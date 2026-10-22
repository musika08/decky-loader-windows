'''Windows supervisor: keeps the loader running, restarts it on request and swaps in self-updates.

The supervisor runs the loader as a child process (`--loader`). The child reports what it wants through its exit code.
'''
import hashlib, logging, os, shutil, subprocess, sys, threading, time
from typing import Any, Callable, cast

logger = logging.getLogger("winsupervisor")

EXIT_RESTART: int = 42
EXIT_STOP: int = 43

BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
HEALTHY_RUN_SECONDS = 60.0

SYNCHRONIZE = 0x00100000
PARENT_WAIT_MS = 30000
COPY_ATTEMPTS = 30
CLEANUP_SECONDS = 60

# A onefile PyInstaller bootloader reuses its parent's extraction directory unless told otherwise.
# Every process we start must own its directory, since the starter exits (or is replaced) before it does.
# A onedir build extracts nothing and runs the same way with or without it.
_FRESH_ENV = {"PYINSTALLER_RESET_ENVIRONMENT": "1"}

ATTACH_PARENT_PROCESS = -1

# PyInstaller's default onedir contents directory (EXE(contents_directory=...), "_internal" since PyInstaller 6)
ONEDIR_CONTENTS = "_internal"

# Same as the loader's (main.py LOG_FORMAT)
LOG_FORMAT = "[%(module)s][%(levelname)s]: %(message)s"
SUPERVISOR_LOG = "supervisor.log"

def _setup_file_logging(filename: str, fmt: str) -> str | None:
    from .winlogging import setup_file_logging
    return setup_file_logging(filename, fmt)

def _log_level() -> int:
    from .localplatform import get_log_level
    return get_log_level()

def setup_logging(basic_config: Callable[..., None] = logging.basicConfig, log_level: Callable[[], int] = _log_level,
                  file_logging: Callable[[str, str], str | None] = _setup_file_logging) -> str | None:
    '''Log like the loader does: to the console, if there is one, and to supervisor.log next to the loader's log.'''
    try:
        level, invalid = log_level(), False
    except KeyError:
        # An invalid LOG_LEVEL must not stop the supervisor (the loader still fails on it and gets respawned)
        level, invalid = logging.INFO, True
    basic_config(level=level, format=LOG_FORMAT)
    if invalid:
        logger.warning("Invalid LOG_LEVEL %r, using INFO", os.getenv("LOG_LEVEL"))
    path = file_logging(SUPERVISOR_LOG, LOG_FORMAT)
    if path:
        logger.info("Logging to %s", path)
    return path

def is_onedir_build() -> bool:
    r'''True for a PyInstaller onedir build: frozen, with `sys._MEIPASS` being the `_internal` folder next to the exe.

    A onefile build points `sys._MEIPASS` at its extraction directory in %TEMP% instead.'''
    if not getattr(sys, "frozen", False):
        return False
    meipass = getattr(sys, "_MEIPASS", None)
    if not isinstance(meipass, str) or not meipass:
        return False
    def norm(p: str) -> str:
        return os.path.normcase(os.path.realpath(p))
    return norm(meipass) == norm(os.path.join(os.path.dirname(sys.executable), ONEDIR_CONTENTS))

def _parent_pid(pid: int) -> int | None:
    from .wininstall import parent_pid
    return parent_pid(pid)

def attach_parent_console(kernel32: Any = None, opener: Callable[[str, str], Any] = open,
                          ppid: Callable[[], int] = os.getppid, parent_of: Callable[[int], int | None] = _parent_pid,
                          onedir: Callable[[], bool] = is_onedir_build) -> bool:
    '''In a windowed (-noconsole) build sys.stdout/sys.stderr are None, so CLI modes would print nothing.
    Attach to the console of whoever started us and write there. If there is none, output goes nowhere.

    In a onefile build our code runs in the bootloader's child, and a windowed bootloader has no console, so the
    console to attach to is the bootloader's parent's (the terminal the user typed into). A onedir build has no
    bootloader process, so there the terminal is our direct parent. Fall back to our direct parent's console when
    that fails or we are not a onefile build.'''
    if sys.stdout is not None:
        return False
    try:
        if kernel32 is None:
            import ctypes
            kernel32 = ctypes.windll.kernel32 # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        attached = False
        if getattr(sys, "frozen", False) and not onedir():
            try:
                launcher = parent_of(ppid())
            except OSError:
                launcher = None
            if launcher:
                attached = bool(kernel32.AttachConsole(launcher))
        if not attached and not kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            return False
        out, err = opener("CONOUT$", "w"), opener("CONOUT$", "w")
    except OSError:
        return False
    sys.stdout, sys.stderr = out, err
    return True

