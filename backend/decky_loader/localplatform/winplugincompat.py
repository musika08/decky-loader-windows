r"""Small Linux compatibility shims for plugin processes on Windows.

Plugins are written for SteamOS and often fail on import or on their first file write:
`import fcntl` / `import pwd` raise ImportError, `os.getuid` is missing, logs go to `/tmp/...`
(which resolves to `<current drive>\tmp`) and paths like `/home/deck/...` are hardcoded.
This module adds what can be faked cheaply inside the plugin process:

- `fcntl` with flock/lockf on top of msvcrt.locking (exclusive only: LOCK_SH locks exclusively)
- `pwd` / `grp` describing the current user, and os.getuid & co. (0 elevated, else 1000)
- unelevated hosts only: cwd on the system drive, `<SystemDrive>\tmp`, and
  `<SystemDrive>\home\<user>` and `\home\deck` junctions to the user profile
- a bin dir on PATH with `xdg-open.cmd` and a no-op `chmod.cmd`

Not shimmed: /proc, /sys, /dev, systemctl, dbus, flatpak, pty/termios.
Root plugins must not use /tmp (use DECKY_PLUGIN_RUNTIME_DIR): <SystemDrive>\tmp may be user-owned.
Everything is best effort; a failing shim is logged and never stops a plugin from loading.
"""
import errno
import msvcrt
import os
import stat
import sys
import threading
import time
import types
import _winapi  # pyright: ignore [reportMissingImports]
from logging import getLogger
from typing import Any, Callable, MutableMapping, NamedTuple

_log = getLogger("winplugincompat")

ROOT_UID = 0
USER_UID = 1000

SHIM_BIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "winbin")

# --- fcntl -----------------------------------------------------------------------------

LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8
F_DUPFD, F_GETFD, F_SETFD, F_GETFL, F_SETFL, F_GETLK, F_SETLK, F_SETLKW = range(8)
FD_CLOEXEC = 1

# Windows byte-range locks are mandatory: other handles can't read or write locked bytes.
# Lock one byte far past any real lock file's end so the file itself stays readable.
_LOCK_OFFSET = 0x7FFFFFFE
_RETRY_DELAY = 0.05
# Seek, lock, seek back must not interleave between threads sharing an fd
_seek_lock = threading.Lock()


def _fileno(f: Any) -> int:
    if isinstance(f, int):
        return f
    fileno = getattr(f, "fileno", None)
    if fileno is None:
        raise TypeError("argument must be an int, or have a fileno() method.")
    return int(fileno())


def _locking(fd: int, mode: int) -> None:
    with _seek_lock:
        pos = os.lseek(fd, 0, os.SEEK_CUR)
        try:
            os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, mode, 1)
        finally:
            os.lseek(fd, pos, os.SEEK_SET)


def _try(fd: int, mode: int) -> bool:
    try:
        _locking(fd, mode)
        return True
    except OSError as e:
        ok = (errno.EACCES, errno.EDEADLOCK) + ((errno.EINVAL,) if mode == msvcrt.LK_UNLCK else ())
        if e.errno not in ok:
            raise
        return False


def _lock(f: Any, operation: int) -> None:
    fd = _fileno(f)
    if operation & LOCK_UN:
        # Unlocking something this handle doesn't hold is fine on Linux
        _try(fd, msvcrt.LK_UNLCK)
        return
    if not operation & (LOCK_SH | LOCK_EX):
        raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))
    if _try(fd, msvcrt.LK_NBLCK):
        return
    # Conflict. Windows refuses a second lock from the same handle, while Linux treats it as a
    # no-op; UnlockFile only succeeds for the handle that holds the lock, so that tells which it is.
    # Nothing is remembered between calls: fd and handle numbers get reused after close.
    if _try(fd, msvcrt.LK_UNLCK) and _try(fd, msvcrt.LK_NBLCK):
        return
    while True:
        if operation & LOCK_NB:
            raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
        time.sleep(_RETRY_DELAY)
        if _try(fd, msvcrt.LK_NBLCK):
            return


def flock(fd: Any, operation: int) -> None:
    _lock(fd, operation)


def lockf(fd: Any, cmd: int, len: int = 0, start: int = 0, whence: int = 0) -> None:
    '''Locks the whole file; len/start/whence are ignored. Unlike Linux, where lockf locks belong
    to the process, two fds on the same file in one process conflict here (as with flock).'''
    _lock(fd, cmd)


def fcntl(fd: Any, cmd: int, arg: Any = 0) -> Any:
    _fileno(fd)
    if cmd in (F_GETFD, F_GETFL, F_SETFD, F_SETFL):
        # No fd flags or O_NONBLOCK on Windows files; report none and accept any
        return 0
    raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))


