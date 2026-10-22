r"""Small Linux compatibility shims for plugin processes on Windows.

Plugins are written for SteamOS and often fail on import or on their first file write:
`import fcntl` / `import pwd` raise ImportError, `os.getuid` is missing, logs go to `/tmp/...`
(which resolves to `<current drive>\tmp`) and paths like `/home/deck/...` are hardcoded.
This module adds what can be faked cheaply inside the plugin process:

- `fcntl` with flock/lockf on top of msvcrt.locking (exclusive only: LOCK_SH locks exclusively)
- `pwd` / `grp` describing the current user, and os.getuid & co. (0 elevated, else 1000)
- unelevated hosts only: cwd on the system drive, `<SystemDrive>\tmp`, and
  `<SystemDrive>\home\<user>` and `\home\deck` junctions to the user profile
- a bin dir on PATH with a no-op `chmod.cmd`
- list-form subprocess calls (subprocess and asyncio) find a bare program name that CreateProcess
  misses: `.exe`/`.com` on PATH, or a `.cmd`/`.bat` from that bin dir only, so `["chmod", ...]` works;
  then a few known install locations (Tailscale's CLI in Program Files)
- list-form calls of Linux tools (ps, kill, pgrep, pkill, killall, pidof, ping in iputils syntax,
  timedatectl, date) run Decky's own versions (wincompattools); a leading sudo/su/runuser is dropped
  when it wouldn't change the user, and refused otherwise; a Python script path runs on Decky's interpreter
- shell=True commands: a simple command naming one of those tools runs as a list-form call, and
  POSIX single quotes ('a b') become double quotes, which cmd.exe and programs understand

Not shimmed: /proc, /sys, /dev, systemctl, dbus, flatpak, pty/termios.
Root plugins must not use /tmp (use DECKY_PLUGIN_RUNTIME_DIR): <SystemDrive>\tmp may be user-owned.
Everything is best effort; a failing shim is logged and never stops a plugin from loading.
"""
import ctypes
import errno
import msvcrt
import os
import stat
import subprocess
import sys
import threading
import time
import types
import _winapi  # pyright: ignore [reportMissingImports]
from logging import getLogger
from typing import Any, Callable, Mapping, MutableMapping, NamedTuple, Sequence, cast

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

# --- program lookup for list-form calls ------------------------------------------------

# CreateProcess only tries `<name>.exe` for a bare program name, so subprocess.run(["chmod", ...])
# and asyncio.create_subprocess_exec("chmod", ...) miss chmod.cmd, which shell=True finds.
_ANYWHERE_EXTS = (".com", ".exe")
# cmd.exe parses a batch file's command line again, and list2cmdline's quoting doesn't protect
# against that (BatBadBut). Batch files are only run from our read-only bin dir, and only with
# arguments free of characters cmd.exe treats specially.
_SHIM_EXTS = (".bat", ".cmd")
_CMD_SPECIAL = frozenset('"%!^&|<>()')
_NAME_INVALID = frozenset('/\\:*?"<>|')
_DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD"


def _found_as_exe(name: str) -> bool:
    '''Whether CreateProcess finds `name` itself: SearchPathW without a path uses its search
    order (application dir, current dir, system dirs, PATH) and also appends only .exe.'''
    buf = ctypes.create_unicode_buffer(32768)
    found = ctypes.windll.kernel32.SearchPathW(None, name, ".exe", len(buf), buf, None)  # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]
    return bool(found)


def _env_value(env: Mapping[Any, Any] | None, key: str) -> str | None:
    if env is None:
        env = os.environ
    for k, v in env.items():
        if isinstance(k, str) and k.upper() == key and isinstance(v, str):
            return v
    return None


def _bare_name(arg: Any) -> str | None:
    '''A program name without directory or extension, as CreateProcess would append .exe to.'''
    if not isinstance(arg, (str, bytes, os.PathLike)):
        return None
    name = os.fsdecode(cast("str | bytes | os.PathLike[str]", arg))
    if not name or name != name.strip() or name.endswith("."):
        return None
    if any(c in _NAME_INVALID or ord(c) < 32 for c in name) or os.path.splitext(name)[1]:
        return None
    return name


