'''Windows plugin process launcher.

Root-flag plugins run with the loader's own (elevated) token. Every other plugin runs with a restricted copy of it:
Administrators deny-only, all privileges but SeChangeNotify removed, medium integrity, owned by the user.
'''
import ctypes, subprocess
from ctypes import wintypes
from typing import Any

CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400

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
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102

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
    k.WaitForSingleObject.argtypes = [HANDLE, DWORD]
    k.WaitForSingleObject.restype = DWORD
    k.TerminateProcess.argtypes = [HANDLE, wintypes.UINT]
    k.TerminateProcess.restype = BOOL
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.LocalFree.restype = ctypes.c_void_p

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


class PluginProcess:
    '''The subset of multiprocessing.Process that PluginWrapper uses, for a plugin host started as its own program.'''

    def __init__(self, argv: list[str], env: dict[str, str], elevated: bool) -> None:
        self.argv = argv
        self.env = env
        self.elevated = elevated
        self.pid: int = 0
        self._popen: subprocess.Popen[bytes] | None = None
        self._handle: int | None = None

    def start(self) -> None:
        if self.elevated or not is_elevated():
            self._popen = subprocess.Popen(self.argv, env=self.env, creationflags=CREATE_NO_WINDOW)
            self.pid = self._popen.pid
        else:
            self.start_restricted()

    def start_restricted(self) -> None:
        '''Start under a restricted medium-integrity copy of this process's token, whatever its elevation.'''
        a = _api()[1]
        token = _open_own_token(TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ASSIGN_PRIMARY | TOKEN_ADJUST_DEFAULT)
        restricted = None
        try:
            restricted = _restrict_token(token)
            si = STARTUPINFOW()
            si.cb = ctypes.sizeof(si)
            pi = PROCESS_INFORMATION()
            cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(self.argv))
            env = _env_block(self.env)
            _check(a.CreateProcessAsUserW(restricted, None, cmdline, None, None, False,
                                          CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW, env, None,
                                          ctypes.byref(si), ctypes.byref(pi)))
            _close(pi.hThread)
            self._handle = pi.hProcess
            self.pid = pi.dwProcessId
        finally:
            _close(restricted)
            _close(token)

    def is_alive(self) -> bool:
        if self._popen is not None:
            return self._popen.poll() is None
        if self._handle is None:
            return False
        if _api()[0].WaitForSingleObject(self._handle, 0) == WAIT_TIMEOUT:
            return True
        self._release()
        return False

    def terminate(self) -> None:
        if self._popen is not None:
            if self._popen.poll() is None:
                self._popen.terminate() # TerminateProcess(h, 1)
        elif self._handle is not None:
            _api()[0].TerminateProcess(self._handle, 1)

    def kill(self) -> None:
        self.terminate()

    def _release(self) -> None:
        h, self._handle = self._handle, None
        _close(h)

    def __del__(self) -> None:
        try:
            self._release()
        except Exception:
            pass
