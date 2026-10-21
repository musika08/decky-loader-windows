'''Windows plugin process launcher.

Root-flag plugins run with the loader's own (elevated) token. Every other plugin runs with a restricted copy of it:
Administrators deny-only, all privileges but SeChangeNotify removed, medium integrity, owned by the user.

Each child starts suspended inside its own kill-on-close Job object, so stopping a plugin also stops every process
it started (the PyInstaller onefile bootloader runs the real interpreter as its child).
'''
import ctypes, os, subprocess
from ctypes import wintypes
from typing import Any

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectBasicAccountingInformation = 1
JobObjectExtendedLimitInformation = 9

TOKEN_ASSIGN_PRIMARY = 0x0001
TOKEN_DUPLICATE = 0x0002
TOKEN_QUERY = 0x0008
TOKEN_ADJUST_DEFAULT = 0x0080

DISABLE_MAX_PRIVILEGE = 0x1
LUA_TOKEN = 0x4

# TOKEN_INFORMATION_CLASS
TokenUser = 1
TokenOwner = 4
TokenDefaultDacl = 6
TokenElevation = 20
TokenIntegrityLevel = 25

SE_GROUP_INTEGRITY = 0x20
SDDL_REVISION_1 = 1

ADMINISTRATORS_SID = "S-1-5-32-544"
MEDIUM_INTEGRITY_SID = "S-1-16-8192"

HANDLE = ctypes.c_void_p
PSID = ctypes.c_void_p


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", PSID), ("Attributes", wintypes.DWORD)]


class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", SID_AND_ATTRIBUTES)]


class TOKEN_OWNER(ctypes.Structure):
    _fields_ = [("Owner", PSID)]


class TOKEN_DEFAULT_DACL(ctypes.Structure):
    _fields_ = [("DefaultDacl", ctypes.c_void_p)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", HANDLE),
        ("hStdOutput", HANDLE),
        ("hStdError", HANDLE),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t), # ULONG_PTR
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", HANDLE),
        ("hThread", HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


_dlls: tuple[Any, Any] | None = None

def _api() -> tuple[Any, Any]:
    '''(kernel32, advapi32) with argtypes set. Loaded lazily so importing this module is harmless anywhere.'''
    global _dlls
    if _dlls is not None:
        return _dlls
    k: Any = ctypes.WinDLL("kernel32", use_last_error=True) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    a: Any = ctypes.WinDLL("advapi32", use_last_error=True) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    DWORD, BOOL = wintypes.DWORD, wintypes.BOOL

    k.GetCurrentProcess.argtypes = []
    k.GetCurrentProcess.restype = HANDLE
    k.CloseHandle.argtypes = [HANDLE]
    k.CloseHandle.restype = BOOL
    k.TerminateProcess.argtypes = [HANDLE, wintypes.UINT]
    k.TerminateProcess.restype = BOOL
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.LocalFree.restype = ctypes.c_void_p
    k.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, BOOL, DWORD,
                                 ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    k.CreateProcessW.restype = BOOL
    k.ResumeThread.argtypes = [HANDLE]
    k.ResumeThread.restype = DWORD
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.CreateJobObjectW.restype = HANDLE
    k.SetInformationJobObject.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD]
    k.SetInformationJobObject.restype = BOOL
    k.QueryInformationJobObject.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)]
    k.QueryInformationJobObject.restype = BOOL
    k.AssignProcessToJobObject.argtypes = [HANDLE, HANDLE]
    k.AssignProcessToJobObject.restype = BOOL
    k.TerminateJobObject.argtypes = [HANDLE, wintypes.UINT]
    k.TerminateJobObject.restype = BOOL

    a.OpenProcessToken.argtypes = [HANDLE, DWORD, ctypes.POINTER(HANDLE)]
    a.OpenProcessToken.restype = BOOL
    a.GetTokenInformation.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD)]
    a.GetTokenInformation.restype = BOOL
    a.SetTokenInformation.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD]
    a.SetTokenInformation.restype = BOOL
    a.CreateRestrictedToken.argtypes = [HANDLE, DWORD, DWORD, ctypes.POINTER(SID_AND_ATTRIBUTES), DWORD, ctypes.c_void_p,
                                        DWORD, ctypes.POINTER(SID_AND_ATTRIBUTES), ctypes.POINTER(HANDLE)]
    a.CreateRestrictedToken.restype = BOOL
    a.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(PSID)]
    a.ConvertStringSidToSidW.restype = BOOL
    a.ConvertSidToStringSidW.argtypes = [PSID, ctypes.POINTER(ctypes.c_void_p)]
    a.ConvertSidToStringSidW.restype = BOOL
    a.GetLengthSid.argtypes = [PSID]
    a.GetLengthSid.restype = DWORD
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(DWORD)]
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = BOOL
    a.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(BOOL)]
    a.GetSecurityDescriptorDacl.restype = BOOL
    a.CreateProcessAsUserW.argtypes = [HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, BOOL, DWORD,
                                       ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    a.CreateProcessAsUserW.restype = BOOL
    _dlls = (k, a)
    return _dlls


def _check(ok: Any) -> None:
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]


