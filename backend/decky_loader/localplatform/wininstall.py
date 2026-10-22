r'''Windows install: copies the installer folder to Program Files and registers an elevated logon task that starts it.

Layout: `%ProgramFiles%\Decky Loader\python` (a private copy of the PSF-signed CPython runtime) and `\app` (Decky's
source and its dependencies), and `%ProgramData%\decky` (privileged root: plugins, settings). The task runs
`python\pythonw.exe app\main.py`. Only an installer folder (`python` plus `app`, built by tools\build_win_runtime.py)
can be installed; PyInstaller builds are portable only.
'''
import csv, ctypes, datetime, io, logging, os, secrets, shutil, subprocess, sys, tempfile, time
from typing import Any, Callable
from xml.sax.saxutils import escape

logger = logging.getLogger("wininstall")

TASK_NAME = "Decky Loader"
# The portable PyInstaller exe, and what earlier installs put in Program Files (with its `_internal` folder)
EXE_NAME = "PluginLoader.exe"
PROGRAM_DIR_NAME = "Decky Loader"
# Left in %ProgramData%\decky by the earlier onefile layout (the exe and its wscript launcher, its extraction dir)
LEGACY_DIRS = ("services", "tmp")

# The installed program, and an installer folder, hold exactly these two folders
RUNTIME_DIR_NAME = "python"
APP_DIR_NAME = "app"
PROGRAM_SUBDIRS = (RUNTIME_DIR_NAME, APP_DIR_NAME)
MAIN_SCRIPT = "main.py"
CONSOLE_INTERPRETER = "python.exe"
WINDOWED_INTERPRETER = "pythonw.exe" # What the task runs: no console window
INTERPRETERS = (CONSOLE_INTERPRETER, WINDOWED_INTERPRETER)

def runtime_interpreters(root: str) -> list[str]:
    r'''`<root>\python\python.exe` and `pythonw.exe`.'''
    return [os.path.join(root, RUNTIME_DIR_NAME, name) for name in INTERPRETERS]

def main_script_path(root: str) -> str:
    r'''`<root>\app\main.py`.'''
    return os.path.join(root, APP_DIR_NAME, MAIN_SCRIPT)

def running_main_script() -> str | None:
    '''Absolute path of the script this interpreter was started with (`__main__`), or None (e.g. `-c`).'''
    main = sys.modules.get("__main__")
    path = getattr(main, "__file__", None)
    return os.path.abspath(path) if isinstance(path, str) and path else None

def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))

def program_images(app_dir: str, root: str) -> list[str]:
    r'''Every image our processes can run from: the installed runtime's interpreters, and the exe of the two earlier
    layouts (`<app_dir>\PluginLoader.exe`, `<root>\services\PluginLoader.exe`). Other Pythons never match.'''
    return runtime_interpreters(app_dir) + [os.path.join(app_dir, EXE_NAME), os.path.join(root, "services", EXE_NAME)]

_SID_SYSTEM = "S-1-5-18"
_SID_ADMINS = "S-1-5-32-544"

SW_SHOWNORMAL = 1
SHELL_EXECUTE_OK_ABOVE = 32

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    except Exception:
        return False

def current_user_sid(run: Callable[..., Any] = subprocess.run) -> str:
    out = run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True).stdout
    row = next(csv.reader(io.StringIO(out.strip())))
    return row[1]

