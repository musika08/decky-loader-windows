r"""Linux process tools for plugins on Windows: ps, kill, pgrep, pkill, killall and pidof.

They run as `<decky> --compat-tool <name> <host pid> args...` (winplugincompat rewrites list-form calls to that), with
the plugin host's token. Only the options plugins use are supported; anything else fails like bad usage does on Linux.

Only processes in this tool's session that belong to the same Windows account are visible, so never services,
csrss/winlogon/dwm or other users' programs; processes the token can't query are invisible too. Signals:
STOP/TSTP/TTIN/TTOU suspend every thread (NtSuspendProcess), CONT resumes, INT/QUIT/TERM/KILL terminate at once
(Windows has no graceful SIGTERM), 0 only checks. Others (HUP, USR1...) have no Windows equivalent and fail.
Never signalled: this tool and the processes between it and the plugin host, the host's ancestors (the loader), the
loader's other children (other plugins' hosts), any process running an installed Decky's own program, and the Steam
client (Decky's UI runs in it, and plugins' `steam` restart commands don't exist on Windows).
"""
import getopt
import os
import re
import time
from typing import Any, Callable, Iterable, NamedTuple, Protocol, TextIO

# Linux x86-64 numbering
SIGNALS = {"HUP": 1, "INT": 2, "QUIT": 3, "ILL": 4, "TRAP": 5, "ABRT": 6, "BUS": 7, "FPE": 8, "KILL": 9, "USR1": 10,
           "SEGV": 11, "USR2": 12, "PIPE": 13, "ALRM": 14, "TERM": 15, "STKFLT": 16, "CHLD": 17, "CONT": 18, "STOP": 19,
           "TSTP": 20, "TTIN": 21, "TTOU": 22, "URG": 23, "XCPU": 24, "XFSZ": 25, "VTALRM": 26, "PROF": 27, "WINCH": 28,
           "POLL": 29, "PWR": 30, "SYS": 31}
SIGNAL_NAMES = {n: s for s, n in SIGNALS.items()}
ACTIONS = {0: "check", SIGNALS["STOP"]: "suspend", SIGNALS["TSTP"]: "suspend", SIGNALS["TTIN"]: "suspend",
           SIGNALS["TTOU"]: "suspend", SIGNALS["CONT"]: "resume", SIGNALS["INT"]: "terminate",
           SIGNALS["QUIT"]: "terminate", SIGNALS["TERM"]: "terminate", SIGNALS["KILL"]: "terminate"}

NO_SUCH_PROCESS = "No such process"
NOT_PERMITTED = "Operation not permitted"
NOT_SUPPORTED = "Operation not supported"

# Comm names (image name without .exe, lower case) that are never suspended or terminated
PROTECTED_NAMES = frozenset({"steam", "steamwebhelper", "steamservice", "steamerrorreporter", "gameoverlayui",
                             "steamhome"})
# NtResumeProcess lowers each thread's suspend count by one; SIGCONT must undo several SIGSTOP-like suspends.
# This can also undo a suspend someone else made (a debugger, another tool): SIGCONT on Linux resumes a stopped
# process whoever stopped it, and Windows doesn't record who suspended a thread, so that is accepted.
RESUME_TRIES = 8
# killall -w gives up after this many seconds (psmisc waits forever)
WAIT_TIMEOUT = 30.0
# Control characters in names and command lines print as "?", as procps does
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class Proc:
    '''A visible process. `comm` is the image name without .exe, like Linux's comm.'''
    def __init__(self, pid: int, ppid: int, comm: str, create_time: float, stopped: bool,
                 cmdline: Callable[[], list[str]], cpu_seconds: Callable[[], float] = lambda: 0.0, handle: Any = None):
        self.pid, self.ppid, self.create_time, self.stopped = pid, ppid, create_time, stopped
        self.comm = _CONTROL.sub("?", comm)
        self._cmdline, self._cmdline_value = cmdline, None
        self.cpu_seconds = cpu_seconds
        self.handle = handle

    @property
    def cmdline(self) -> list[str]:
        if self._cmdline_value is None:
            try:
                self._cmdline_value = [_CONTROL.sub("?", a) for a in self._cmdline()]
            except Exception:
                self._cmdline_value = []
        return self._cmdline_value

    @property
    def args(self) -> str:
        '''Linux /proc/<pid>/cmdline joined by spaces, or [comm] when unreadable.'''
        return " ".join(self.cmdline) or f"[{self.comm}]"