def _close(h: int | None) -> None:
    if h:
        _api()[0].CloseHandle(h)


def _open_own_token(access: int) -> int:
    k, a = _api()
    h = HANDLE()
    _check(a.OpenProcessToken(k.GetCurrentProcess(), access, ctypes.byref(h)))
    assert h.value
    return h.value


def _token_info(token: int, cls: int) -> ctypes.Array[ctypes.c_char]:
    a = _api()[1]
    size = wintypes.DWORD()
    a.GetTokenInformation(token, cls, None, 0, ctypes.byref(size))
    buf = ctypes.create_string_buffer(size.value)
    _check(a.GetTokenInformation(token, cls, buf, size, ctypes.byref(size)))
    return buf


def is_elevated() -> bool:
    token = _open_own_token(TOKEN_QUERY)
    try:
        return wintypes.DWORD.from_buffer(_token_info(token, TokenElevation)).value != 0
    finally:
        _close(token)


def _string_sid(s: str) -> int:
    sid = PSID()
    _check(_api()[1].ConvertStringSidToSidW(s, ctypes.byref(sid)))
    assert sid.value
    return sid.value


def _sid_to_string(sid: int) -> str:
    out = ctypes.c_void_p()
    _check(_api()[1].ConvertSidToStringSidW(sid, ctypes.byref(out)))
    try:
        return ctypes.wstring_at(out.value or 0)
    finally:
        _api()[0].LocalFree(out)


def _restrict_token(token: int) -> int:
    '''Medium-integrity, non-admin primary token derived from `token`. The caller closes the result.'''
    k, a = _api()
    admins = _string_sid(ADMINISTRATORS_SID)
    medium = _string_sid(MEDIUM_INTEGRITY_SID)
    sd = ctypes.c_void_p()
    restricted = HANDLE()
    try:
        disable = (SID_AND_ATTRIBUTES * 1)(SID_AND_ATTRIBUTES(admins, 0))
        _check(a.CreateRestrictedToken(token, DISABLE_MAX_PRIVILEGE | LUA_TOKEN, 1, disable, 0, None, 0, None, ctypes.byref(restricted)))
        new = restricted.value
        assert new
        try:
            label = TOKEN_MANDATORY_LABEL(SID_AND_ATTRIBUTES(medium, SE_GROUP_INTEGRITY))
            _check(a.SetTokenInformation(new, TokenIntegrityLevel, ctypes.byref(label),
                                         ctypes.sizeof(label) + a.GetLengthSid(medium)))

            # An elevated token is owned by Administrators and its default DACL only grants Administrators and SYSTEM.
            # With Administrators deny-only the child could not open its own objects (named pipes, its process), so
            # hand ownership and the default DACL to the user, as UAC's filtered token does.
            user_buf = _token_info(new, TokenUser) # keep the SID's backing memory alive
            user_sid = ctypes.c_void_p.from_buffer(user_buf).value
            owner = TOKEN_OWNER(user_sid)
            _check(a.SetTokenInformation(new, TokenOwner, ctypes.byref(owner), ctypes.sizeof(owner)))

            sddl = f"D:(A;;GA;;;{_sid_to_string(user_sid or 0)})(A;;GA;;;SY)"
            _check(a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, SDDL_REVISION_1, ctypes.byref(sd), None))
            present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
            _check(a.GetSecurityDescriptorDacl(sd, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)))
            default_dacl = TOKEN_DEFAULT_DACL(dacl.value)
            _check(a.SetTokenInformation(new, TokenDefaultDacl, ctypes.byref(default_dacl), ctypes.sizeof(default_dacl)))
        except BaseException:
            _close(new)
            raise
        return new
    finally:
        if sd.value:
            k.LocalFree(sd)
        k.LocalFree(admins)
        k.LocalFree(medium)


def _env_block(env: dict[str, str]) -> ctypes.Array[ctypes.c_char]:
    for key in env:
        if not key or "=" in key[1:] or "\0" in key or "\0" in env[key]:
            raise ValueError(f"invalid environment variable name {key!r}")
    items = sorted(env.items(), key=lambda kv: kv[0].upper())
    block = "".join(f"{k}={v}\0" for k, v in items) + "\0"
    return ctypes.create_string_buffer(block.encode("utf-16-le"))