def _cmd_safe(args: Sequence[Any]) -> bool:
    for arg in args:
        try:
            text = os.fsdecode(arg)
        except Exception:
            return False
        if any(c in _CMD_SPECIAL or ord(c) < 32 for c in text):
            return False
    return True


def find_program(name: str, env: Mapping[Any, Any] | None, bin_dir: str = SHIM_BIN_DIR) -> str | None:
    '''Searches the drive or UNC PATH entries of `env` (os.environ if None) in PATHEXT order, like
    shutil.which, but only for .exe/.com files, plus .bat/.cmd files inside `bin_dir`.'''
    pathext = [e.strip().lower() for e in (_env_value(env, "PATHEXT") or _DEFAULT_PATHEXT).split(os.pathsep)]
    shim_dir = os.path.normcase(os.path.normpath(bin_dir))
    for entry in (_env_value(env, "PATH") or "").split(os.pathsep):
        entry = entry.strip().strip('"')
        # Drive or UNC paths only: before Python 3.13 isabs accepts rooted Linux-style entries
        # like /usr/bin, which resolve against the current drive
        if not os.path.splitdrive(entry)[0] or not os.path.isabs(entry):
            continue
        in_shim_dir = os.path.normcase(os.path.normpath(entry)) == shim_dir
        for ext in pathext:
            if ext in _ANYWHERE_EXTS or (in_shim_dir and ext in _SHIM_EXTS):
                candidate = os.path.join(entry, name + ext)
                if os.path.isfile(candidate):
                    return candidate
    return None


# Programs that install outside PATH: name -> path under Program Files. Program Files is only writable by
# administrators, so this is safe in elevated hosts too.
KNOWN_PROGRAMS = {"tailscale": ("Tailscale", "tailscale.exe")}
_FOLDERID_PROGRAM_FILES = "{905E63B6-C1BF-494E-B29C-65B732D3D21A}"


def program_files_dir() -> str | None:
    '''The real Program Files folder (SHGetKnownFolderPath), never %ProgramFiles%, which the environment can change.'''
    from ctypes import wintypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]
    u = uuid.UUID(_FOLDERID_PROGRAM_FILES)
    guid = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    path = ctypes.c_wchar_p()
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    try:
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) != 0:
            return None
        return path.value
    finally:
        ctypes.WinDLL("ole32").CoTaskMemFree(path)


def known_program(name: str, program_files: Callable[[], str | None] = program_files_dir) -> str | None:
    parts = KNOWN_PROGRAMS.get(name)
    base = program_files() if parts else None
    if parts is None or not base:
        return None
    path = os.path.join(base, *parts)
    return path if os.path.isfile(path) else None


def resolve_argv(args: Any, env: Mapping[Any, Any] | None, bin_dir: str = SHIM_BIN_DIR,
                 known: Callable[[str], str | None] = known_program) -> Any:
    '''Returns `args` with a bare argv[0] that CreateProcess won't find replaced by a full path,
    or `args` unchanged. Only lists and tuples are looked at (a tuple comes back as a list).'''
    if not isinstance(args, (list, tuple)):
        return args
    argv: list[Any] = list(cast("Sequence[Any]", args))
    try:
        name = _bare_name(argv[0]) if argv else None
        if name is None or _found_as_exe(name):
            return argv
        program = find_program(name, env, bin_dir) or known(name)
        if program is None:
            return argv
        if os.path.splitext(program)[1].lower() in _SHIM_EXTS and not _cmd_safe([program, *argv[1:]]):
            _log.warning(f"not running {program} for {name!r}: its arguments contain characters cmd.exe would interpret")
            return argv
        _log.debug(f"resolved {name!r} to {program}")
        return [program, *argv[1:]]
    except Exception as e:
        _log.warning(f"program lookup for {argv[0]!r} failed: {e}")
        return argv


# --- Linux tools, user switches and scripts in subprocess calls --------------------------

LINUX_BIN_DIRS = frozenset({"/bin", "/usr/bin", "/sbin", "/usr/sbin", "/usr/local/bin", "/usr/local/sbin"})
USER_SWITCH = ("sudo", "su", "runuser")
# Shell syntax a command must not use to run as a list-form call instead
_SHELL_SYNTAX = frozenset("|&;<>()$`\\*?[]{}~!#\n\r")


def _self_command() -> list[str]:
    from .winsupervisor import self_command
    return self_command()


