'''Windows supervisor: keeps the loader running, restarts it on request and swaps in self-updates.

The supervisor runs the loader as a child process (`--loader`). The child reports what it wants through its exit code.
'''
import os, shutil, subprocess, sys, time
from typing import Callable

EXIT_RESTART: int = 42
EXIT_STOP: int = 43

BACKOFF_START = 1.0
BACKOFF_MAX = 30.0
HEALTHY_RUN_SECONDS = 60.0

SYNCHRONIZE = 0x00100000
PARENT_WAIT_MS = 30000
COPY_ATTEMPTS = 5

# The PyInstaller bootloader reuses its parent's extraction directory unless told otherwise.
# Every process we start must own its directory, since the starter exits (or is replaced) before it does.
_FRESH_ENV = {"PYINSTALLER_RESET_ENVIRONMENT": "1"}

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

def pending_update_path(exe: str) -> str | None:
    new = exe + ".new"
    try:
        if os.path.getsize(new) > 0:
            return new
    except OSError:
        pass
    return None

def cleanup_stale_update(exe: str) -> None:
    '''Best effort removal of a leftover `<exe>.new` from a finished update.'''
    new = exe + ".new"
    if os.path.normcase(os.path.abspath(new)) == os.path.normcase(os.path.abspath(sys.executable)):
        return
    try:
        os.remove(new)
    except OSError:
        pass

def make_spawn(exe: str) -> Callable[[list[str]], int]:
    '''Blocking spawn of the loader child; returns its exit code.'''
    def spawn(argv: list[str]) -> int:
        env = {**os.environ, "DECKY_SUPERVISED": "1", **_FRESH_ENV}
        cwd = os.path.dirname(exe) if getattr(sys, "frozen", False) else None
        return subprocess.run(argv, cwd=cwd, env=env).returncode
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
                launch_detached([new, "--finish-update", exe, str(os.getpid())])
                return 0
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

def _launch_target(target: str) -> None:
    launch_detached([target])

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
                return 1
            sleep(1.0)

    launch(target)
    return 0