class ProcessTable(Protocol):
    user: str

    def list(self) -> list[Proc]: ...

    def act(self, proc: Proc, action: str) -> str | None:
        '''Perform "check", "suspend", "resume" or "terminate"; an error text such as NO_SUCH_PROCESS, or None.'''
        ...


class Context:
    '''The process table plus the pids tools must skip: `own` (never matched: this tool and its launcher chain up to
    the plugin host) and `protected` (never signalled: `own` and the host's ancestors).'''
    def __init__(self, table: ProcessTable, own: Iterable[int] = (), protected: Iterable[int] = ()):
        self.table = table
        self.own = set(own)
        self.protected = set(protected) | self.own


def comm_of(image_name: str) -> str:
    return image_name[:-4] if image_name.lower().endswith(".exe") else image_name


class RawProcess(NamedTuple):
    pid: int
    ppid: int
    session: int
    created: int  # FILETIME ticks
    name: str
    stopped: bool # every thread suspended
    cpu: int      # user + kernel time, 100 ns ticks


# NtQuerySystemInformation constants
SYSTEM_PROCESS_INFORMATION_CLASS = 5
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
THREAD_STATE_WAITING, WAIT_REASON_SUSPENDED = 5, 5
PROCESS_TERMINATE, PROCESS_SUSPEND_RESUME, PROCESS_QUERY_LIMITED_INFORMATION = 0x0001, 0x0800, 0x1000
STILL_ACTIVE = 259
ERROR_INVALID_PARAMETER = 87
FILETIME_UNIX_EPOCH = 116444736000000000


def _api() -> tuple[Any, Any, Any]:
    import ctypes
    from ctypes import wintypes
    ntdll = ctypes.WinDLL("ntdll")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    ntdll.NtQuerySystemInformation.restype = ctypes.c_long
    ntdll.NtQuerySystemInformation.argtypes = [ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    for name in ("NtSuspendProcess", "NtResumeProcess"):
        getattr(ntdll, name).restype = ctypes.c_long
        getattr(ntdll, name).argtypes = [wintypes.HANDLE]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_int64)] * 4
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    return ntdll, kernel32, advapi32


