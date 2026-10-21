import asyncio, json, sys
from pathlib import Path
from typing import Any
import pytest

from decky_loader.plugin.sandboxed_plugin import SandboxedPlugin


def test_stop_message_runs_unload(monkeypatch: pytest.MonkeyPatch):
    from decky_loader.plugin import sandboxed_plugin
    monkeypatch.setattr(sandboxed_plugin, "ON_WINDOWS", True)
    called: list[bool] = []

    class FakePlugin:
        async def _unload(self) -> None:
            called.append(True)

    sp = SandboxedPlugin("p", False, [], "main.py", "p", ".", None, "me", 1)
    sp.Plugin = FakePlugin() # pyright: ignore [reportAttributeAccessIssue]

    async def run() -> Any:
        res = await sp.on_new_message(json.dumps({"stop": True}))
        await asyncio.sleep(0.2) # the shutdown task runs and exits the loop
        return res

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(SystemExit):
            loop.run_until_complete(run())
    finally:
        loop.close()
    assert called == [True]


def test_stop_message_is_windows_only(monkeypatch: pytest.MonkeyPatch):
    from decky_loader.plugin import sandboxed_plugin
    monkeypatch.setattr(sandboxed_plugin, "ON_WINDOWS", False)
    shutdowns: list[bool] = []
    sp = SandboxedPlugin("p", False, [], "main.py", "p", ".", None, "me", 1)

    async def shutdown(uninstall: bool = False) -> None:
        shutdowns.append(True)

    sp.shutdown = shutdown # pyright: ignore [reportAttributeAccessIssue]
    with pytest.raises(KeyError): # elsewhere it is not a control message (Linux uses SIGTERM), as before
        asyncio.run(sp.on_new_message(json.dumps({"stop": True})))
    assert shutdowns == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only stop path")
def test_wrapper_stop_sends_stop_line_and_closes_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from decky_loader.plugin import plugin as plugin_mod

    events: list[str] = []

    class FakeSocket:
        active = True
        async def write_single_line(self, line: str) -> None:
            events.append(line)
        async def close_socket_connection(self) -> None:
            pass

    class FakeProc:
        alive = True
        def is_alive(self) -> bool:
            return self.alive
        def terminate(self) -> None:
            events.append("terminate")
        def kill(self) -> None:
            events.append("kill")
        def close(self) -> None:
            events.append("close")

    class Wrapper(plugin_mod.PluginWrapper):
        async def kill_if_still_running(self) -> None:
            events.append("wait")
            proc: Any = self.proc
            proc.alive = False

    monkeypatch.setattr(plugin_mod, "get_homebrew_path", lambda: str(tmp_path / "home"))
    pdir = tmp_path / "plugins" / "P"
    pdir.mkdir(parents=True)
    (pdir / "plugin.json").write_text(json.dumps({"name": "P", "author": "me", "flags": [], "api_version": 1}))
    (pdir / "main.py").write_text("class Plugin: pass\n")

    async def noop(*_: Any) -> None:
        pass

    async def run(failing: bool) -> None:
        events.clear()
        w = Wrapper(str(pdir / "main.py"), "P", str(tmp_path / "plugins"), noop)
        sock: Any = FakeSocket()
        if failing:
            async def boom(line: str) -> None:
                raise ConnectionResetError("gone")
            sock.write_single_line = boom
        w._socket = sock # pyright: ignore [reportPrivateUsage, reportAttributeAccessIssue]
        w.proc = FakeProc() # pyright: ignore [reportAttributeAccessIssue]
        await w.stop(uninstall=True)

    asyncio.run(run(False))
    assert [json.loads(e) if e.startswith("{") else e for e in events] == [{"uninstall": True}, {"stop": True}, "wait", "close"]

    asyncio.run(run(True)) # a dead connection must not stop the shutdown from finishing
    assert events == ["wait", "close"]

    async def run_dead() -> None:
        events.clear()
        w = Wrapper(str(pdir / "main.py"), "P", str(tmp_path / "plugins"), noop)
        w._socket = FakeSocket() # pyright: ignore [reportPrivateUsage, reportAttributeAccessIssue]
        proc = FakeProc()
        proc.alive = False
        w.proc = proc # pyright: ignore [reportAttributeAccessIssue]
        await w.stop()

    asyncio.run(run_dead()) # a host that already exited gets no stop request (it could only stall on the socket)
    assert events == ["wait", "close"]