def _interpreter() -> list[str] | None:
    '''How to run a Python script with Decky's interpreter: self_command without main.py (None for a PyInstaller exe).'''
    if getattr(sys, "frozen", False):
        return None
    return _self_command()[:-1]


class CompatConfig(NamedTuple):
    bin_dir: str = SHIM_BIN_DIR
    elevated: bool = False
    user: str = ""
    # Decky's own command line (winsupervisor.self_command), which runs the tools with `--compat-tool`
    self_command: Callable[[], list[str]] = _self_command
    interpreter: Callable[[], list[str] | None] = _interpreter
    known: Callable[[str], str | None] = known_program


def tool_name(arg: Any) -> str | None:
    '''"ps" or "/usr/bin/ps" -> "ps" when Decky has its own version of that tool or wrapper.'''
    from .wincompattools import NAMES
    if not isinstance(arg, (str, bytes, os.PathLike)):
        return None
    try:
        text = os.fsdecode(cast("str | bytes | os.PathLike[str]", arg))
    except Exception:
        return None
    directory, slash, base = text.rpartition("/")
    if slash:
        if directory not in LINUX_BIN_DIRS:
            return None
        text = base
    return text if text in NAMES else None


def split_simple_command(command: str) -> list[str] | None:
    '''The words of a POSIX shell command that needs no shell (no pipes, redirections, variables, globs,
    backslashes...), or None.'''
    import shlex
    if any(c in _SHELL_SYNTAX for c in command):
        return None
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        return None
    return words or None


def _target_allowed(target: str, user: str, elevated: bool) -> bool:
    '''Whether running as `target` needs no user switch: root in an elevated host, else this user or "deck".
    An elevated host refuses its own user too: dropping administrator rights isn't done here.'''
    t = target.strip().lower()
    if t.startswith("#"):
        return t[1:] == (str(ROOT_UID) if elevated else str(USER_UID))
    if elevated:
        return t == "root"
    return t in {user.lower(), "deck"} and t != "root"


def unwrap_user_switch(argv: list[str], user: str, elevated: bool) -> list[str] | None:
    '''The command inside `sudo [-u user] [-E] ... cmd`, `runuser -u user -- cmd`, or `su [user] -c 'cmd'` when the
    user doesn't change (see _target_allowed); None to refuse: another user, an option that needs a real sudo
    (-i, -s, -l...), or a -c command that needs a shell.'''
    name = tool_name(argv[0])
    args = argv[1:]
    target = "root"
    command: list[str] | None = None
    i = 0
    if name == "sudo":
        long_flags = {"--preserve-env", "--set-home", "--non-interactive", "--stdin", "--preserve-groups",
                      "--reset-timestamp"}
        while i < len(args):
            arg = args[i]
            if arg == "--":
                command = args[i + 1:]
                break
            if not arg.startswith("-") or arg == "-":
                command = args[i:]
                break
            if arg in ("-u", "--user", "-g", "--group"):
                if i + 1 >= len(args):
                    return None
                if arg in ("-u", "--user"):
                    target = args[i + 1]
                i += 1
            elif arg.startswith(("--user=", "--group=")):
                if arg.startswith("--user="):
                    target = arg.partition("=")[2]
            elif arg.startswith(("-u", "-g")) and not arg.startswith("--"):
                if arg.startswith("-u"):
                    target = arg[2:]
            elif arg in long_flags or arg.startswith("--preserve-env="):
                pass
            elif not arg.startswith("--") and all(c in "EHnSPk" for c in arg[1:]):
                pass
            else:
                return None # -i, -s, -l, -v, -e... need a real sudo
            i += 1
        if command and "=" in command[0] and not command[0].startswith(("/", "\\")):
            return None # VAR=value before the command
    elif name == "runuser":
        login_user: str | None = None
        while i < len(args):
            arg = args[i]
            if arg == "--":
                command = args[i + 1:]
                break
            if arg in ("-u", "--user") and i + 1 < len(args):
                target = args[i + 1]
                i += 2
                continue
            if arg in ("-c", "--command") and i + 1 < len(args):
                command = split_simple_command(args[i + 1])
                if command is None:
                    return None
                i += 2
                continue
            if arg in ("-m", "-p", "--preserve-environment", "-l", "-", "--login"):
                i += 1
                continue
            if arg.startswith("-"):
                return None
            if command is None and login_user is None and target == "root":
                login_user = arg
                target = arg
                i += 1
                continue
            command = args[i:]
            break
    elif name == "su":
        user_given = False
        while i < len(args):
            arg = args[i]
            if arg in ("-c", "--command") and i + 1 < len(args):
                command = split_simple_command(args[i + 1])
                if command is None:
                    return None
                i += 2
                continue
            if arg.startswith("--command="):
                command = split_simple_command(arg.partition("=")[2])
                if command is None:
                    return None
            elif arg.startswith("-c") and len(arg) > 2:
                command = split_simple_command(arg[2:])
                if command is None:
                    return None
            elif arg in ("-m", "-p", "--preserve-environment", "-l", "-", "--login"):
                pass
            elif arg.startswith("-") or user_given:
                return None # -s shell, or arguments for the user's shell
            else:
                target, user_given = arg, True
            i += 1
    else:
        return argv
    if not command or not _target_allowed(target, user, elevated):
        return None
    return command