def ioctl(fd: Any, request: int, arg: Any = 0, mutate_flag: bool = True) -> Any:
    _fileno(fd)
    raise OSError(errno.ENOTTY, os.strerror(errno.ENOTTY))


def make_fcntl_module() -> types.ModuleType:
    mod = types.ModuleType("fcntl", "Decky's Windows stand-in for fcntl (flock/lockf via msvcrt.locking).")
    for name in ("LOCK_SH", "LOCK_EX", "LOCK_NB", "LOCK_UN", "F_DUPFD", "F_GETFD", "F_SETFD", "F_GETFL",
                 "F_SETFL", "F_GETLK", "F_SETLK", "F_SETLKW", "FD_CLOEXEC"):
        setattr(mod, name, globals()[name])
    mod.flock = flock  # pyright: ignore [reportAttributeAccessIssue]
    mod.lockf = lockf  # pyright: ignore [reportAttributeAccessIssue]
    mod.fcntl = fcntl  # pyright: ignore [reportAttributeAccessIssue]
    mod.ioctl = ioctl  # pyright: ignore [reportAttributeAccessIssue]
    return mod

# --- pwd / grp / os.getuid --------------------------------------------------------------

class struct_passwd(NamedTuple):
    pw_name: str
    pw_passwd: str
    pw_uid: int
    pw_gid: int
    pw_gecos: str
    pw_dir: str
    pw_shell: str


class struct_group(NamedTuple):
    gr_name: str
    gr_passwd: str
    gr_gid: int
    gr_mem: list[str]


def make_pwd_grp_modules(user: str, home: str, uid: int) -> tuple[types.ModuleType, types.ModuleType]:
    '''Every uid/gid this shim hands out (0 and 1000) and the names `user` and "deck" map to the current user.'''
    entry = struct_passwd(user, "x", uid, uid, user, home, "cmd.exe")
    group = struct_group(user, "x", uid, [user])
    ids = {ROOT_UID, USER_UID, uid}
    names: set[str] = {user.lower(), "deck"} | ({"root"} if uid == ROOT_UID else set[str]())

    def _by_id(i: int, what: str) -> None:
        if not isinstance(i, int):  # pyright: ignore [reportUnnecessaryIsInstance]
            raise TypeError(f"{what} should be integer, not {type(i).__name__}")
        if i not in ids:
            raise KeyError(f"{what} not found: {i}")

    def _by_name(n: str, what: str) -> None:
        if not isinstance(n, str):  # pyright: ignore [reportUnnecessaryIsInstance]
            raise TypeError(f"{what} must be str, not {type(n).__name__}")
        if n.lower() not in names:
            raise KeyError(f"{what} not found: {n!r}")

    def getpwuid(uid: int) -> struct_passwd:
        _by_id(uid, "getpwuid(): uid")
        return entry

    def getpwnam(name: str) -> struct_passwd:
        _by_name(name, "getpwnam(): name")
        return entry

    def getgrgid(gid: int) -> struct_group:
        _by_id(gid, "getgrgid(): gid")
        return group

    def getgrnam(name: str) -> struct_group:
        _by_name(name, "getgrnam(): name")
        return group

    pwd = types.ModuleType("pwd", "Decky's Windows stand-in for pwd (current user only).")
    pwd.struct_passwd = struct_passwd  # pyright: ignore [reportAttributeAccessIssue]
    pwd.getpwuid = getpwuid  # pyright: ignore [reportAttributeAccessIssue]
    pwd.getpwnam = getpwnam  # pyright: ignore [reportAttributeAccessIssue]
    pwd.getpwall = lambda: [entry]  # pyright: ignore [reportAttributeAccessIssue]
    grp = types.ModuleType("grp", "Decky's Windows stand-in for grp (current user only).")
    grp.struct_group = struct_group  # pyright: ignore [reportAttributeAccessIssue]
    grp.getgrgid = getgrgid  # pyright: ignore [reportAttributeAccessIssue]
    grp.getgrnam = getgrnam  # pyright: ignore [reportAttributeAccessIssue]
    grp.getgrall = lambda: [group]  # pyright: ignore [reportAttributeAccessIssue]
    return pwd, grp


def install_os_ids(uid: int, target: Any = os) -> None:
    '''Adds os.getuid & co. where missing. chown is a no-op so code that sees uid 0
    and then chowns (tarfile extraction, shutil.chown) keeps working.'''
    for name in ("getuid", "geteuid", "getgid", "getegid"):
        if not hasattr(target, name):
            setattr(target, name, lambda: uid)
    for name in ("chown", "lchown", "fchown"):
        if not hasattr(target, name):
            setattr(target, name, _noop_chown)


def _noop_chown(path: Any, uid: int, gid: int, *args: Any, **kwargs: Any) -> None:
    pass


