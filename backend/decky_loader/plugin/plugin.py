from asyncio import CancelledError, Task, create_task, sleep, wait, wait_for
from json import dumps, load, loads
from logging import getLogger
import logging
import os
import signal
import sys
from os import environ, path
from multiprocessing import Process
from time import time
from traceback import format_exc

from .sandboxed_plugin import SandboxedPlugin
from .messages import MethodCallRequest, SocketMessageType
from ..enums import PluginLoadType, UserType
from ..localplatform.localplatform import file_owner, chown, chmod, get_chown_plugin_path, ON_WINDOWS
from ..localplatform.localsocket import LocalSocket, PortSocket
from ..helpers import get_homebrew_path, mkdir_as_user

from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict, List

if TYPE_CHECKING:
    from ..localplatform.winprocess import PluginProcess

EmittedEventCallbackType = Callable[[str, Any], Coroutine[Any, Any, Any]]

# How long an update waits for the new plugin backend before rolling back. On Windows each refused connect to the
# not-yet-listening plugin host takes about 2 s plus PortSocket's 2 s retry delay, and the host is a fresh
# PluginLoader.exe, so 20 s would roll back plugins that start fine.
STARTUP_TIMEOUT = 60 if ON_WINDOWS else 20

_held_locks: List[Any] = []

def _before_fork():
    for ref in list(getattr(logging, "_handlerList", [])):
        lock = getattr(ref(), "lock", None)
        if lock is not None and lock.acquire(timeout=0.5):
            _held_locks.append(lock)

def _after_fork_in_parent():
    while _held_locks:
        _held_locks.pop().release()

def _after_fork_in_child():
    _held_locks.clear()
    signal.set_wakeup_fd(-1)
    for sig in (signal.SIGTERM, signal.SIGINT):
        if signal.getsignal(sig) != signal.SIG_IGN:
            signal.signal(sig, signal.SIG_DFL)

if sys.platform != "win32": # no fork on Windows, and no os.register_at_fork
    os.register_at_fork(before=_before_fork, after_in_parent=_after_fork_in_parent, after_in_child=_after_fork_in_child)

