'''Windows supervisor: keeps the loader running, restarts it on request and swaps in self-updates.

The supervisor runs the loader as a child process (`--loader`). The child reports what it wants through its exit code.
'''
import hashlib, logging, os, shutil, subprocess, sys, threading, time
from typing import Any, Callable

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

# The PyInstaller bootloader reuses its parent's extraction directory unless told otherwise.
# Every process we start must own its directory, since the starter exits (or is replaced) before it does.
_FRESH_ENV = {"PYINSTALLER_RESET_ENVIRONMENT": "1"}

ATTACH_PARENT_PROCESS = -1

def _parent_pid(pid: int) -> int | None:
    from .wininstall import parent_pid
    return parent_pid(pid)

def attach_parent_console(kernel32: Any = None, opener: Callable[[str, str], Any] = open,
                          ppid: Callable[[], int] = os.getppid, parent_of: Callable[[int], int | None] = _parent_pid) -> bool:
    '''In a windowed (-noconsole) build sys.stdout/sys.stderr are None, so CLI modes would print nothing.
    Attach to the console of whoever started us and write there. If there is none, output goes nowhere.

    In a onefile build our code runs in the bootloader's child, and a windowed bootloader has no console, so the
    console to attach to is the bootloader's parent's (the terminal the user typed into). Fall back to our direct
    parent's console when that fails or we are not frozen.'''
    if sys.stdout is not None:
        return False
    try:
        if kernel32 is None:
            import ctypes
            kernel32 = ctypes.windll.kernel32 # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        attached = False
        if getattr(sys, "frozen", False):
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

def self_command() -> list[str]:
    '''Command that starts another copy of this program, without mode flags.'''
    if getattr(sys, "frozen", False):
        return [sys.executable]
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "main.py")
    return [sys.executable, main_py]

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
        env = {**os.environ, "DECKY_SUPERVISED": "1", **_FRESH_ENV}
        cwd = os.path.dirname(exe) if getattr(sys, "frozen", False) else None
        return run(argv, env, cwd)
    return spawn

def launch_detached(argv: list[str]) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    subprocess.Popen(argv, creationflags=flags, close_fds=True, cwd=os.path.dirname(argv[0]) or None,
                     env={**os.environ, **_FRESH_ENV})

def run_supervisor(spawn: Callable[[list[str]], int], sleep: Callable[[float], None], exe: str,
                   launch_detached: Callable[[list[str]], None], clock: Callable[[], float] = time.monotonic) -> int:
    backoff = 0.0
    while True:
        started = clock()
        code = spawn(self_command() + ["--loader"])
        ran_for = clock() - started

        # A plain 0 is a clean shutdown the loader did not ask us to undo (e.g. Ctrl+C), so do not respawn.
        if code == EXIT_STOP or code == 0:
            return 0
        if code == EXIT_RESTART:
            backoff = 0.0
            new = pending_update_path(exe)
            if new is not None:
                # The finisher outlives us, so it must not be a child we wait on.
                # Under a onefile build our own pid is the child; the bootloader parent is what holds the image lock.
                waiting_on = os.getppid() if getattr(sys, "frozen", False) else os.getpid()
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

def relaunch(target: str, *, installed: Callable[[], bool] = _is_installed, run: Callable[..., Any] = subprocess.run,
             direct: Callable[[list[str]], None] = launch_detached, system_dir: Callable[[], str] = _system_directory) -> None:
    '''Start the loader again after an update.

    Installed: through the scheduled task, so it keeps the task's elevation, admin-only temp directory and tracking.
    Portable, or if the task cannot be started: a direct detached launch.'''
    if installed():
        from .wininstall import TASK_NAME
        try:
            run([os.path.join(system_dir(), "schtasks.exe"), "/Run", "/TN", TASK_NAME], check=True, capture_output=True)
            return
        except (OSError, subprocess.SubprocessError):
            logger.exception("Could not start the %s task, launching %s directly", TASK_NAME, target)
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
