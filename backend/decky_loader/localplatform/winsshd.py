r'''Windows counterpart of starting/stopping `sshd.service`: the OpenSSH Server Windows service (`sshd`), only if it
is already installed. Nothing is installed or reconfigured. Starting or stopping a service needs administrator rights,
so it works in installed mode only.
'''
import asyncio, os, subprocess
from logging import getLogger
from typing import Callable

logger = getLogger("winsshd")

UNIT = "sshd.service"
SERVICE = "sshd"

ERROR_ACCESS_DENIED = 5
ERROR_SERVICE_ALREADY_RUNNING = 1056
ERROR_SERVICE_DISABLED = 1058
ERROR_SERVICE_DOES_NOT_EXIST = 1060
ERROR_SERVICE_NOT_ACTIVE = 1062

NOT_INSTALLED = "OpenSSH Server is not installed. Add it in Windows Settings > System > Optional features, then try again."
NEEDS_ADMIN = "Starting or stopping OpenSSH Server needs administrator rights (Decky's installed mode)."
DISABLED = "The OpenSSH Server service (sshd) is disabled. Set its startup type to Manual or Automatic in Services."

def _sc_exe() -> str:
    from .wininstall import system_directory
    return os.path.join(system_directory(), "sc.exe")

def _run_sc(args: list[str]) -> int:
    # sc.exe from System32 (resolved by the OS, not PATH); no console window in windowed builds
    return subprocess.run([_sc_exe()] + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          creationflags=subprocess.CREATE_NO_WINDOW, check=False).returncode # pyright: ignore [reportAttributeAccessIssue]

async def set_sshd_running(running: bool, *, run: Callable[[list[str]], int] = _run_sc) -> str | None:
    '''Start or stop the sshd service. None on success (or if it already was in that state), else why not.'''
    def go() -> str | None:
        if run(["query", SERVICE]) == ERROR_SERVICE_DOES_NOT_EXIST:
            return NOT_INSTALLED
        code = run(["start" if running else "stop", SERVICE])
        if code == 0 or code == (ERROR_SERVICE_ALREADY_RUNNING if running else ERROR_SERVICE_NOT_ACTIVE):
            return None
        if code == ERROR_ACCESS_DENIED:
            return NEEDS_ADMIN
        if code == ERROR_SERVICE_DISABLED:
            return DISABLED
        return f"Could not {'start' if running else 'stop'} the OpenSSH Server service (sc.exe exit code {code})."
    try:
        error = await asyncio.to_thread(go)
    except OSError as e:
        error = f"Could not run sc.exe: {e}"
    if error is None:
        logger.info(f"OpenSSH Server {'started' if running else 'stopped'}")
    else:
        logger.warning(error)
    return error