def self_command(*, executable: str | None = None, frozen: bool | None = None, package_file: str = __file__,
                 our_runtime: bool | None = None) -> list[str]:
    r'''Command that starts another copy of this program, without mode flags.

    A PyInstaller build is just its exe. From source, it is this interpreter running the main.py next to the
    decky_loader package; on our own runtime (installed, or an installer folder) with -I, so PYTHON* environment
    variables are ignored: `<dir>\python\pythonw.exe -I <dir>\app\main.py` when installed.'''
    from .wininstall import self_invocation
    executable = executable or sys.executable
    if getattr(sys, "frozen", False) if frozen is None else frozen:
        return [executable]
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(package_file)))), "main.py")
    return self_invocation(executable, main_py, our_runtime=our_runtime)

def child_env(env: dict[str, str]) -> dict[str, str]:
    '''`env` without any PYTHON* variable, for the processes we start (loader, plugin hosts, update finisher). They run
    with -I on our runtime anyway; this also keeps them out of anything those processes start.'''
    return {k: v for k, v in env.items() if not k.upper().startswith("PYTHON")}

# Next to main.py in an installer folder (tools\build_win_runtime.py writes it): the values PyInstaller's runtime
# hook (build_config.py, written by pyinstaller.spec) sets in a packaged build
BUILD_CONFIG = "build_config.json"
BUILD_CONFIG_KEYS = ("DECKY_PACKAGED_BUILD", "DECKY_REPO", "DECKY_BRANCH", "DECKY_NOCONSOLE")

def apply_build_config(path: str, environ: Any = os.environ) -> dict[str, str]:
    '''Set the BUILD_CONFIG_KEYS found in the JSON object at `path` in `environ`, overriding like the runtime hook
    does. A missing file (running from a checkout) changes nothing; a broken one is logged and ignored.'''
    import json
    try:
        with open(path, encoding="utf-8") as f:
            data: Any = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning("Ignoring %s: %s", path, e)
        return {}
    applied: dict[str, str] = {}
    if isinstance(data, dict):
        values = cast(dict[str, Any], data)
        for key in BUILD_CONFIG_KEYS:
            value = values.get(key)
            if isinstance(value, str):
                environ[key] = applied[key] = value
    return applied

def next_backoff(prev: float, ran_for: float) -> float:
    if ran_for >= HEALTHY_RUN_SECONDS or prev <= 0:
        return BACKOFF_START
    return min(prev * 2, BACKOFF_MAX)

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def _identical(a: str, b: str) -> bool:
    try:
        return os.path.getsize(a) == os.path.getsize(b) and _sha256(a) == _sha256(b)
    except OSError:
        return False

def pending_update_path(exe: str) -> str | None:
    new = exe + ".new"
    try:
        if os.path.getsize(new) > 0 and not _identical(new, exe):
            return new
    except OSError:
        pass
    return None

def cleanup_stale_update(exe: str, remove: Callable[[str], None] = os.remove, sleep: Callable[[float], None] = time.sleep) -> threading.Thread | None:
    '''Remove a leftover `<exe>.new` from a finished update.

    The finisher may still be running from that file, so deletion is retried in a background thread.
    '''
    new = exe + ".new"
    if not os.path.exists(new) or os.path.normcase(os.path.abspath(new)) == os.path.normcase(os.path.abspath(sys.executable)):
        return None
    if pending_update_path(exe) is not None:
        return None # A genuinely different pending update, keep it

    def work() -> None:
        for _ in range(CLEANUP_SECONDS):
            try:
                remove(new)
                return
            except FileNotFoundError:
                return
            except OSError:
                sleep(1.0)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread

