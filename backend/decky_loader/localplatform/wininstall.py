'''Windows install: registers an elevated logon task and lays out the admin-only install directory.

Layout: `%ProgramData%\\decky\\services\\PluginLoader.exe` (privileged root `%ProgramData%\\decky`).
'''
import csv, ctypes, datetime, io, logging, os, secrets, shutil, subprocess, sys, tempfile, time
from typing import Any, Callable
from xml.sax.saxutils import escape

from .winsupervisor import self_command

logger = logging.getLogger("wininstall")

TASK_NAME = "Decky Loader"
EXE_NAME = "PluginLoader.exe"

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

def build_task_xml(exe: str, workdir: str, user_sid: str) -> str:
    sid, exe, workdir = escape(user_sid), escape(exe), escape(workdir)
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
      <Command>{exe}</Command>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'''

# Applied at directory creation, so there is no window where the directory is writable by anyone else.
# Owner and group: Administrators. Protected DACL: SYSTEM full, Administrators full, Users read/execute.
SECURE_SDDL = "O:BAG:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)"

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
SE_DACL_PROTECTED = 0x1000
TRUSTED_OWNERS = (_SID_ADMINS, _SID_SYSTEM)

def is_reparse_point(path: str) -> bool:
    '''Junctions and symlinks. Never follows the link.'''
    try:
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return False

def _owner_and_control(path: str) -> tuple[str, int]:
    '''(owner SID string, security descriptor control flags) of a path.'''
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                                             ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                                             ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL

    SE_FILE_OBJECT, OWNER_INFO, DACL_INFO = 1, 1, 4
    owner, dacl, sd = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    rc = advapi.GetNamedSecurityInfoW(path, SE_FILE_OBJECT, OWNER_INFO | DACL_INFO, ctypes.byref(owner), None,
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
            kernel.LocalFree(sid_str)
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not advapi.GetSecurityDescriptorControl(sd, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error()) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
        return owner_sid, int(control.value)
    finally:
        kernel.LocalFree(sd)

def is_trusted_dir(path: str, *, reparse: Callable[[str], bool] = is_reparse_point,
                   owner_and_control: Callable[[str], tuple[str, int]] = _owner_and_control) -> bool:
    '''Exists, is a real directory (not a link), owned by Administrators or SYSTEM, with a protected DACL.'''
    if not os.path.lexists(path) or reparse(path) or not os.path.isdir(path):
        return False
    try:
        owner, control = owner_and_control(path)
    except OSError:
        return False
    return owner in TRUSTED_OWNERS and bool(control & SE_DACL_PROTECTED)

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

def ensure_trusted_dir(path: str, *, remove_links: bool, is_trusted: Callable[[str], bool] = is_trusted_dir,
                       create_dir: Callable[[str], None] = create_secure_dir, reparse: Callable[[str], bool] = is_reparse_point,
                       rename: Callable[[str, str], None] = os.rename, rmdir: Callable[[str], None] = os.rmdir,
                       now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc)) -> None:
    '''Leave `path` as a trusted directory: reuse it if it already is, otherwise move it aside (or drop a link) and recreate it.'''
    if os.path.lexists(path):
        if is_trusted(path):
            return
        if remove_links and reparse(path):
            rmdir(path) # Removes only the link, never the target
        else:
            aside = f"{path}.untrusted-{now().strftime('%Y%m%d%H%M%S')}"
            rename(path, aside) # Renames only the link for a junction
            print(f"{path} was not owned and protected by administrators; moved it to {aside}", file=sys.stderr)
    create_dir(path)

def _skip_links(directory: str, names: list[str]) -> list[str]:
    return [n for n in names if is_reparse_point(os.path.join(directory, n))]

def migrate_plugins(src: str, dst: str, reparse: Callable[[str], bool] = is_reparse_point) -> None:
    '''Copy plugins as admin without ever following a user-created link out of the source tree.'''
    if not os.path.isdir(src) or reparse(src) or os.listdir(dst):
        return
    shutil.copytree(src, dst, ignore=_skip_links, dirs_exist_ok=True)
_FOLDERID_PROGRAMDATA = "{62AB5D82-FDC1-4DC3-A9DD-070D1D495D97}"

def known_program_data() -> str:
    '''ProgramData from the shell known-folder API. The PROGRAMDATA env var is user-controlled and must not be trusted here.'''
    from ctypes import wintypes
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]
    guid = GUID()
    ctypes.oledll.ole32.CLSIDFromString(_FOLDERID_PROGRAMDATA, ctypes.byref(guid)) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
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

def profile_path_for_sid(sid: str) -> str | None:
    import winreg # pyright: ignore [reportMissingImports]
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList" + "\\" + sid) as key: # pyright: ignore [reportUnknownMemberType]
            value, _ = winreg.QueryValueEx(key, "ProfileImagePath") # pyright: ignore [reportUnknownMemberType, reportUnknownVariableType]
            return os.path.expandvars(str(value)) # pyright: ignore [reportUnknownArgumentType]
    except OSError:
        return None

MOVEFILE_DELAY_UNTIL_REBOOT = 0x4

def move_on_reboot(path: str) -> bool:
    return bool(ctypes.windll.kernel32.MoveFileExW(path, None, MOVEFILE_DELAY_UNTIL_REBOOT)) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]

COPY_ATTEMPTS = 10

def _elevate(args: str, shell_execute: Callable[..., int], exe: str | None) -> int:
    cmd = self_command()
    if exe is not None and not getattr(sys, "frozen", False):
        cmd = [exe] + cmd[1:]
    file = cmd[0]
    params = " ".join(([subprocess.list2cmdline(cmd[1:])] if cmd[1:] else []) + [args])
    result = shell_execute(None, "runas", file, params, None, SW_SHOWNORMAL)
    if result <= SHELL_EXECUTE_OK_ABOVE:
        logger.error("Elevation was refused or failed (code %s)", result)
        return 1
    return 0

def _shell_execute(*args: Any) -> int:
    return int(ctypes.windll.shell32.ShellExecuteW(*args)) # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]

def _same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))

def install(for_sid: str | None, *, is_admin: Callable[[], bool] = is_admin, run: Callable[..., Any] = subprocess.run,
            shell_execute: Callable[..., int] = _shell_execute, current_sid: Callable[[], str] = current_user_sid,
            is_admin_member: Callable[[], bool] = user_in_administrators,
            profile_for_sid: Callable[[str], str | None] = profile_path_for_sid,
            exe: str | None = None, program_data: str | None = None,
            copyfile: Callable[[str, str], object] = shutil.copyfile, replace: Callable[[str, str], None] = os.replace,
            sleep: Callable[[float], None] = time.sleep, is_trusted: Callable[[str], bool] = is_trusted_dir,
            create_dir: Callable[[str], None] = create_secure_dir, rename: Callable[[str, str], None] = os.rename,
            rmdir: Callable[[str], None] = os.rmdir, reparse: Callable[[str], bool] = is_reparse_point) -> int:
    exe = exe or sys.executable
    if not is_admin():
        if not is_admin_member():
            print("Decky's installed mode requires an administrator account; use portable mode instead", file=sys.stderr)
            return 1
        # Resolve the SID before elevating so the task targets this user even if UAC credentials belong to another admin.
        sid = for_sid or current_sid()
        return _elevate(f"--install --for-sid {sid}", shell_execute, exe)

    sid = for_sid or current_sid()
    root = os.path.join(program_data or known_program_data(), "decky")
    services = os.path.join(root, "services")
    plugins_dst = os.path.join(root, "plugins")
    installed_exe = os.path.join(services, EXE_NAME)

    # Stop a running elevated loader first, it locks the installed exe
    run(["schtasks", "/End", "/TN", TASK_NAME], check=False)

    # Every directory is created with the protected descriptor attached, and anything pre-existing that is not
    # already admin-owned and protected is moved aside rather than trusted.
    try:
        kw: dict[str, Any] = dict(is_trusted=is_trusted, create_dir=create_dir, reparse=reparse, rename=rename, rmdir=rmdir)
        ensure_trusted_dir(root, remove_links=False, **kw)
        ensure_trusted_dir(services, remove_links=True, **kw)
        ensure_trusted_dir(plugins_dst, remove_links=True, **kw)
    except OSError as e:
        print(f"Could not prepare {root}: {e}", file=sys.stderr)
        return 1

    if not (os.path.exists(installed_exe) and _same_file(exe, installed_exe)):
        # Write a brand new file inside the protected directory, then swap it in. An existing exe is never opened for writing.
        staged = f"{installed_exe}.installing-{secrets.token_hex(8)}"
        try:
            copyfile(exe, staged)
            for attempt in range(COPY_ATTEMPTS):
                try:
                    replace(staged, installed_exe)
                    break
                except PermissionError:
                    if attempt == COPY_ATTEMPTS - 1:
                        print(f"Could not replace {installed_exe}: it is still in use. Close Decky Loader and try again.", file=sys.stderr)
                        return 1
                    sleep(1.0)
        finally:
            try:
                os.remove(staged)
            except OSError:
                pass

    profile = profile_for_sid(sid)
    if profile is not None:
        migrate_plugins(os.path.join(profile, "homebrew", "plugins"), plugins_dst, reparse)

    # The task XML lives in the protected root, not a user-writable temp directory
    fd, xml_path = tempfile.mkstemp(suffix=".xml", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as f: # utf-16 writes a BOM
            f.write(build_task_xml(installed_exe, services, sid))
        run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", xml_path, "/F"], check=True)
    finally:
        try:
            os.remove(xml_path)
        except OSError:
            pass
    run(["schtasks", "/Run", "/TN", TASK_NAME], check=True)
    return 0

def uninstall(*, is_admin: Callable[[], bool] = is_admin, run: Callable[..., Any] = subprocess.run,
              shell_execute: Callable[..., int] = _shell_execute, exe: str | None = None,
              program_data: str | None = None, rmtree: Callable[..., Any] = shutil.rmtree,
              defer_delete: Callable[[str], bool] = move_on_reboot) -> int:
    if not is_admin():
        return _elevate("--uninstall", shell_execute, exe or sys.executable)

    run(["schtasks", "/End", "/TN", TASK_NAME], check=False)
    run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)

    services = os.path.join(program_data or known_program_data(), "decky", "services")
    failed: list[str] = []

    def on_error(func: Any, path: str, exc: Any) -> None:
        if defer_delete(path):
            logger.warning("Locked, will be deleted at reboot: %s", path)
        else:
            failed.append(path)

    if os.path.isdir(services):
        kw = "onexc" if sys.version_info >= (3, 12) else "onerror"
        rmtree(services, **{kw: on_error})

    if failed:
        print("Could not remove: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0
