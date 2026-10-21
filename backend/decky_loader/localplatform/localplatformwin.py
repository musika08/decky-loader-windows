from ..enums import UserType
import os, sys
from aiohttp.web_runner import GracefulExit # pyright: ignore [reportMissingImports, reportUnknownVariableType]
from .winsupervisor import EXIT_RESTART, EXIT_STOP

_exit_code: int | None = None

def request_exit(code: int) -> None:
    '''Ask the loader to shut down cleanly (aiohttp runs on_shutdown) and exit with `code` for the supervisor.'''
    global _exit_code
    _exit_code = code
    raise GracefulExit()

def requested_exit_code() -> int | None:
    return _exit_code

def chown(path : str,  user : UserType = UserType.HOST_USER, recursive : bool = True) -> bool:
    return True # Stubbed

def chmod(path : str, permissions : int, recursive : bool = True) -> bool:
    return True # Stubbed

def file_owner(path : str) -> UserType|None:
    return UserType.HOST_USER # Stubbed

def get_home_path(user : UserType = UserType.HOST_USER) -> str:
    return os.path.expanduser("~") # Mostly stubbed

def setgid(user : UserType = UserType.HOST_USER):
    pass # Stubbed

def setuid(user : UserType = UserType.HOST_USER):
    pass # Stubbed

async def service_active(service_name : str) -> bool:
    return True # Stubbed

async def service_stop(service_name : str) -> bool:
    if service_name == "plugin_loader":
        request_exit(EXIT_STOP)

    return True # Stubbed

async def service_start(service_name : str) -> bool:
    return True # Stubbed

async def service_restart(service_name : str, block : bool = True) -> bool:
    if service_name == "plugin_loader":
        request_exit(EXIT_RESTART)

    return True # Stubbed

def get_effective_username() -> str:
    return os.getlogin()

def get_username() -> str:
    return os.getlogin()

def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))

def installed_root() -> str | None:
    r'''`%ProgramData%\decky` when running from `%ProgramData%\decky\services`, else None.'''
    root = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "decky")
    services = _norm(os.path.join(root, "services"))
    if _norm(sys.executable).startswith(services + os.sep):
        return root
    return None

def get_privileged_path() -> str:
    r'''Installed: the admin-only `%ProgramData%\decky`. Portable: same as unprivileged_path.'''
    path = os.getenv("PRIVILEGED_PATH")
    if path != None:
        os.makedirs(path, exist_ok=True)
        return path

    root = installed_root()
    if root != None:
        return root

    return get_unprivileged_path()

def get_unprivileged_path() -> str:
    path = os.getenv("UNPRIVILEGED_PATH")

    if path == None:
        path = os.getenv("PRIVILEGED_PATH", os.path.join(os.path.expanduser("~"), "homebrew"))

    os.makedirs(path, exist_ok=True)

    return path

def get_unprivileged_user() -> str:
    return os.getenv("UNPRIVILEGED_USER", os.getlogin())

async def restart_webhelper() -> bool:
    return True # Stubbed

async def close_cef_socket():
    return # Stubbed