def python_script(arg: Any) -> str | None:
    '''A Python script given by full path (drive or UNC): *.py/*.pyw, or a file without extension whose `#!` line
    names python. CreateProcess can't run those; on Linux the kernel reads the `#!` line.'''
    if not isinstance(arg, (str, bytes, os.PathLike)):
        return None
    try:
        path = os.fsdecode(cast("str | bytes | os.PathLike[str]", arg))
        if not os.path.splitdrive(path)[0] or not os.path.isabs(path) or not os.path.isfile(path):
            return None
        ext = os.path.splitext(path)[1].lower()
        if ext in (".py", ".pyw"):
            return path
        if ext:
            return None
        with open(path, "rb") as f:
            first = f.readline(256)
    except (OSError, ValueError):
        return None
    import re
    line = first.split(b"\n")[0].strip()
    return path if line.startswith(b"#!") and re.search(rb"(^#!|[/\s])python[0-9.]*(\s|$)", line) else None


def _tool_argv(config: CompatConfig, name: str, args: Sequence[Any]) -> list[str] | None:
    try:
        texts = [os.fsdecode(a) for a in args]
        return [*config.self_command(), "--compat-tool", name, str(os.getpid()), *texts]
    except Exception as e:
        _log.warning(f"could not run Decky's {name}: {e}")
        return None


def rewrite_argv(args: Any, env: Mapping[Any, Any] | None, config: CompatConfig) -> Any:
    '''A list-form call's argv with sudo/su/runuser handled, Decky's tools and Python scripts swapped in, and bare
    program names resolved (resolve_argv). Anything else comes back unchanged.'''
    from .winping import is_linux_syntax
    if not isinstance(args, (list, tuple)):
        return args
    argv: list[Any] = list(cast("Sequence[Any]", args))
    if not argv:
        return argv
    try:
        for _ in range(3):
            name = tool_name(argv[0])
            if name not in USER_SWITCH:
                break
            inner = unwrap_user_switch([os.fsdecode(a) for a in argv], config.user, config.elevated)
            if inner is None:
                _log.warning(f"refusing {name}: Decky can't run commands as another user on Windows")
                return _tool_argv(config, name, argv[1:]) or argv
            argv = list(inner)
        name = tool_name(argv[0])
        if name is not None and name not in USER_SWITCH:
            texts = [os.fsdecode(a) for a in argv[1:]]
            if name != "ping" or is_linux_syntax(texts):
                tool = _tool_argv(config, name, texts)
                if tool is not None:
                    return tool
        script = python_script(argv[0])
        if script is not None:
            interpreter = config.interpreter()
            if interpreter:
                return [*interpreter, script, *argv[1:]]
    except Exception as e:
        _log.warning(f"command rewrite for {argv[0]!r} failed: {e}")
    return resolve_argv(argv, env, config.bin_dir, config.known)


def _startable(program: Any) -> bool:
    if not isinstance(program, str):
        return False
    if os.path.splitdrive(program)[0] and os.path.isabs(program):
        return os.path.isfile(program)
    name = _bare_name(program)
    return name is not None and _found_as_exe(name)