def _create_job() -> int:
    k = _api()[0]
    job = k.CreateJobObjectW(None, None)
    _check(job)
    try:
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _check(k.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)))
    except BaseException:
        _close(job)
        raise
    return job


def run_in_job(argv: list[str], env: dict[str, str] | None, cwd: str | None) -> int:
    """Run `argv` to completion inside a new kill-on-close Job object and return its exit code.

    The child starts suspended and only runs once it is in the job, so nothing it starts can escape. The job handle
    lives in this process: if we die, or once the child exits, everything still in the job is killed.
    The child shares our console and standard handles, like subprocess.run.
    """
    import _winapi # pyright: ignore [reportMissingImports]
    k = _api()[0]
    job = _create_job()
    try:
        hp, ht, _pid, _tid = _winapi.CreateProcess(None, subprocess.list2cmdline(argv), None, None, False,
                                                   CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT, dict(os.environ) if env is None else env, cwd,
                                                   subprocess.STARTUPINFO())
        try:
            try:
                if not k.AssignProcessToJobObject(job, int(hp)):
                    err = ctypes.get_last_error()
                    k.TerminateProcess(int(hp), 1)
                    raise ctypes.WinError(err) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
                if k.ResumeThread(int(ht)) == 0xFFFFFFFF:
                    err = ctypes.get_last_error()
                    k.TerminateJobObject(job, 1)
                    raise ctypes.WinError(err) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
            finally:
                _winapi.CloseHandle(ht)
            _winapi.WaitForSingleObject(hp, _winapi.INFINITE)
            return int(_winapi.GetExitCodeProcess(hp))
        finally:
            _winapi.CloseHandle(hp)
    finally:
        _close(job)


class PluginProcess:
    """The subset of multiprocessing.Process that PluginWrapper uses, for a plugin host started as its own program.

    The process and everything it starts live in a kill-on-close Job object owned by this instance.
    """

    def __init__(self, argv: list[str], env: dict[str, str], elevated: bool) -> None:
        self.argv = argv
        self.env = env
        self.elevated = elevated
        self.pid: int = 0
        self._job: int | None = None

    def start(self) -> None:
        if self.elevated or not is_elevated():
            self._spawn(None)
        else:
            self.start_restricted()

    def start_restricted(self) -> None:
        """Start under a restricted medium-integrity copy of this process's token, whatever its elevation."""
        token = _open_own_token(TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ASSIGN_PRIMARY | TOKEN_ADJUST_DEFAULT)
        restricted = None
        try:
            restricted = _restrict_token(token)
            self._spawn(restricted)
        finally:
            _close(restricted)
            _close(token)

    def _spawn(self, token: int | None) -> None:
        """Create the process suspended with `token` (None: our own), put it in a new job, then let it run."""
        if self._job is not None:
            raise RuntimeError("process already started")
        k, a = _api()
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        pi = PROCESS_INFORMATION()
        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(self.argv))
        env = _env_block(self.env)
        flags = CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW | CREATE_SUSPENDED
        job = _create_job()
        try:
            if token is None:
                _check(k.CreateProcessW(None, cmdline, None, None, False, flags, env, None, ctypes.byref(si), ctypes.byref(pi)))
            else:
                _check(a.CreateProcessAsUserW(token, None, cmdline, None, None, False, flags, env, None,
                                              ctypes.byref(si), ctypes.byref(pi)))
            try:
                if not k.AssignProcessToJobObject(job, pi.hProcess):
                    err = ctypes.get_last_error()
                    k.TerminateProcess(pi.hProcess, 1)
                    raise ctypes.WinError(err) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
                if k.ResumeThread(pi.hThread) == 0xFFFFFFFF:
                    err = ctypes.get_last_error()
                    k.TerminateJobObject(job, 1)
                    raise ctypes.WinError(err) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
            finally:
                _close(pi.hThread)
                _close(pi.hProcess)
        except BaseException:
            _close(job) # kill-on-close takes any member down with it
            raise
        self._job = job
        self.pid = pi.dwProcessId

    def is_alive(self) -> bool:
        if self._job is None:
            return False
        info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        _check(_api()[0].QueryInformationJobObject(self._job, JobObjectBasicAccountingInformation, ctypes.byref(info),
                                                   ctypes.sizeof(info), None))
        return info.ActiveProcesses > 0

    def terminate(self) -> None:
        if self._job is not None:
            _api()[0].TerminateJobObject(self._job, 1)

    def kill(self) -> None:
        self.terminate()

    def close(self) -> None:
        """Release the job. Any process still in it is killed."""
        job, self._job = self._job, None
        _close(job)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