def _run_in_job(argv: list[str], env: dict[str, str] | None, cwd: str | None) -> int:
    from .winprocess import run_in_job
    return run_in_job(argv, env, cwd)

def make_spawn(exe: str, run: Callable[[list[str], dict[str, str] | None, str | None], int] = _run_in_job) -> Callable[[list[str]], int]:
    '''Blocking spawn of the loader child; returns its exit code.

    The child runs in a kill-on-close job held by this process, so if the supervisor dies or is ended, the loader and
    its plugin hosts go with it.'''
    def spawn(argv: list[str]) -> int:
        env = child_env({**os.environ, "DECKY_SUPERVISED": "1", **_FRESH_ENV})
        cwd = os.path.dirname(exe) if getattr(sys, "frozen", False) else None
        return run(argv, env, cwd)
    return spawn

def launch_detached(argv: list[str]) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    subprocess.Popen(argv, creationflags=flags, close_fds=True, cwd=os.path.dirname(argv[0]) or None,
                     env=child_env({**os.environ, **_FRESH_ENV}))

SUPERVISOR_MUTEX = "Local\\DeckyLoader-Supervisor"
ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183

# Never closed: the system closes it when this process exits, which is what releases the lock.
_supervisor_lock: Any = None

def _mutex_kernel32() -> Any:
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateMutexW.restype = ctypes.c_void_p
    return k

def _last_error() -> int:
    import ctypes
    return ctypes.get_last_error()

def _clear_last_error() -> None:
    import ctypes
    ctypes.set_last_error(0)

def acquire_supervisor_lock(kernel32: Any = None, last_error: Callable[[], int] = _last_error,
                            clear_last_error: Callable[[], None] = _clear_last_error,
                            name: str = SUPERVISOR_MUTEX) -> bool:
    '''Make this the only supervisor in the session. False means another one already holds the lock.

    The mutex handle is not inheritable (no SECURITY_ATTRIBUTES), so the loader and the update finisher never hold
    it; it is released exactly when this process exits. If the mutex cannot be created for any other reason, the
    supervisor starts anyway rather than leaving Decky stopped.'''
    global _supervisor_lock
    k = kernel32 if kernel32 is not None else _mutex_kernel32()
    clear_last_error()
    handle = k.CreateMutexW(None, False, name)
    err = last_error()
    if handle and err == ERROR_ALREADY_EXISTS:
        logger.warning("Another Decky Loader supervisor is already running in this session, exiting")
        return False
    if not handle:
        if err == ERROR_ACCESS_DENIED:
            # It exists, but was created by a process we cannot open (e.g. the elevated installed supervisor)
            logger.warning("Another Decky Loader supervisor is already running in this session, exiting")
            return False
        logger.warning("Could not create the %s mutex (error %s), starting without the single-instance check", name, err)
        return True
    _supervisor_lock = handle
    return True

def run_supervisor(spawn: Callable[[list[str]], int], sleep: Callable[[float], None], exe: str,
                   launch_detached: Callable[[list[str]], None], clock: Callable[[], float] = time.monotonic,
                   self_update: bool | None = None) -> int:
    '''`self_update`: swap in a downloaded `<exe>.new` on restart. Only a PyInstaller exe updates itself (the default);
    from source `exe` is the Python interpreter, which must never be replaced.'''
    if self_update is None:
        self_update = bool(getattr(sys, "frozen", False))
    backoff = 0.0
    while True:
        started = clock()
        code = spawn(self_command() + ["--loader"])
        ran_for = clock() - started
        logger.info("The loader exited with code %s after %.0f seconds", code, ran_for)

        # A plain 0 is a clean shutdown the loader did not ask us to undo (e.g. Ctrl+C), so do not respawn.
        if code == EXIT_STOP or code == 0:
            return 0
        if code == EXIT_RESTART:
            backoff = 0.0
            new = pending_update_path(exe) if self_update else None
            if new is not None:
                # The finisher outlives us, so it must not be a child we wait on.
                # Under a onefile build our own pid is the child; the bootloader parent is what holds the image lock.
                # A onedir build has no bootloader process.
                waiting_on = os.getppid() if getattr(sys, "frozen", False) and not is_onedir_build() else os.getpid()
                try:
                    launch_detached([new, "--finish-update", exe, str(waiting_on)])
                    return 0
                except OSError:
                    logger.exception("Could not start the update finisher, discarding the pending update")
                    try:
                        os.remove(new)
                    except OSError:
                        pass
                    continue
            continue

        backoff = next_backoff(backoff, ran_for)
        sleep(backoff)