def translate_posix_quotes(command: str) -> str:
    '''POSIX single-quoted words ('a b') as double-quoted ones ("a b") for cmd.exe and the program's argument parser,
    which don't know single quotes. Unchanged when there are none, when they are unbalanced, or when a quoted part
    holds something cmd.exe would still interpret inside double quotes (" % ! ^ and the like) or a control character.
    Double-quoted POSIX text is left as it is.'''
    if "'" not in command:
        return command
    out: list[str] = []
    in_double = changed = False
    i = 0
    while i < len(command):
        c = command[i]
        if in_double:
            out.append(c)
            if c == "\\" and i + 1 < len(command):
                out.append(command[i + 1])
                i += 2
                continue
            in_double = c != '"'
            i += 1
            continue
        if c == '"':
            in_double = True
            out.append(c)
        elif c == "\\" and command[i + 1:i + 2] == "'":
            out.append("'") # an escaped quote is a literal one
            changed = True
            i += 1
        elif c == "'":
            end = command.find("'", i + 1)
            if end < 0:
                return command
            content = command[i + 1:end]
            if any(ch in _CMD_SPECIAL or ord(ch) < 32 for ch in content):
                return command
            # Backslashes right before the closing quote would escape it for the program's argument parser
            bare = content.rstrip("\\")
            out.append('"' + bare + "\\" * (2 * (len(content) - len(bare))) + '"')
            changed = True
            i = end
        else:
            out.append(c)
        i += 1
    if in_double or not changed:
        return command
    return "".join(out)


def rewrite_shell(command: Any, env: Mapping[Any, Any] | None, config: CompatConfig) -> tuple[Any, bool]:
    '''(args, shell) for a shell=True call: a simple command naming one of Decky's tools or a user switch runs as a
    list-form call (no cmd.exe, which would also mangle its quoting); otherwise POSIX single quotes are translated.'''
    if not isinstance(command, str):
        return command, True
    try:
        words = split_simple_command(command)
        if words is not None and tool_name(words[0]) is not None:
            argv = rewrite_argv(words, env, config)
            # Only when the result surely starts: through cmd.exe a missing program is exit code 1, not an exception
            if argv != words and _startable(argv[0]):
                return argv, False
        return translate_posix_quotes(command), True
    except Exception as e:
        _log.warning(f"shell command rewrite failed: {e}")
        return command, True


def install_program_lookup(popen: Any = subprocess.Popen, bin_dir: str = SHIM_BIN_DIR,
                           config: CompatConfig | None = None) -> bool:
    '''Wraps Popen._execute_child, which every subprocess call and asyncio's Windows subprocess
    transport (asyncio.windows_utils.Popen subclasses subprocess.Popen) go through. Calls with
    executable= are never touched; list-form calls go through rewrite_argv, shell=True ones through
    rewrite_shell. Returns False if already installed.'''
    original = popen._execute_child
    if getattr(original, "_decky_program_lookup", False):
        return False
    cfg = config or CompatConfig(bin_dir=bin_dir, user=os.environ.get("USERNAME", ""))

    def _execute_child(self: Any, args: Any, executable: Any, preexec_fn: Any, close_fds: Any, pass_fds: Any,
                       cwd: Any, env: Any, startupinfo: Any, creationflags: Any, shell: Any, *rest: Any) -> Any:
        if executable is None:
            if shell:
                args, shell = rewrite_shell(args, env, cfg)
                if not shell:
                    # cmd.exe would have run hidden; so does the program now started in its place
                    creationflags |= subprocess.CREATE_NO_WINDOW
            else:
                args = rewrite_argv(args, env, cfg)
        return original(self, args, executable, preexec_fn, close_fds, pass_fds, cwd, env, startupinfo,
                        creationflags, shell, *rest)

    setattr(_execute_child, "_decky_program_lookup", True)
    popen._execute_child = _execute_child
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
    _best_effort("set up program lookup",
                 lambda: install_program_lookup(config=CompatConfig(elevated=bool(elevated), user=user)))
    if elevated:
        # The drive root and profile are user-writable; an admin process must not create or
        # follow things there on a plugin's behalf (see winsteamlinks)
        return
    _best_effort("move the working directory to the system drive", lambda: ensure_cwd_on_drive(system_drive, home))
    _best_effort("create /tmp", lambda: ensure_tmp(system_drive))
    _best_effort("link /home/deck", lambda: ensure_home_links(system_drive, home, user))