def install_modules(modules: dict[str, Callable[[], types.ModuleType]],
                    sys_modules: MutableMapping[str, Any] = sys.modules) -> list[str]:
    installed: list[str] = []
    for name, factory in modules.items():
        if name in sys_modules:
            continue
        try:
            __import__(name)
            continue
        except ImportError:
            pass
        sys_modules[name] = factory()
        installed.append(name)
    return installed

# --- Linux paths (unelevated hosts only) -----------------------------------------------

def _is_link(path: str) -> bool:
    '''Junction, symlink or any other reparse point (os.path.isjunction needs Python 3.12).'''
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _drive(path: str) -> str:
    return os.path.splitdrive(path)[0].lower()


def ensure_cwd_on_drive(system_drive: str, home: str) -> None:
    '''`/tmp/x` resolves against the cwd's drive, so keep the cwd on the drive with \\tmp.'''
    if _drive(os.getcwd()) == system_drive.lower():
        return
    target = home if _drive(home) == system_drive.lower() and os.path.isdir(home) else system_drive + os.sep
    os.chdir(target)
    _log.info(f"working directory moved to {target}")


def ensure_tmp(system_drive: str) -> None:
    tmp = os.path.join(system_drive + os.sep, "tmp")
    if os.path.lexists(tmp):
        if _is_link(tmp) or not os.path.isdir(tmp):
            _log.warning(f"{tmp} is not a plain directory, leaving it alone")
        return
    try:
        os.mkdir(tmp)
    except FileExistsError:
        return
    _log.info(f"created {tmp}")


def ensure_home_links(system_drive: str, home: str, user: str) -> None:
    home_root = os.path.join(system_drive + os.sep, "home")
    if os.path.lexists(home_root):
        if _is_link(home_root) or not os.path.isdir(home_root):
            _log.warning(f"{home_root} is not a plain directory, leaving it alone")
            return
    else:
        try:
            os.mkdir(home_root)
        except FileExistsError:
            pass
    names = {n.lower(): n for n in ("deck", user) if n}
    for name in names.values():
        link = os.path.join(home_root, name)
        if os.path.lexists(link):
            continue
        try:
            _winapi.CreateJunction(home, link)
        except FileExistsError:
            continue
        except OSError as e:
            _log.warning(f"could not link {link} -> {home}: {e}")
            continue
        _log.info(f"linked {link} -> {home}")

# --- PATH helpers ----------------------------------------------------------------------

def prepend_shim_bin(env: MutableMapping[str, str], bin_dir: str = SHIM_BIN_DIR) -> bool:
    if not os.path.isdir(bin_dir):
        _log.warning(f"shim bin dir {bin_dir} is missing")
        return False
    path = env.get("PATH", "")
    entries = [os.path.normcase(os.path.normpath(p)) for p in path.split(os.pathsep) if p]
    if os.path.normcase(os.path.normpath(bin_dir)) in entries:
        return True
    env["PATH"] = bin_dir + (os.pathsep + path if path else "")
    return True

# --- entry point -----------------------------------------------------------------------

def _is_elevated() -> bool:
    from .winprocess import is_elevated
    return is_elevated()


def _best_effort(what: str, step: Callable[[], Any]) -> None:
    try:
        step()
    except Exception as e:
        _log.warning(f"plugin compat: could not {what}: {e}")


def install_plugin_compat(elevated: bool | None = None, env: MutableMapping[str, str] = os.environ,
                          system_drive: str | None = None, home: str | None = None, user: str | None = None) -> None:
    '''Call in the plugin host before the plugin's main.py is imported. Never raises.'''
    try:
        if elevated is None:
            elevated = _is_elevated()
        uid = ROOT_UID if elevated else USER_UID
        home = home or env.get("USERPROFILE") or os.path.expanduser("~")
        user = user or env.get("USERNAME") or os.path.basename(home)
        system_drive = system_drive or env.get("SystemDrive") or "C:"
    except Exception as e:
        _log.warning(f"plugin compat disabled: {e}")
        return

    def _modules() -> None:
        pwd, grp = make_pwd_grp_modules(user, home, uid)
        installed = install_modules({"fcntl": make_fcntl_module, "pwd": lambda: pwd, "grp": lambda: grp})
        if installed:
            _log.debug(f"shimmed modules: {', '.join(installed)}")

    _best_effort("shim fcntl/pwd/grp", _modules)
    _best_effort("add os.getuid", lambda: install_os_ids(uid))
    _best_effort("add the shim bin dir to PATH", lambda: prepend_shim_bin(env))
    if elevated:
        # The drive root and profile are user-writable; an admin process must not create or
        # follow things there on a plugin's behalf (see winsteamlinks)
        return
    _best_effort("move the working directory to the system drive", lambda: ensure_cwd_on_drive(system_drive, home))
    _best_effort("create /tmp", lambda: ensure_tmp(system_drive))
    _best_effort("link /home/deck", lambda: ensure_home_links(system_drive, home, user))