def _wait_for_pid(pid: int) -> None:
    import _winapi # pyright: ignore [reportMissingImports]
    try:
        handle = _winapi.OpenProcess(SYNCHRONIZE, False, pid)
    except OSError:
        return # Already exited
    try:
        _winapi.WaitForSingleObject(handle, PARENT_WAIT_MS)
    finally:
        _winapi.CloseHandle(handle)

def _is_installed() -> bool:
    from .localplatformwin import installed_root
    return installed_root() is not None

def _system_directory() -> str:
    from .wininstall import system_directory
    return system_directory()

CREATE_NO_WINDOW = 0x08000000
RELAUNCH_WAIT_SECONDS = 10.0
RELAUNCH_POLL_SECONDS = 0.25

def image_running(target: str) -> bool:
    '''True if a process other than this one (and its bootloader parent) is running from `target`.'''
    from .wininstall import list_processes, matching_pids
    try:
        return bool(matching_pids(list_processes(), target, own={os.getpid(), os.getppid()}))
    except OSError:
        logger.exception("Could not list running processes")
        return False

def relaunch(target: str, *, installed: Callable[[], bool] = _is_installed, run: Callable[..., Any] = subprocess.run,
             direct: Callable[[list[str]], None] = launch_detached, system_dir: Callable[[], str] = _system_directory,
             running: Callable[[str], bool] = image_running, sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic) -> None:
    '''Start the loader again after an update.

    Installed: through the scheduled task, so it keeps the task's elevation and tracking.
    The task can accept the request and still not start the exe, so wait up to RELAUNCH_WAIT_SECONDS for `target`
    to show up as a process.
    Portable, or if the task cannot be started or does not start `target`: a direct detached launch.'''
    if installed():
        from .wininstall import TASK_NAME
        try:
            run([os.path.join(system_dir(), "schtasks.exe"), "/Run", "/TN", TASK_NAME], check=True, capture_output=True,
                creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            logger.exception("Could not start the %s task, launching %s directly", TASK_NAME, target)
        else:
            deadline = clock() + RELAUNCH_WAIT_SECONDS
            while True:
                if running(target):
                    return
                if clock() >= deadline:
                    break
                sleep(RELAUNCH_POLL_SECONDS)
            logger.error("The %s task did not start %s within %s seconds, launching it directly", TASK_NAME, target,
                         RELAUNCH_WAIT_SECONDS)
    direct([target])

def _launch_target(target: str) -> None:
    relaunch(target)

def finish_update(target: str, parent_pid: int, copy: Callable[[str, str], object] = shutil.copyfile,
                  launch: Callable[[str], None] = _launch_target, sleep: Callable[[float], None] = time.sleep) -> int:
    '''Runs from the downloaded `.new` binary: wait for the old supervisor to exit, overwrite it, start it again.'''
    _wait_for_pid(parent_pid)

    for attempt in range(COPY_ATTEMPTS):
        try:
            copy(sys.executable, target)
            break
        except OSError:
            if attempt == COPY_ATTEMPTS - 1:
                # Bring the old version back rather than leaving no loader running
                launch(target)
                return 1
            sleep(1.0)

    launch(target)
    return 0