class PluginWrapper:
    def __init__(self, file: str, plugin_directory: str, plugin_path: str, emit_callback: EmittedEventCallbackType) -> None:
        self.file = file
        self.plugin_path = plugin_path
        self.plugin_directory = plugin_directory

        self.version = None

        self.load_type = PluginLoadType.LEGACY_EVAL_IIFE.value

        plugin_dir_path = path.join(plugin_path, plugin_directory)
        plugin_json_path = path.join(plugin_dir_path, "plugin.json")

        json = load(open(plugin_json_path, "r", encoding="utf-8"))
        if path.isfile(path.join(plugin_dir_path, "package.json")):
            package_json = load(open(path.join(plugin_dir_path, "package.json"), "r", encoding="utf-8"))
            self.version = package_json["version"]
            if ("type" in package_json and package_json["type"] == "module"):
                self.load_type = PluginLoadType.ESMODULE_V1.value

        self.name = json["name"]
        self.author = json["author"]
        self.flags = json["flags"]
        self.api_version = json["api_version"] if "api_version" in json else 0
        self.disabled = False
        
        self.passive = not path.isfile(self.file)

        self.log = getLogger("plugin")

        if get_chown_plugin_path():
            # ensure plugin folder ownership
            if file_owner(plugin_dir_path) != UserType.EFFECTIVE_USER:
                chown(plugin_dir_path, UserType.EFFECTIVE_USER if "root" in self.flags else UserType.HOST_USER, True)
                chown(plugin_dir_path, UserType.EFFECTIVE_USER, False)
                chmod(plugin_dir_path, 755, True)
            # fix plugin.json permissions
            if file_owner(plugin_json_path) != UserType.EFFECTIVE_USER:
                chown(plugin_json_path, UserType.EFFECTIVE_USER, False)
                chmod(plugin_json_path, 755, False)

        self.sandboxed_plugin = SandboxedPlugin(self.name, self.passive, self.flags, self.file, self.plugin_directory, self.plugin_path, self.version, self.author, self.api_version)
        self.proc: "Process | PluginProcess | None" = None
        self._socket = LocalSocket()
        self._listener_task: Task[Any]
        self._method_call_requests: Dict[str, MethodCallRequest] = {}

        self.emitted_event_callback: EmittedEventCallbackType = emit_callback

        # TODO enable this after websocket release
        self.legacy_method_warning = False

        home = get_homebrew_path()
        mkdir_as_user(path.join(home, "settings", self.plugin_directory))
        # TODO maybe dont chown this?
        mkdir_as_user(path.join(home, "data"))
        mkdir_as_user(path.join(home, "data", self.plugin_directory))
        # TODO maybe dont chown this?
        mkdir_as_user(path.join(home, "logs"))
        mkdir_as_user(path.join(home, "logs", self.plugin_directory))

    def __str__(self) -> str:
        return self.name
    
    def get_display_name(self) -> str:
        """Returns plugin name with version if available, formatted for logging."""
        if self.version:
            return f"{self.name} (v{self.version})"

        return self.name

    async def _response_listener(self):
        while self._socket.active:
            try:
                line = await self._socket.read_single_line()
                # EOF reads as None while the connection is still held ("" if the socket was deactivated);
                # None with no reader means connecting failed, which the next iteration retries.
                windows_eof = ON_WINDOWS and (line == "" or (line is None and self._socket.reader is not None))
                exited = not windows_eof and line is None and self.proc is not None and not self.proc.is_alive()
                if windows_eof or exited:
                    if windows_eof:
                        # EOF: the plugin host closed the connection or died. Reading again would spin.
                        self.log.info(f"Plugin {self.get_display_name()} closed its socket")
                    else:
                        self.log.error(f"{self.get_display_name()} exited unexpectedly (exit code {getattr(self.proc, 'exitcode', None)})")
                    await self._socket.close_socket_connection()
                    for request in self._method_call_requests.values():
                        request.cancel()
                    self._method_call_requests = {}
                    return
                if line is None:
                    await sleep(0.5)
                if line != None:
                    res = loads(line)
                    if res["type"] == SocketMessageType.EVENT.value:
                        create_task(self.emitted_event_callback(res["event"], res["args"]))
                    elif res["type"] == SocketMessageType.RESPONSE.value:
                        self._method_call_requests.pop(res["id"]).set_result(res)
            except CancelledError:
                self.log.info(f"Stopping response listener for {self.get_display_name()}")
                await self._socket.close_socket_connection()

                # Cancel the request so that WSRouter can perform cleanup
                for request in self._method_call_requests.values():
                    request.cancel()
                
                self._method_call_requests = {}
                raise
            except:
                pass

    async def execute_legacy_method(self, method_name: str, kwargs: Dict[Any, Any]):
        if not self.legacy_method_warning:
            self.legacy_method_warning = True
            self.log.warning(f"Plugin {self.get_display_name()} is using legacy method calls. This will be removed in a future release.")
        if self.passive:
            raise RuntimeError("This plugin is passive (aka does not implement main.py)")
        
        request = MethodCallRequest()
        await self._socket.get_socket_connection()
        await self._socket.write_single_line(dumps({ "type": SocketMessageType.CALL, "method": method_name, "args": kwargs, "id": request.id, "legacy": True }, ensure_ascii=False))
        self._method_call_requests[request.id] = request

        return await request.wait_for_result()

    async def execute_method(self, method_name: str, *args: List[Any]):
        if self.passive:
            raise RuntimeError("This plugin is passive (aka does not implement main.py)")
        
        request = MethodCallRequest()
        await self._socket.get_socket_connection()
        await self._socket.write_single_line(dumps({ "type": SocketMessageType.CALL, "method": method_name, "args": args, "id": request.id }, ensure_ascii=False))
        self._method_call_requests[request.id] = request

        return await request.wait_for_result()
    
    def start(self):
        if self.passive:
            return self
        if ON_WINDOWS:
            self.proc = self._start_windows()
        else:
            self.proc = Process(target=self.sandboxed_plugin.initialize, args=[self._socket])
            self.proc.start()
        self._listener_task = create_task(self._response_listener())
        return self

    def _start_windows(self) -> "PluginProcess":
        '''Start the plugin host as its own program: elevated for root plugins, restricted otherwise.'''
        from ..localplatform import winprocess
        from ..localplatform.winsupervisor import child_env, self_command
        from .plugin_host import TOKEN_ENV, encode_params

        sock = self._socket
        assert isinstance(sock, PortSocket)
        elevated = "root" in self.flags
        if elevated and not winprocess.is_elevated():
            self.log.warning(f"Plugin {self.get_display_name()} requests root, but the loader is unelevated. Starting it with the loader's privileges.")
        env = child_env(dict(environ)) # No PYTHON* variables: the host runs on our runtime with -I anyway
        env[TOKEN_ENV] = sock.token
        # A onefile child must own its PyInstaller extraction directory, since it may outlive this loader.
        # A onedir build extracts nothing and ignores it.
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        proc = winprocess.PluginProcess(self_command() + ["--plugin-host", encode_params(self.sandboxed_plugin, sock.port)], env, elevated)
        proc.start()
        return proc

    async def wait_started(self):
        """Wait for backend initialization and its IPC server, bounded to STARTUP_TIMEOUT seconds."""
        if self.passive or self.disabled:
            return

        deadline = time() + STARTUP_TIMEOUT

        while time() < deadline:
            if self.proc is None or not self.proc.is_alive():
                raise RuntimeError(f"Plugin {self.name} exited during startup")

            # The response listener opens the connection once initialization completes.
            if self._socket.writer is not None:
                return
            await sleep(0.1)

        raise TimeoutError(f"Plugin {self.name} did not initialize within {STARTUP_TIMEOUT} seconds")

    async def stop(self, uninstall: bool = False):
        try:
            start_time = time()
            if self.passive:
                return
            self.log.info(f"Shutting down {self.get_display_name()}")

            pending: set[Task[None]] | None = None;

            if uninstall:
                _, pending = await wait([
                    create_task(self._socket.write_single_line(dumps({ "uninstall": uninstall }, ensure_ascii=False)))
                ], timeout=1)

            if ON_WINDOWS:
                # no SIGTERM on Windows: ask over the socket, kill_if_still_running is the backstop.
                # A host that already exited cannot answer, and writing to it would only stall on the socket.
                if self.proc and self.proc.is_alive():
                    await self._request_stop()
            else:
                self.terminate() # the plugin process will handle SIGTERM and shut down cleanly without a socket message

            if hasattr(self, "_listener_task"):
                self._listener_task.cancel()
            
            await self.kill_if_still_running()

            if ON_WINDOWS and self.proc:
                self.proc.close() # the process is gone; release the job now rather than at garbage collection

            if pending:
                for pending_task in pending:
                    pending_task.cancel()

            self.log.info(f"Plugin {self.get_display_name()} has been stopped in {time() - start_time:.1f}s")
        except Exception as e:
            self.log.error(f"Error during shutdown for plugin {self.get_display_name()}: {str(e)}\n{format_exc()}")

    async def _request_stop(self):
        try:
            await wait_for(self._socket.write_single_line(dumps({ "stop": True }, ensure_ascii=False)), timeout=2)
        except Exception as e:
            self.log.warning(f"Could not send the stop request to {self.get_display_name()}: {e!r}")

    async def kill_if_still_running(self):
        start_time = time()
        while self.proc and self.proc.is_alive():
            elapsed_time = time() - start_time
            if elapsed_time >= 5:
                self.log.warning(f"Plugin {self.get_display_name()} still alive 5 seconds after stop request! Sending SIGKILL!")
                self.terminate(True)
            await sleep(0.1)


    def terminate(self, kill: bool = False):
        if self.proc and self.proc.is_alive():
            if kill:
                self.proc.kill()
            else:
                self.proc.terminate()