def snapshot() -> list[RawProcess]:
    '''Every process in one NtQuerySystemInformation call: pid, parent, session, creation time, image name and whether
    all its threads are suspended (what psutil calls STATUS_STOPPED), without opening any process.'''
    import ctypes
    from ctypes import wintypes
    ntdll, _, _ = _api()

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]

    class SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("NextEntryOffset", wintypes.ULONG), ("NumberOfThreads", wintypes.ULONG),
                    ("Reserved1", ctypes.c_byte * 24), ("CreateTime", ctypes.c_int64), ("UserTime", ctypes.c_int64),
                    ("KernelTime", ctypes.c_int64), ("ImageName", UNICODE_STRING), ("BasePriority", ctypes.c_long),
                    ("UniqueProcessId", ctypes.c_void_p), ("InheritedFromUniqueProcessId", ctypes.c_void_p),
                    ("HandleCount", wintypes.ULONG), ("SessionId", wintypes.ULONG), ("UniqueProcessKey", ctypes.c_void_p),
                    ("Reserved2", ctypes.c_size_t * 12), ("Reserved3", ctypes.c_int64 * 6)]

    class SYSTEM_THREAD_INFORMATION(ctypes.Structure):
        _fields_ = [("Reserved1", ctypes.c_int64 * 3), ("WaitTime", wintypes.ULONG), ("StartAddress", ctypes.c_void_p),
                    ("ClientId", ctypes.c_void_p * 2), ("Priority", ctypes.c_long), ("BasePriority", ctypes.c_long),
                    ("ContextSwitches", wintypes.ULONG), ("ThreadState", wintypes.ULONG), ("WaitReason", wintypes.ULONG)]

    size = 1 << 20
    while True:
        buf = ctypes.create_string_buffer(size)
        needed = wintypes.ULONG()
        status = ntdll.NtQuerySystemInformation(SYSTEM_PROCESS_INFORMATION_CLASS, buf, size, ctypes.byref(needed))
        if status & 0xFFFFFFFF == STATUS_INFO_LENGTH_MISMATCH:
            size = max(size * 2, needed.value + 65536)
            continue
        if status < 0:
            raise OSError(f"NtQuerySystemInformation failed: 0x{status & 0xFFFFFFFF:08X}")
        break
    procs: list[RawProcess] = []
    base = ctypes.addressof(buf)
    offset = 0
    while True:
        info = SYSTEM_PROCESS_INFORMATION.from_address(base + offset)
        threads = (SYSTEM_THREAD_INFORMATION * info.NumberOfThreads).from_address(
            base + offset + ctypes.sizeof(SYSTEM_PROCESS_INFORMATION))
        stopped = info.NumberOfThreads > 0 and all(
            t.ThreadState == THREAD_STATE_WAITING and t.WaitReason == WAIT_REASON_SUSPENDED for t in threads)
        name = ctypes.wstring_at(info.ImageName.Buffer, info.ImageName.Length // 2) if info.ImageName.Buffer else ""
        procs.append(RawProcess(int(info.UniqueProcessId or 0), int(info.InheritedFromUniqueProcessId or 0),
                                int(info.SessionId), int(info.CreateTime), name, stopped,
                                int(info.UserTime) + int(info.KernelTime)))
        if not info.NextEntryOffset:
            return procs
        offset += info.NextEntryOffset


def _owner_sid(pid: int) -> bytes | None:
    '''The SID owning process `pid` (its token user), or None when it can't be queried.'''
    import ctypes
    from ctypes import wintypes
    _, kernel32, advapi32 = _api()
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return None
    token = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(process, 0x0008, ctypes.byref(token)): # TOKEN_QUERY
            return None
        try:
            buf = ctypes.create_string_buffer(256)
            size = wintypes.DWORD()
            if not advapi32.GetTokenInformation(token, 1, buf, len(buf), ctypes.byref(size)): # TokenUser
                return None
            sid = ctypes.c_void_p.from_buffer(buf).value # TOKEN_USER.User.Sid
            return ctypes.string_at(sid, advapi32.GetLengthSid(sid)) if sid else None
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(process)


def _cmdline(pid: int) -> list[str]:
    import psutil
    return [str(a) for a in psutil.Process(pid).cmdline()]


class WinProcessTable:
    '''The real process table: this session's processes owned by this account.'''
    def __init__(self, snap: Callable[[], list[RawProcess]] = snapshot, owner: Callable[[int], bytes | None] = _owner_sid):
        import getpass
        self.user = os.environ.get("USERNAME") or getpass.getuser()
        self._snapshot, self._owner = snap, owner
        self._sid = owner(os.getpid())
        self._session = next((p.session for p in snap() if p.pid == os.getpid()), None)

    def raw(self) -> list[RawProcess]:
        return self._snapshot()

    def list(self) -> list[Proc]:
        procs: list[Proc] = []
        if self._sid is None or self._session is None:
            return procs
        for p in self._snapshot():
            if p.pid == 0 or p.session != self._session or self._owner(p.pid) != self._sid:
                continue
            procs.append(Proc(p.pid, p.ppid, comm_of(p.name), (p.created - FILETIME_UNIX_EPOCH) / 1e7, p.stopped,
                              lambda pid=p.pid: _cmdline(pid), lambda cpu=p.cpu: cpu / 1e7, p.created))
        return sorted(procs, key=lambda proc: proc.pid)

    def _stopped(self, pid: int) -> bool:
        return any(p.pid == pid and p.stopped for p in self._snapshot())

    def act(self, proc: Proc, action: str) -> str | None:
        import ctypes
        from ctypes import wintypes
        ntdll, kernel32, _ = _api()
        access = PROCESS_QUERY_LIMITED_INFORMATION | {"suspend": PROCESS_SUSPEND_RESUME, "resume": PROCESS_SUSPEND_RESUME,
                                                       "terminate": PROCESS_TERMINATE}.get(action, 0)
        handle = kernel32.OpenProcess(access, False, proc.pid)
        if not handle:
            return NO_SUCH_PROCESS if ctypes.get_last_error() == ERROR_INVALID_PARAMETER else NOT_PERMITTED
        try:
            # The handle pins the process: check it is still the one listed, not a later one with the same pid
            created, exited, kernel, user = (ctypes.c_int64() for _ in range(4))
            code = wintypes.DWORD()
            if (not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel),
                                             ctypes.byref(user))
                    or created.value != proc.handle
                    or not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE):
                return NO_SUCH_PROCESS
            if action == "suspend":
                if not self._stopped(proc.pid) and ntdll.NtSuspendProcess(handle) < 0:
                    return NOT_PERMITTED
            elif action == "resume":
                for _ in range(RESUME_TRIES):
                    if ntdll.NtResumeProcess(handle) < 0:
                        return NOT_PERMITTED
                    if not self._stopped(proc.pid):
                        break
            elif action == "terminate":
                if not kernel32.TerminateProcess(handle, 1):
                    return NOT_PERMITTED
            return None
        finally:
            kernel32.CloseHandle(handle)