def build_task_xml(exe: str, workdir: str, user_sid: str, arguments: str | None = None) -> str:
    '''The logon task: start `exe` directly with `arguments` (a command line, or none), in `workdir`, elevated,
    for `user_sid`.'''
    sid, workdir, command = escape(user_sid), escape(workdir), escape(exe)
    args = f"\n      <Arguments>{escape(arguments)}</Arguments>" if arguments else ""
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts Decky Loader at logon with administrator rights.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{sid}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{sid}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>{args}
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'''

# Applied at directory creation, so there is no window where the directory is writable by anyone else.
# Owner and group: Administrators. Protected DACL: SYSTEM full, Administrators full, Users read/execute.
SECURE_SDDL = "O:BAG:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)"

FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
SE_DACL_PROTECTED = 0x1000
TRUSTED_OWNERS = (_SID_ADMINS, _SID_SYSTEM)

READ_CONTROL = 0x00020000
FILE_READ_ATTRIBUTES = 0x80
# Handles that only ask for attribute/security access are ignored by the share-mode check, so without a
# data access right (list directory) the missing FILE_SHARE_DELETE would not actually block a rename.
FILE_LIST_DIRECTORY = 0x1
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2 # Deliberately no FILE_SHARE_DELETE: nobody can rename or delete a directory we hold open
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

def is_reparse_point(path: str) -> bool:
    '''Junctions and symlinks. Never follows the link.'''
    try:
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return False

def _kernel32() -> Any:
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    k.CreateFileW.restype = ctypes.c_void_p
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.CloseHandle.restype = wintypes.BOOL
    k.LocalFree.argtypes = [ctypes.c_void_p]
    k.LocalFree.restype = ctypes.c_void_p
    return k

def open_dir(path: str) -> int:
    '''Open a directory handle without following a final reparse point and without share-delete.'''
    k = _kernel32()
    handle = k.CreateFileW(path, READ_CONTROL | FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                           OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None)
    if handle is None or handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    return handle

def close_handle(handle: int) -> None:
    _kernel32().CloseHandle(handle)

def query_dir(handle: int) -> tuple[int, str, int]:
    '''(file attributes, owner SID string, security descriptor control flags), all read from the one handle.'''
    from ctypes import wintypes
    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]
    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", FILETIME), ("ftLastAccessTime", FILETIME),
                    ("ftLastWriteTime", FILETIME), ("dwVolumeSerialNumber", wintypes.DWORD), ("nFileSizeHigh", wintypes.DWORD),
                    ("nFileSizeLow", wintypes.DWORD), ("nNumberOfLinks", wintypes.DWORD), ("nFileIndexHigh", wintypes.DWORD),
                    ("nFileIndexLow", wintypes.DWORD)]
    k = _kernel32()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    k.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
    k.GetFileInformationByHandle.restype = wintypes.BOOL
    advapi.GetSecurityInfo.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL

    info = BY_HANDLE_FILE_INFORMATION()
    if not k.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]

    SE_FILE_OBJECT, OWNER_INFO, DACL_INFO = 1, 1, 4
    owner, dacl, sd = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    rc = advapi.GetSecurityInfo(handle, SE_FILE_OBJECT, OWNER_INFO | DACL_INFO, ctypes.byref(owner), None,
                                ctypes.byref(dacl), None, ctypes.byref(sd))
    if rc != 0:
        raise ctypes.WinError(rc) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    try:
        sid_str = ctypes.c_void_p()
        if not advapi.ConvertSidToStringSidW(owner, ctypes.byref(sid_str)):
            raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        try:
            owner_sid = ctypes.wstring_at(sid_str.value or 0)
        finally:
            k.LocalFree(sid_str)
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not advapi.GetSecurityDescriptorControl(sd, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        return int(info.dwFileAttributes), owner_sid, int(control.value)
    finally:
        k.LocalFree(sd)

def open_trusted_dir(path: str, *, opener: Callable[[str], Any] = open_dir,
                     query: Callable[[Any], tuple[int, str, int]] = query_dir,
                     closer: Callable[[Any], None] = close_handle) -> Any | None:
    '''Open `path` and validate that same handle: a real directory (not a link), owned by Administrators or
    SYSTEM, with a protected DACL. Returns the open handle if trusted, else closes it and returns None.
    Everything is checked on the handle, so the path cannot be swapped between the check and later use.'''
    try:
        handle = opener(path)
    except OSError:
        return None
    try:
        attrs, owner, control = query(handle)
        trusted = (bool(attrs & FILE_ATTRIBUTE_DIRECTORY) and not attrs & FILE_ATTRIBUTE_REPARSE_POINT
                   and owner in TRUSTED_OWNERS and bool(control & SE_DACL_PROTECTED))
    except OSError:
        trusted = False
    if not trusted:
        closer(handle)
        return None
    return handle

def create_secure_dir(path: str, sddl: str = SECURE_SDDL) -> None:
    '''CreateDirectoryW with the descriptor attached, so the directory never exists with a weaker one.'''
    from ctypes import wintypes
    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wintypes.BOOL)]
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SECURITY_ATTRIBUTES)]
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p

    SDDL_REVISION_1 = 1
    sd = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, SDDL_REVISION_1, ctypes.byref(sd), None):
        raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    try:
        sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), sd, False)
        if not kernel.CreateDirectoryW(path, ctypes.byref(sa)):
            raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    finally:
        kernel.LocalFree(sd)

def ensure_trusted_dir(path: str, *, remove_links: bool, open_trusted: Callable[[str], Any] = open_trusted_dir,
                       create_dir: Callable[[str], None] = create_secure_dir, reparse: Callable[[str], bool] = is_reparse_point,
                       rename: Callable[[str, str], None] = os.rename, rmdir: Callable[[str], None] = os.rmdir,
                       now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc)) -> Any:
    '''Return an open, validated handle to `path` as a trusted directory.

    Reuse it if it already is one; otherwise move it aside (or drop a link) and create it fresh. A freshly created
    directory is re-opened and validated like any other, and a creation race (already exists) fails closed.'''
    if os.path.lexists(path):
        handle = open_trusted(path)
        if handle is not None:
            return handle
        if remove_links and reparse(path):
            rmdir(path) # Removes only the link, never the target
        else:
            aside = f"{path}.untrusted-{now().strftime('%Y%m%d%H%M%S')}"
            rename(path, aside) # Renames only the link for a junction
            print(f"{path} was not owned and protected by administrators; moved it to {aside}", file=sys.stderr)
    create_dir(path) # Raises if it already exists
    handle = open_trusted(path)
    if handle is None:
        raise OSError(f"{path} is not a trusted directory right after creation")
    return handle

_FOLDERID_PROGRAMDATA = "{62AB5D82-FDC1-4DC3-A9DD-070D1D495D97}"
_FOLDERID_PROGRAMFILES = "{905e63b6-c1bf-494e-b29c-65b732d3d21a}"

def known_program_data() -> str:
    '''ProgramData from the shell known-folder API. The PROGRAMDATA env var is user-controlled and must not be trusted here.'''
    return known_folder(_FOLDERID_PROGRAMDATA)

def known_program_files() -> str:
    '''Program Files from the shell known-folder API, never from the user-controlled ProgramFiles env var.'''
    return known_folder(_FOLDERID_PROGRAMFILES)

def program_dir(program_files: str | None = None) -> str:
    r'''`%ProgramFiles%\Decky Loader`, where installed mode keeps the runtime (`python`) and the program (`app`).'''
    return os.path.join(program_files or known_program_files(), PROGRAM_DIR_NAME)

def system_directory() -> str:
    r'''`%SystemRoot%\System32`, resolved by the OS rather than read from the environment.'''
    buf = ctypes.create_unicode_buffer(260)
    n = int(ctypes.windll.kernel32.GetSystemDirectoryW(buf, len(buf))) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
    if n == 0 or n >= len(buf):
        raise OSError("GetSystemDirectoryW failed")
    return buf.value

def known_folder(folder_id: str) -> str:
    from ctypes import wintypes
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]
    guid = GUID()
    ctypes.oledll.ole32.CLSIDFromString(folder_id, ctypes.byref(guid)) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    out = ctypes.c_wchar_p()
    hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    try:
        if hr != 0 or not out.value:
            raise OSError(f"SHGetKnownFolderPath failed ({hr})")
        return out.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]

def user_in_administrators(run: Callable[..., Any] = subprocess.run) -> bool:
    '''True if the current token lists BUILTIN\\Administrators (deny-only counts: an unelevated admin has it that way).'''
    out = run(["whoami", "/groups", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True).stdout
    return any(_SID_ADMINS in row for row in csv.reader(io.StringIO(out.strip())))

MOVEFILE_DELAY_UNTIL_REBOOT = 0x4

def move_on_reboot(path: str) -> bool:
    return bool(ctypes.windll.kernel32.MoveFileExW(path, None, MOVEFILE_DELAY_UNTIL_REBOOT)) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]

TH32CS_SNAPPROCESS = 0x2
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
TERMINATE_WAIT_MS = 5000
WAIT_OBJECT_0 = 0
ERROR_INVALID_PARAMETER = 87

def process_snapshot() -> list[tuple[int, int]]:
    '''(pid, parent pid) for every process, from one Toolhelp snapshot. Raises OSError if the snapshot fails.'''
    from ctypes import wintypes
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]
    k = _kernel32()
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    k.Process32FirstW.restype = wintypes.BOOL
    k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    k.Process32NextW.restype = wintypes.BOOL

    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap is None or snap == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    out: list[tuple[int, int]] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID)))
            ok = k.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k.CloseHandle(snap)
    return out

def parent_pid(pid: int, snapshot: Callable[[], list[tuple[int, int]]] = process_snapshot) -> int | None:
    '''The parent pid Toolhelp records for `pid`, or None if `pid` is not running.'''
    for p, parent in snapshot():
        if p == pid:
            return parent
    return None

def list_processes() -> list[tuple[int, str | None]]:
    '''(pid, full image path) for every process; the path is None where it cannot be queried.'''
    from ctypes import wintypes
    k = _kernel32()
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = ctypes.c_void_p
    k.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.QueryFullProcessImageNameW.restype = wintypes.BOOL

    out: list[tuple[int, str | None]] = []
    for pid, _parent in process_snapshot():
        image: str | None = None
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            try:
                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buf))
                if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    image = buf.value
            finally:
                k.CloseHandle(h)
        out.append((pid, image))
    return out

def _norm_path(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))

def matching_pids(processes: list[tuple[int, str | None]], exe: str, own: set[int]) -> list[int]:
    '''Pids whose image is exactly `exe`, other than ours (`own`).'''
    target = _norm_path(exe)
    return [pid for pid, image in processes if image is not None and pid not in own and _norm_path(image) == target]

def terminate_pid(pid: int) -> None:
    k = _kernel32()
    from ctypes import wintypes
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = ctypes.c_void_p
    k.TerminateProcess.argtypes = [ctypes.c_void_p, wintypes.UINT]
    k.TerminateProcess.restype = wintypes.BOOL
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    h = k.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
    if not h:
        raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    try:
        if not k.TerminateProcess(h, 1):
            raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        k.WaitForSingleObject(h, TERMINATE_WAIT_MS)
    finally:
        k.CloseHandle(h)

def wait_pid_exit(pid: int, timeout_ms: int = TERMINATE_WAIT_MS) -> bool:
    '''True once `pid` has exited (or no longer exists) within `timeout_ms`.'''
    k = _kernel32()
    from ctypes import wintypes
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = ctypes.c_void_p
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    h = k.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return ctypes.get_last_error() == ERROR_INVALID_PARAMETER
    try:
        return k.WaitForSingleObject(h, timeout_ms) == WAIT_OBJECT_0
    finally:
        k.CloseHandle(h)

def own_ancestry(pid: int, processes: list[tuple[int, int]]) -> set[int]:
    '''`pid` and every ancestor of it that `processes` (pid, parent pid) still knows about.'''
    parents = dict(processes)
    chain = {pid}
    while pid in parents and parents[pid] not in chain and parents[pid] != 0:
        pid = parents[pid]
        chain.add(pid)
    return chain

def terminate_image(exe: str, *, processes: Callable[[], list[tuple[int, str | None]]] = list_processes,
                    terminate: Callable[[int], None] = terminate_pid,
                    snapshot: Callable[[], list[tuple[int, int]]] = process_snapshot,
                    wait_exit: Callable[[int], bool] = wait_pid_exit) -> list[int]:
    '''Kill every process running from `exe` (supervisor, loader, root plugin hosts), except this one and its
    parent chain (the onefile bootloader, and whatever started it). `schtasks /End` only ends the task's own
    process, not what it started. Failing to list processes is logged and skipped rather than raised.'''
    try:
        running = processes()
    except OSError as e:
        logger.warning("Could not list running processes, so copies of %s were not stopped: %s", exe, e)
        return []
    me = os.getpid()
    try:
        own = own_ancestry(me, snapshot())
    except OSError as e:
        logger.warning("Could not read the process tree: %s", e)
        own = set[int]()
    own |= {me, os.getppid()}
    killed: list[int] = []
    for pid in matching_pids(running, exe, own=own):
        try:
            terminate(pid)
            killed.append(pid)
        except OSError as e:
            # Ending the task closes the job, so copies may already be exiting; TerminateProcess then fails with
            # access denied. Only warn about a process that is still running afterwards.
            if wait_exit(pid):
                killed.append(pid)
            else:
                logger.warning("Could not stop process %s: %s", pid, e)
    return killed


COPY_ATTEMPTS = 10

def elevation_params(command: list[str], args: list[str]) -> tuple[str, str]:
    '''(file, parameters) for ShellExecute: `command[0]` runs, and the rest of `command` (the script, when running from
    source) goes before `args`. Every parameter is quoted as needed, so paths with spaces survive.'''
    return command[0], subprocess.list2cmdline(command[1:] + args)

def _elevate(command: list[str], args: list[str], shell_execute: Callable[..., int]) -> int:
    file, params = elevation_params(command, args)
    result = shell_execute(None, "runas", file, params, None, SW_SHOWNORMAL)
    if result <= SHELL_EXECUTE_OK_ABOVE:
        logger.error("Elevation was refused or failed (code %s)", result)
        return 1
    return 0

def _shell_execute(*args: Any) -> int:
    return int(ctypes.windll.shell32.ShellExecuteW(*args)) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]

def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))

INSTALL_COMMAND = r"python\python.exe app\main.py --install"

def frozen_install_message() -> str:
    return ("This PluginLoader.exe is a portable build and cannot be installed. Installed mode runs Decky on a private copy "
            "of the signed Python runtime: build the installer folder with tools\\update-decky.ps1 -NoInstall (or "
            f"tools\\build_win_runtime.py), then run {INSTALL_COMMAND} from that folder.")

def not_a_stage_message(exe: str, script: str | None) -> str:
    return (f"--install only works from an installer folder (with python and app folders, built by tools\\build_win_runtime.py): "
            f"run {INSTALL_COMMAND} from that folder. This copy runs {script or '(no script)'} with {exe}.")

def stage_root(exe: str, script: str | None, *, isdir: Callable[[str], bool] = os.path.isdir) -> str | None:
    r'''The installer folder this copy runs from: `exe` is `<folder>\python\python.exe` (or pythonw.exe), `script` is
    `<folder>\app\main.py`, and both folders exist. None if this copy is not running from one.'''
    if not script:
        return None
    root = os.path.dirname(os.path.dirname(os.path.abspath(exe)))
    if not any(_same_file(exe, i) for i in runtime_interpreters(root)) or not _same_file(script, main_script_path(root)):
        return None
    if not all(isdir(os.path.join(root, sub)) for sub in PROGRAM_SUBDIRS):
        return None
    return root

def _copy_file_plain(src: str, dst: str) -> str:
    # Content and timestamps only. The copy gets the ACL it inherits where it lands (Program Files: Users read and
    # execute), never the source's: copy2 (CopyFile2) is not used, and nothing copies security descriptors.
    shutil.copyfile(src, dst)
    shutil.copystat(src, dst)
    return dst

def copy_tree_plain(src: str, dst: str) -> object:
    '''Copy the folder `src` to the new folder `dst`, file contents and timestamps only (see _copy_file_plain).'''
    return shutil.copytree(src, dst, copy_function=_copy_file_plain)

def remove_tree(path: str, *, rmtree: Callable[..., Any] = shutil.rmtree, defer_delete: Callable[[str], bool] = move_on_reboot,
                reparse: Callable[[str], bool] = is_reparse_point, rmdir: Callable[[str], None] = os.rmdir) -> list[str]:
    '''Delete `path` if it exists. Locked files are scheduled for deletion at reboot. A junction or symlink is removed
    as a link only, never followed. Returns what could neither be deleted nor scheduled.'''
    if not os.path.lexists(path):
        return []
    if reparse(path):
        try:
            rmdir(path)
            return []
        except OSError as e:
            logger.warning("Could not remove the link %s: %s", path, e)
            return [path]

    failed: list[str] = []

    def on_error(func: Any, p: str, exc: Any) -> None:
        if defer_delete(p):
            logger.warning("Locked, will be deleted at reboot: %s", p)
        else:
            failed.append(p)

    kw = "onexc" if sys.version_info >= (3, 12) else "onerror"
    rmtree(path, **{kw: on_error})
    return failed

def remove_legacy_layout(root: str, **remove_kw: Any) -> list[str]:
    r'''Remove `%ProgramData%\decky\services` and `\tmp` left by the earlier onefile install. Plugins and settings stay.'''
    failed: list[str] = []
    for sub in LEGACY_DIRS:
        failed += remove_tree(os.path.join(root, sub), **remove_kw)
    return failed

def stale_copies(app_dir: str, *, listdir: Callable[[str], list[str]] = os.listdir) -> list[str]:
    '''Staging (`<app_dir>.installing-*`) and previous (`<app_dir>.old-*`) copies left next to `app_dir` by
    earlier installs that were interrupted or could not delete them.'''
    parent, name = os.path.split(app_dir)
    prefixes = (f"{name}.installing-".lower(), f"{name}.old-".lower())
    try:
        entries = listdir(parent)
    except OSError:
        return []
    return [os.path.join(parent, e) for e in entries if e.lower().startswith(prefixes)]

def stage_and_swap(src_root: str, app_dir: str, *, copytree: Callable[[str, str], object] = copy_tree_plain,
                   mkdir: Callable[[str], None] = os.mkdir,
                   rename: Callable[[str, str], None] = os.rename, sleep: Callable[[float], None] = time.sleep,
                   discard: Callable[[str], list[str]] = remove_tree, listdir: Callable[[str], list[str]] = os.listdir,
                   token: Callable[[], str] = lambda: secrets.token_hex(8)) -> bool:
    r'''Copy the installer folder's `python` and `app` folders (and nothing else from `src_root`) into a new sibling
    of `app_dir`, then swap it in: the current `app_dir` is renamed aside, the staged copy renamed into place, and the
    old copy deleted, so whatever an earlier install left in `app_dir` (`PluginLoader.exe`, `_internal`) goes with it.
    Nothing in an existing `app_dir` is written in place. On failure prints why, keeps (or restores) the current
    `app_dir`, and returns False. Copies left behind by earlier installs are removed first, best effort (`discard`
    removes a link as a link only).'''
    for leftover in stale_copies(app_dir, listdir=listdir):
        for p in discard(leftover):
            logger.warning("Could not remove a copy left by an earlier install: %s", p)

    parent, name = os.path.split(app_dir)
    tag = token()
    staged = os.path.join(parent, f"{name}.installing-{tag}")
    old = os.path.join(parent, f"{name}.old-{tag}")
    try:
        mkdir(staged) # Raises if it already exists
    except OSError as e:
        # Not ours to delete: it was not created by this run
        print(f"Could not create {staged}: {e}", file=sys.stderr)
        return False
    try:
        for sub in PROGRAM_SUBDIRS:
            copytree(os.path.join(src_root, sub), os.path.join(staged, sub))
    except OSError as e: # shutil.Error (copytree's collected failures) included
        print(f"Could not copy Decky Loader to {staged}: {e}", file=sys.stderr)
        discard(staged)
        return False

    moved_aside = False
    for attempt in range(COPY_ATTEMPTS):
        try:
            if not moved_aside and os.path.lexists(app_dir):
                rename(app_dir, old)
                moved_aside = True
            rename(staged, app_dir)
            break
        except OSError as e:
            if isinstance(e, PermissionError) and attempt < COPY_ATTEMPTS - 1:
                sleep(1.0)
                continue
            if isinstance(e, PermissionError):
                print(f"Could not replace {app_dir}: it is still in use. Close Decky Loader and try again.", file=sys.stderr)
            else:
                print(f"Could not replace {app_dir}: {e}", file=sys.stderr)
            if moved_aside:
                try:
                    rename(old, app_dir)
                except OSError as e2:
                    print(f"Could not move the previous copy back from {old} to {app_dir}: {e2}", file=sys.stderr)
            discard(staged)
            return False

    if moved_aside:
        for p in discard(old):
            logger.warning("Could not remove the previous copy: %s", p)
    return True

NOT_MIGRATED_NOTICE =r"Plugins in %USERPROFILE%\homebrew\plugins are not migrated. Reinstall them from the Decky store."

# Steam's CEF only opens its DevTools port (127.0.0.1:8080), which Decky injects through, while this file exists
CEF_DEBUG_MARKER = ".cef-enable-remote-debugging"

def read_registry_value(hive: int, key: str, value: str) -> str | None:
    import winreg
    try:
        with winreg.OpenKey(hive, key) as k:
            return str(winreg.QueryValueEx(k, value)[0])
    except OSError:
        return None

def steam_dir_for_sid(sid: str, *, read: Callable[[int, str, str], str | None] = read_registry_value,
                      isfile: Callable[[str], bool] = os.path.isfile) -> str | None:
    r'''The Steam install of the user `sid`: their own `SteamPath` (from `HKEY_USERS\<sid>`, since this runs elevated
    and HKCU may belong to whoever approved the UAC prompt), else the machine-wide InstallPath. Must hold steam.exe.'''
    import winreg
    candidates: list[tuple[int, str, str]] = [
        (winreg.HKEY_USERS, sid + r"\Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for hive, key, value in candidates:
        raw = read(hive, key, value)
        if not raw:
            continue
        path = os.path.normpath(raw)
        if os.path.isabs(path) and isfile(os.path.join(path, "steam.exe")):
            return path
    return None

def _path_has_reparse_point(path: str, reparse: Callable[[str], bool]) -> bool:
    '''True if `path` or any directory above it is a junction or symlink.'''
    while True:
        if reparse(path):
            return True
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent

def _create_new_empty(path: str) -> None:
    # O_EXCL is CREATE_NEW: fails if anything, a link included, already has the name; nothing is followed or overwritten
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)))

def final_path_of_handle(handle: Any) -> str:
    '''The path the OS resolved for an open handle, with every link along the way followed (extended-length form).'''
    from ctypes import wintypes
    k = _kernel32()
    k.GetFinalPathNameByHandleW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    k.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    buf = ctypes.create_unicode_buffer(32768)
    n = k.GetFinalPathNameByHandleW(handle, buf, len(buf), 0)
    if n == 0 or n >= len(buf):
        raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    return buf.value

GENERIC_READ = 0x80000000

def final_path_of_file(path: str) -> str:
    '''Final path of the file at `path`, opened without following a final reparse point.'''
    k = _kernel32()
    handle = k.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING,
                           FILE_FLAG_OPEN_REPARSE_POINT, None)
    if handle is None or handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
    try:
        return final_path_of_handle(handle)
    finally:
        k.CloseHandle(handle)

def enable_cef_remote_debugging(sid: str, *, steam_dir: Callable[[str], str | None] = steam_dir_for_sid,
                                reparse: Callable[[str], bool] = is_reparse_point, opener: Callable[[str], Any] = open_dir,
                                query: Callable[[Any], tuple[int, str, int]] = query_dir,
                                closer: Callable[[Any], None] = close_handle,
                                create: Callable[[str], None] = _create_new_empty,
                                folder_path: Callable[[Any], str] = final_path_of_handle,
                                file_path: Callable[[str], str] = final_path_of_file,
                                remove: Callable[[str], None] = os.remove) -> str | None:
    r'''Create the empty `<Steam>\.cef-enable-remote-debugging` if missing, so Steam's CEF debugger (which Decky needs)
    is on. Best effort: prints a note instead of failing the install. Never follows a reparse point: the Steam folder
    is user-writable and this runs elevated. Returns the marker path if it was created.'''
    try:
        steam = steam_dir(sid)
        if steam is None:
            print(f"Steam was not found, so {CEF_DEBUG_MARKER} was not created. Decky needs it: create an empty file "
                  f"with that name in the Steam folder.")
            return None
        marker = os.path.join(steam, CEF_DEBUG_MARKER)
        if os.path.lexists(marker):
            return None
        if _path_has_reparse_point(steam, reparse):
            print(f"Not creating {marker}: the Steam folder path contains a junction or symbolic link. "
                  f"Create the empty file yourself if Decky does not load.")
            return None
        # Held without share-delete while the file is created, so the folder (and so its path) cannot be swapped
        handle = opener(steam)
        try:
            attrs, _owner, _control = query(handle)
            if not attrs & FILE_ATTRIBUTE_DIRECTORY or attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                print(f"Not creating {marker}: {steam} is not a plain folder.")
                return None
            create(marker)
            # Belt and braces: the new file must really be inside the folder we hold, else delete what we made
            created = file_path(marker)
            if os.path.normcase(os.path.dirname(created)) != os.path.normcase(folder_path(handle)):
                remove(created)
                print(f"Not creating {marker}: it resolved to {created}, outside {steam}. Removed it.")
                return None
        finally:
            closer(handle)
        print(f"Created {marker} (Steam's CEF debugger, which Decky uses). It takes effect the next time Steam starts.")
        return marker
    except FileExistsError:
        return None
    except Exception as e:
        print(f"Could not create {CEF_DEBUG_MARKER} in the Steam folder: {e}. Decky needs it: create an empty file "
              f"with that name in the Steam folder.")
        return None

def install(for_sid: str | None, *, is_admin: Callable[[], bool] = is_admin, run: Callable[..., Any] = subprocess.run,
            shell_execute: Callable[..., int] = _shell_execute, current_sid: Callable[[], str] = current_user_sid,
            is_admin_member: Callable[[], bool] = user_in_administrators, frozen: Callable[[], bool] = _is_frozen,
            exe: str | None = None, script: str | None = None, program_data: str | None = None,
            program_files: str | None = None, isdir: Callable[[str], bool] = os.path.isdir,
            copytree: Callable[[str, str], object] = copy_tree_plain,
            mkdir: Callable[[str], None] = os.mkdir, rmtree: Callable[..., Any] = shutil.rmtree,
            defer_delete: Callable[[str], bool] = move_on_reboot,
            sleep: Callable[[float], None] = time.sleep, open_trusted: Callable[[str], Any] = open_trusted_dir,
            close_dir: Callable[[Any], None] = close_handle,
            create_dir: Callable[[str], None] = create_secure_dir, rename: Callable[[str, str], None] = os.rename,
            rmdir: Callable[[str], None] = os.rmdir, reparse: Callable[[str], bool] = is_reparse_point,
            system_dir: Callable[[], str] = system_directory, kill_running: Callable[[str], list[int]] = terminate_image,
            cef_debug: Callable[[str], object] = enable_cef_remote_debugging) -> int:
    exe = exe or sys.executable
    # Checked before elevating, so the message shows up in the console the user typed into
    if frozen():
        print(frozen_install_message(), file=sys.stderr)
        return 1
    script = script or running_main_script()
    src = stage_root(exe, script, isdir=isdir)
    if src is None or script is None:
        print(not_a_stage_message(exe, script), file=sys.stderr)
        return 1
    if not is_admin():
        if not is_admin_member():
            print("Decky's installed mode requires an administrator account; use portable mode instead", file=sys.stderr)
            return 1
        # Resolve the SID before elevating so the task targets this user even if UAC credentials belong to another admin.
        sid = for_sid or current_sid()
        return _elevate([exe, script], ["--install", "--for-sid", sid], shell_execute)

    sid = for_sid or current_sid()
    root = os.path.join(program_data or known_program_data(), "decky")
    plugins_dst = os.path.join(root, "plugins")
    app_dir = program_dir(program_files)
    schtasks = os.path.join(system_dir(), "schtasks.exe")
    remove_kw: dict[str, Any] = dict(rmtree=rmtree, defer_delete=defer_delete, reparse=reparse, rmdir=rmdir)

    # Program Files' own ACL already keeps non-admins out, so only refuse a link planted in its place
    if reparse(app_dir):
        print(f"Cannot install to {app_dir}: it is a junction or symbolic link. Remove it and try again.", file=sys.stderr)
        return 1

    # Stop a running elevated loader first, it locks the installed files. Ending the task only ends its first
    # process, so also stop everything else still running from the installed runtime, or from an earlier layout's exe.
    run([schtasks, "/End", "/TN", TASK_NAME], check=False)
    for image in program_images(app_dir, root):
        kill_running(image)

    root_handle: Any = None
    try:
        # Every directory is created with the protected descriptor attached, and anything pre-existing that is not
        # already admin-owned and protected is moved aside rather than trusted. The root handle stays open (no
        # share-delete) for the rest of the install, so the validated directory cannot be swapped out from under us.
        kw: dict[str, Any] = dict(open_trusted=open_trusted, create_dir=create_dir, reparse=reparse, rename=rename, rmdir=rmdir)
        try:
            root_handle = ensure_trusted_dir(root, remove_links=False, **kw)
            close_dir(ensure_trusted_dir(plugins_dst, remove_links=True, **kw))
        except OSError as e:
            print(f"Could not prepare {root}: {e}", file=sys.stderr)
            return 1

        # Run from the installed copy itself: there is nothing to copy
        if not _same_file(src, app_dir):
            if not stage_and_swap(src, app_dir, copytree=copytree, mkdir=mkdir, rename=rename,
                                  sleep=sleep, discard=lambda p: remove_tree(p, **remove_kw)):
                return 1

        # Only once the new program is in place: a failed install leaves the earlier layout working
        for p in remove_legacy_layout(root, **remove_kw):
            print(f"Could not remove {p} from the earlier install layout", file=sys.stderr)

        # The task XML lives in the protected root, not a user-writable temp directory
        fd, xml_path = tempfile.mkstemp(suffix=".xml", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-16") as f: # utf-16 writes a BOM
                f.write(build_task_xml(os.path.join(app_dir, RUNTIME_DIR_NAME, WINDOWED_INTERPRETER), app_dir, sid,
                                       subprocess.list2cmdline([main_script_path(app_dir)])))
            run([schtasks, "/Create", "/TN", TASK_NAME, "/XML", xml_path, "/F"], check=True)
        finally:
            try:
                os.remove(xml_path)
            except FileNotFoundError:
                pass
            except OSError:
                logger.exception("Could not remove task file %s", xml_path)
        run([schtasks, "/Run", "/TN", TASK_NAME], check=True)
    finally:
        if root_handle is not None:
            close_dir(root_handle)

    # For the user the task runs as. Uninstall leaves the file in place.
    cef_debug(sid)
    print(NOT_MIGRATED_NOTICE)
    return 0

def uninstall(*, is_admin: Callable[[], bool] = is_admin, run: Callable[..., Any] = subprocess.run,
              shell_execute: Callable[..., int] = _shell_execute, exe: str | None = None, script: str | None = None,
              frozen: Callable[[], bool] = _is_frozen,
              program_data: str | None = None, program_files: str | None = None, rmtree: Callable[..., Any] = shutil.rmtree,
              defer_delete: Callable[[str], bool] = move_on_reboot,
              kill_running: Callable[[str], list[int]] = terminate_image,
              system_dir: Callable[[], str] = system_directory, reparse: Callable[[str], bool] = is_reparse_point,
              rmdir: Callable[[str], None] = os.rmdir, open_trusted: Callable[[str], Any] = open_trusted_dir,
              close_dir: Callable[[Any], None] = close_handle) -> int:
    if not is_admin():
        exe = exe or sys.executable
        script = None if frozen() else (script or running_main_script())
        return _elevate([exe] + ([script] if script else []), ["--uninstall"], shell_execute)

    root = os.path.join(program_data or known_program_data(), "decky")
    app_dir = program_dir(program_files)
    schtasks = os.path.join(system_dir(), "schtasks.exe")
    run([schtasks, "/End", "/TN", TASK_NAME], check=False)
    for image in program_images(app_dir, root):
        kill_running(image)
    run([schtasks, "/Delete", "/TN", TASK_NAME, "/F"], check=False)

    # Plugins and settings in the root are left alone
    remove_kw: dict[str, Any] = dict(rmtree=rmtree, defer_delete=defer_delete, reparse=reparse, rmdir=rmdir)
    failed = remove_tree(app_dir, **remove_kw)
    if os.path.lexists(root):
        # Same check as install: never delete inside a root that is not admin-owned and protected (it could be a
        # junction or a directory a user prepared). The handle stays open so the root cannot be swapped meanwhile.
        root_handle = open_trusted(root)
        if root_handle is None:
            print(f"Not removing the earlier install layout from {root}: it is not owned and protected by administrators",
                  file=sys.stderr)
        else:
            try:
                failed += remove_legacy_layout(root, **remove_kw)
            finally:
                close_dir(root_handle)

    if failed:
        print("Could not remove: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0