def own_and_protected(raw: list[RawProcess], me: int, host_pid: int) -> tuple[list[int], list[int]]:
    '''(own, protected) for Context: walk the parent chain (a parent must be older than its child, since Windows
    reuses the pids of dead parents).'''
    by_pid = {p.pid: p for p in raw}
    chain = [me]
    while True:
        child = by_pid.get(chain[-1])
        parent = by_pid.get(child.ppid) if child else None
        if child is None or parent is None or parent.pid in chain or parent.pid == 0 or parent.created > child.created:
            break
        chain.append(parent.pid)
    if host_pid in chain:
        i = chain.index(host_pid)
        return chain[:i], chain[:i] + chain[i + 1:]
    return [me], chain


def image_path(pid: int) -> str | None:
    '''Full path of the program `pid` runs (QueryFullProcessImageNameW), or None.'''
    import ctypes
    from ctypes import wintypes
    _, kernel32, _ = _api()
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        return buf.value if kernel32.QueryFullProcessImageNameW(process, 0, buf, ctypes.byref(size)) else None
    finally:
        kernel32.CloseHandle(process)


def decky_processes(raw: list[RawProcess], me: int, host_pid: int, image_of: Callable[[int], str | None]) -> list[int]:
    '''Other parts of Decky, never signalled either: the loader's other children (the other plugin hosts) and, for an
    installed Decky (its program under a "Decky Loader" folder), every process running that same program.'''
    by_pid = {p.pid: p for p in raw}
    found: list[int] = []
    host = by_pid.get(host_pid)
    loader = by_pid.get(host.ppid) if host is not None else None
    if host is not None and loader is not None and loader.pid != 0 and loader.created <= host.created:
        found.extend(p.pid for p in raw if p.ppid == loader.pid and p.created >= loader.created and p.pid != p.ppid)
    own_image = image_of(me)
    if own_image and "decky loader" in [part.lower() for part in os.path.normpath(own_image).split(os.sep)]:
        name = os.path.basename(own_image).lower()
        target = os.path.normcase(own_image)
        found.extend(p.pid for p in raw if p.name.lower() == name and p.pid != me
                     and os.path.normcase(image_of(p.pid) or "") == target)
    # The plugin's own host stays signallable, as a plugin can end its own process on Linux
    return [pid for pid in found if pid != host_pid]


def real_context(host_pid: int) -> Context:
    table = WinProcessTable()
    raw = table.raw()
    own, protected = own_and_protected(raw, os.getpid(), host_pid)
    return Context(table, own, [*protected, *decky_processes(raw, os.getpid(), host_pid, image_path)])


class UsageError(Exception):
    pass


def _ints(text: str, what: str) -> list[int]:
    values: list[int] = []
    for part in re.split(r"[\s,]+", text.strip()):
        if not part:
            continue
        try:
            values.append(int(part))
        except ValueError:
            raise UsageError(f"invalid {what}: {part}")
    if not values:
        raise UsageError(f"list of {what}s must follow")
    return values


def _names(text: str) -> list[str]:
    return [n for n in re.split(r"[\s,]+", text.strip()) if n]


def parse_signal(spec: str) -> int:
    '''"9", "KILL", "SIGKILL", "sigkill" -> 9.'''
    s = spec.strip()
    if s.isdigit():
        number = int(s)
        if number == 0 or number in SIGNAL_NAMES:
            return number
        raise UsageError(f"unknown signal {spec}")
    name = s.upper()
    if name.startswith("SIG"):
        name = name[3:]
    name = {"IOT": "ABRT", "IO": "POLL", "CLD": "CHLD"}.get(name, name)
    if name not in SIGNALS:
        raise UsageError(f"unknown signal {spec}")
    return SIGNALS[name]


def _children(procs: list[Proc], parents: set[int]) -> list[Proc]:
    '''Windows keeps a dead parent's pid as ppid, so a child older than the process now using that pid isn't its child.'''
    by_pid = {p.pid: p for p in procs}
    return [p for p in procs if p.ppid in parents and p.ppid in by_pid and p.pid != p.ppid
            and p.create_time >= by_pid[p.ppid].create_time]


def signal_procs(ctx: Context, procs: Iterable[Proc], signum: int) -> dict[int, str | None]:
    '''Signal each process; pid -> error text or None.'''
    action = ACTIONS.get(signum)
    results: dict[int, str | None] = {}
    for proc in procs:
        if action is None:
            results[proc.pid] = NOT_SUPPORTED
        elif action != "check" and (proc.pid in ctx.protected or proc.comm.lower() in PROTECTED_NAMES):
            results[proc.pid] = NOT_PERMITTED
        else:
            results[proc.pid] = ctx.table.act(proc, action)
    return results

# --- ps ---------------------------------------------------------------------------------

def _etime(seconds: int) -> str:
    days, rest = divmod(max(0, seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{minutes:02d}:{secs:02d}"
    if days or hours:
        text = f"{hours:02d}:{text}"
    return f"{days}-{text}" if days else text


def _cputime(seconds: float) -> str:
    total = int(seconds)
    days, rest = divmod(total, 86400)
    text = f"{rest // 3600:02d}:{rest % 3600 // 60:02d}:{rest % 60:02d}"
    return f"{days}-{text}" if days else text


# field -> (header, right aligned width or 0 for left aligned, value)
PS_FIELDS: dict[str, tuple[str, int, Callable[[Proc, Context, float], str]]] = {
    "pid": ("PID", 7, lambda p, c, now: str(p.pid)),
    "ppid": ("PPID", 7, lambda p, c, now: str(p.ppid)),
    "stat": ("STAT", 0, lambda p, c, now: "T" if p.stopped else "S"),
    "s": ("S", 0, lambda p, c, now: "T" if p.stopped else "S"),
    "state": ("S", 0, lambda p, c, now: "T" if p.stopped else "S"),
    "comm": ("COMMAND", 0, lambda p, c, now: p.comm),
    "ucomm": ("COMMAND", 0, lambda p, c, now: p.comm),
    "ucmd": ("CMD", 0, lambda p, c, now: p.comm),
    "args": ("COMMAND", 0, lambda p, c, now: p.args),
    "cmd": ("CMD", 0, lambda p, c, now: p.args),
    "command": ("COMMAND", 0, lambda p, c, now: p.args),
    "user": ("USER", 0, lambda p, c, now: c.table.user),
    "euser": ("EUSER", 0, lambda p, c, now: c.table.user),
    "ruser": ("RUSER", 0, lambda p, c, now: c.table.user),
    "uname": ("USER", 0, lambda p, c, now: c.table.user),
    "tty": ("TT", 0, lambda p, c, now: "?"),
    "tname": ("TTY", 0, lambda p, c, now: "?"),
    "time": ("TIME", 8, lambda p, c, now: _cputime(p.cpu_seconds())),
    "etime": ("ELAPSED", 11, lambda p, c, now: _etime(int(now - p.create_time))),
    "etimes": ("ELAPSED", 7, lambda p, c, now: str(max(0, int(now - p.create_time)))),
}
PS_DEFAULT = ["pid", "tname", "time", "ucmd"]


def _ps_format(text: str, columns: list[tuple[str, str]]) -> None:
    '''"pid,stat=" or "pid=Id": a field after "=" is its header and takes the rest of the text.'''
    rest = text.strip()
    while rest:
        name, eq, after = rest.partition("=")
        fields = [f for f in re.split(r"[\s,]+", name) if f]
        if not eq:
            columns.extend((f, PS_FIELDS[f][0] if f in PS_FIELDS else "") for f in fields)
            break
        # Everything after "=" up to the end is the last field's header
        columns.extend((f, PS_FIELDS[f][0] if f in PS_FIELDS else "") for f in fields[:-1])
        if not fields:
            raise UsageError("improper format list")
        columns.append((fields[-1], after))
        break
    for name, _ in columns:
        if name.lower() not in PS_FIELDS:
            raise UsageError(f'unknown user-defined format specifier "{name}"')


def ps(args: list[str], ctx: Context, out: TextIO, err: TextIO, now: Callable[[], float] = time.time) -> int:
    try:
        opts, rest = getopt.gnu_getopt(args, "eAp:C:o:u:U:", ["pid=", "ppid=", "format=", "no-headers",
                                                               "no-heading", "user=", "User="])
    except getopt.GetoptError as e:
        err.write(f"error: {e.msg} (only part of ps is available on Windows)\n")
        return 1
    if rest:
        err.write(f"error: unsupported option or argument {rest[0]!r} (only part of ps is available on Windows)\n")
        return 1
    every, pids, ppids, names, users = False, set[int](), set[int](), set[str](), set[str]()
    selected, headers = False, True
    columns: list[tuple[str, str]] = []
    try:
        for opt, value in opts:
            if opt in ("-e", "-A"):
                every = selected = True
            elif opt in ("-p", "--pid"):
                pids.update(_ints(value, "process ID"))
                selected = True
            elif opt == "--ppid":
                ppids.update(_ints(value, "process ID"))
                selected = True
            elif opt == "-C":
                names.update(n.lower() for n in _names(value))
                selected = True
            elif opt in ("-u", "-U", "--user", "--User"):
                users.update(n.lower() for n in _names(value))
                selected = True
            elif opt in ("-o", "--format"):
                _ps_format(value, columns)
            elif opt in ("--no-headers", "--no-heading"):
                headers = False
    except UsageError as e:
        err.write(f"error: {e}\n")
        return 1
    procs = ctx.table.list()
    by_pid = {p.pid: p for p in procs}
    for p in procs:
        parent = by_pid.get(p.ppid)
        if parent is not None and parent.create_time > p.create_time:
            # The real parent is gone and its pid names a newer process. Linux would show init (1); following
            # the stale pid could even lead back to this process.
            p.ppid = 1
    if selected:
        me = {ctx.table.user.lower(), ctx.table.user.lower().rpartition("\\")[2], "deck", str(1000), str(0)}
        children = {p.pid for p in _children(procs, ppids)}
        procs = [p for p in procs if every or p.pid in pids or p.pid in children or p.comm.lower() in names
                 or (users & me)]
    if not columns:
        columns = [(f, PS_FIELDS[f][0]) for f in PS_DEFAULT]
    columns = [(name.lower(), header) for name, header in columns]
    when = now()
    rows = [[PS_FIELDS[name][2](p, ctx, when) for name, _ in columns] for p in procs]
    lines: list[list[str]] = []
    if headers and any(header for _, header in columns):
        lines.append([header for _, header in columns])
    lines.extend(rows)
    for line in lines:
        cells: list[str] = []
        for i, ((name, _), cell) in enumerate(zip(columns, line)):
            width = PS_FIELDS[name][1]
            if width:
                cells.append(cell.rjust(width))
            elif i < len(columns) - 1:
                cells.append(cell.ljust(max(len(cell), len(PS_FIELDS[name][0]))))
            else:
                cells.append(cell)
        out.write(" ".join(cells).rstrip() + "\n")
    return 0 if procs else 1

# --- kill -------------------------------------------------------------------------------

def kill(args: list[str], ctx: Context, out: TextIO, err: TextIO) -> int:
    signum = SIGNALS["TERM"]
    signal_given = False
    targets: list[str] = []
    i = 0
    try:
        while i < len(args):
            arg = args[i]
            # Options come first; after a signal, -<number> is a process group like on Linux
            if targets or arg == "-" or not arg.startswith("-") or (signal_given and arg[1:].isdigit()):
                targets.append(arg)
            elif arg == "--":
                targets.extend(args[i + 1:])
                break
            elif arg in ("-l", "--list", "-L", "--table"):
                if i + 1 < len(args):
                    spec = args[i + 1]
                    out.write((SIGNAL_NAMES[int(spec)] if spec.isdigit() and int(spec) in SIGNAL_NAMES
                               else str(parse_signal(spec))) + "\n")
                else:
                    out.write(" ".join(SIGNAL_NAMES[n] for n in sorted(SIGNAL_NAMES)) + "\n")
                return 0
            elif arg in ("-s", "--signal", "-n"):
                if i + 1 >= len(args):
                    raise UsageError(f"option {arg} requires an argument")
                signum, signal_given = parse_signal(args[i + 1]), True
                i += 1
            elif arg.startswith("--signal="):
                signum, signal_given = parse_signal(arg.partition("=")[2]), True
            else:
                signum, signal_given = parse_signal(arg[1:]), True
            i += 1
    except UsageError as e:
        err.write(f"kill: {e}\n")
        return 1
    if not targets:
        err.write("kill: not enough arguments\n")
        return 1
    procs = {p.pid: p for p in ctx.table.list()}
    status = 0
    for target in targets:
        try:
            pid = int(target)
        except ValueError:
            err.write(f"kill: cannot find process \"{target}\"\n")
            status = 1
            continue
        if pid <= 0:
            err.write(f"kill: ({pid}) - process groups are not supported on Windows\n")
            status = 1
            continue
        proc = procs.get(pid)
        if proc is None:
            err.write(f"kill: ({pid}) - {NO_SUCH_PROCESS}\n")
            status = 1
            continue
        problem = signal_procs(ctx, [proc], signum)[pid]
        if problem is not None:
            err.write(f"kill: ({pid}) - {problem}\n")
            status = 1
    return status

# --- pgrep / pkill ----------------------------------------------------------------------

def pgrep(args: list[str], ctx: Context, out: TextIO, err: TextIO, kill_mode: bool = False) -> int:
    tool = "pkill" if kill_mode else "pgrep"
    signum = SIGNALS["TERM"]
    args = list(args)
    if kill_mode and args and args[0].startswith("-") and len(args[0]) > 1:
        try:
            signum = parse_signal(args[0][1:])
            args = args[1:]
        except UsageError:
            pass
    try:
        opts, rest = getopt.gnu_getopt(args, "fxinolacvd:P:u:U:", [
            "full", "exact", "ignore-case", "newest", "oldest", "list-name", "list-full", "count", "inverse",
            "delimiter=", "parent=", "euid=", "uid=", "signal="])
    except getopt.GetoptError as e:
        err.write(f"{tool}: {e.msg} (only part of {tool} is available on Windows)\n")
        return 2
    full = exact = icase = newest = oldest = list_name = list_full = count = inverse = False
    delimiter = "\n"
    parents: set[int] = set()
    users: set[str] = set()
    try:
        for opt, value in opts:
            if opt in ("-f", "--full"):
                full = True
            elif opt in ("-x", "--exact"):
                exact = True
            elif opt in ("-i", "--ignore-case"):
                icase = True
            elif opt in ("-n", "--newest"):
                newest = True
            elif opt in ("-o", "--oldest"):
                oldest = True
            elif opt in ("-l", "--list-name"):
                list_name = True
            elif opt in ("-a", "--list-full"):
                list_full = True
            elif opt in ("-c", "--count"):
                count = True
            elif opt in ("-v", "--inverse"):
                inverse = True
            elif opt in ("-d", "--delimiter"):
                delimiter = value
            elif opt in ("-P", "--parent"):
                parents.update(_ints(value, "process ID"))
            elif opt in ("-u", "-U", "--euid", "--uid"):
                users.update(n.lower() for n in _names(value))
            elif opt == "--signal":
                signum = parse_signal(value)
    except UsageError as e:
        err.write(f"{tool}: {e}\n")
        return 2
    if len(rest) > 1:
        err.write(f"{tool}: only one pattern can be provided\n")
        return 2
    if not rest and not parents and not users:
        err.write(f"{tool}: no matching criteria specified\n")
        return 2
    try:
        regex = re.compile(rest[0], re.IGNORECASE if icase else 0) if rest else None
    except re.error as e:
        err.write(f"{tool}: invalid regular expression: {e}\n")
        return 2
    procs = [p for p in ctx.table.list() if p.pid not in ctx.own]
    me = {ctx.table.user.lower(), ctx.table.user.lower().rpartition("\\")[2], "deck", "1000", "0"}
    children = {p.pid for p in _children(procs, parents)}

    def matches(p: Proc) -> bool:
        if parents and p.pid not in children:
            return False
        if users and not users & me:
            return False
        if regex is None:
            return True
        target = p.args if full else p.comm
        return bool(regex.fullmatch(target) if exact else regex.search(target))

    found = [p for p in procs if matches(p) != inverse]
    if (newest or oldest) and found:
        pick = max if newest else min
        found = [pick(found, key=lambda p: (p.create_time, p.pid))]
    if kill_mode:
        results = signal_procs(ctx, found, signum)
        failed = [pid for pid, problem in results.items() if problem is not None]
        for pid in failed:
            err.write(f"pkill: killing pid {pid} failed: {results[pid]}\n")
        if count:
            out.write(f"{len(found) - len(failed)}\n")
        return 0 if len(found) > len(failed) else 1
    if count:
        out.write(f"{len(found)}\n")
    else:
        texts = [f"{p.pid} {p.args}" if list_full else f"{p.pid} {p.comm}" if list_name else str(p.pid) for p in found]
        if texts:
            out.write(delimiter.join(texts) + "\n")
    return 0 if found else 1

# --- killall / pidof --------------------------------------------------------------------

def _name_matches(proc: Proc, name: str, regex: bool = False, icase: bool = True) -> bool:
    if regex:
        return bool(re.search(name, proc.comm, re.IGNORECASE if icase else 0))
    # Windows file names don't care about case; "steam" and "steam.exe" both name steam.exe
    return proc.comm.lower() == comm_of(os.path.basename(name)).lower()


def killall(args: list[str], ctx: Context, out: TextIO, err: TextIO, sleep: Callable[[float], None] = time.sleep,
            clock: Callable[[], float] = time.monotonic) -> int:
    signum = SIGNALS["TERM"]
    args = list(args)
    if args and args[0].startswith("-") and len(args[0]) > 1:
        try:
            signum = parse_signal(args[0][1:])
            args = args[1:]
        except UsageError:
            pass
    try:
        opts, names = getopt.gnu_getopt(args, "s:eIqrwvu:", ["signal=", "exact", "ignore-case", "quiet", "regexp",
                                                               "wait", "verbose", "user="])
    except getopt.GetoptError as e:
        err.write(f"killall: {e.msg} (only part of killall is available on Windows)\n")
        return 1
    quiet = regex = wait = verbose = False
    users: set[str] = set()
    try:
        for opt, value in opts:
            if opt in ("-s", "--signal"):
                signum = parse_signal(value)
            elif opt in ("-q", "--quiet"):
                quiet = True
            elif opt in ("-r", "--regexp"):
                regex = True
            elif opt in ("-w", "--wait"):
                wait = True
            elif opt in ("-v", "--verbose"):
                verbose = True
            elif opt in ("-u", "--user"):
                users.add(value.lower())
    except UsageError as e:
        err.write(f"killall: {e}\n")
        return 1
    if not names:
        err.write("killall: no process name specified\n")
        return 1
    me = {ctx.table.user.lower(), ctx.table.user.lower().rpartition("\\")[2], "deck"}
    procs = [p for p in ctx.table.list() if p.pid not in ctx.own] if not users or users & me else []
    status = 0
    signalled: list[Proc] = []
    for name in names:
        try:
            found = [p for p in procs if _name_matches(p, name, regex)]
        except re.error as e:
            err.write(f"killall: bad regular expression: {e}\n")
            return 1
        results = signal_procs(ctx, found, signum)
        ok = [p for p in found if results[p.pid] is None]
        for p in found:
            if results[p.pid] is not None and not quiet:
                err.write(f"{p.comm}({p.pid}): {results[p.pid]}\n")
            elif results[p.pid] is None and verbose:
                err.write(f"Killed {p.comm}({p.pid}) with signal {signum}\n")
        if not ok:
            if not found and not quiet:
                err.write(f"{name}: no process found\n")
            status = 1
        signalled.extend(ok)
    if wait and ACTIONS.get(signum) == "terminate":
        # By pid and creation time: a reused pid is a different process, not one still dying
        pending = {(p.pid, p.create_time) for p in signalled}
        deadline = clock() + WAIT_TIMEOUT
        while True:
            pending &= {(p.pid, p.create_time) for p in ctx.table.list()}
            if not pending:
                break
            if clock() >= deadline:
                err.write(f"killall: gave up waiting after {WAIT_TIMEOUT:g} seconds for: "
                          + ", ".join(str(pid) for pid, _ in sorted(pending)) + "\n")
                return 1
            sleep(0.1)
    return status


def pidof(args: list[str], ctx: Context, out: TextIO, err: TextIO) -> int:
    try:
        opts, names = getopt.gnu_getopt(args, "sxqo:", ["single-shot", "quiet", "omit-pid="])
    except getopt.GetoptError as e:
        err.write(f"pidof: {e.msg}\n")
        return 1
    single = quiet = False
    omit: set[int] = set()
    for opt, value in opts:
        if opt in ("-s", "--single-shot"):
            single = True
        elif opt in ("-q", "--quiet"):
            quiet = True
        elif opt in ("-o", "--omit-pid"):
            omit.update(int(v) for v in _names(value) if v.isdigit())
    procs = [p for p in ctx.table.list() if p.pid not in ctx.own and p.pid not in omit]
    found: list[int] = []
    for name in names:
        # Newest first, like Linux pidof's /proc walk order
        pids = sorted((p.pid for p in procs if _name_matches(p, name)), reverse=True)
        found.extend(pids[:1] if single else pids)
    if found and not quiet:
        out.write(" ".join(map(str, found)) + "\n")
    return 0 if found else 1


TOOLS: dict[str, Callable[[list[str], Context, TextIO, TextIO], int]] = {
    "ps": ps,
    "kill": kill,
    "pgrep": pgrep,
    "pkill": lambda args, ctx, out, err: pgrep(args, ctx, out, err, kill_mode=True),
    "killall": killall,
    "pidof": pidof,
}
