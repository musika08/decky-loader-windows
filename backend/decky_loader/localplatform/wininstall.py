'''Windows install: registers an elevated logon task and lays out the admin-only install directory.

Layout: `%ProgramData%\\decky\\services\\PluginLoader.exe` (privileged root `%ProgramData%\\decky`).
'''
import csv, ctypes, io, logging, os, shutil, subprocess, sys, tempfile, time
from typing import Any, Callable
from xml.sax.saxutils import escape

from .winsupervisor import self_command

logger = logging.getLogger("wininstall")

TASK_NAME = "Decky Loader"
EXE_NAME = "PluginLoader.exe"

# SYSTEM, Administrators, Users
_SID_SYSTEM = "*S-1-5-18"
_SID_ADMINS = "*S-1-5-32-544"
_SID_USERS = "*S-1-5-32-545"

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

def acl_commands(root: str) -> list[list[str]]:
    '''Lock the install root to SYSTEM and Administrators (full) and Users (read/execute only).'''
    return [["icacls", root, "/inheritance:r", "/grant:r",
             f"{_SID_SYSTEM}:(OI)(CI)F", f"{_SID_ADMINS}:(OI)(CI)F", f"{_SID_USERS}:(OI)(CI)RX"]]

def acl_setup_commands(root: str) -> list[list[str]]:
    '''Full ordered ACL lockdown. Ownership and inherited or pre-planted ACEs are reset first, so a user who
    pre-created the directory cannot keep implicit WRITE_DAC through ownership.'''
    return [["icacls", root, "/setowner", _SID_ADMINS, "/T", "/C"],
            ["icacls", root, "/reset", "/T", "/C"],
            *acl_commands(root)]

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
    return any(_SID_ADMINS[1:] in row for row in csv.reader(io.StringIO(out.strip())))

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
            temp_dir: str | None = None, copyfile: Callable[[str, str], object] = shutil.copyfile,
            sleep: Callable[[float], None] = time.sleep) -> int:
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
    installed_exe = os.path.join(services, EXE_NAME)

    # Stop a running elevated loader first, it locks the installed exe
    run(["schtasks", "/End", "/TN", TASK_NAME], check=False)

    # Lock the root down before anything is created or copied into it
    os.makedirs(root, exist_ok=True)
    for cmd in acl_setup_commands(root):
        run(cmd, check=True)

    os.makedirs(services, exist_ok=True)
    if not (os.path.exists(installed_exe) and _same_file(exe, installed_exe)):
        for attempt in range(COPY_ATTEMPTS):
            try:
                copyfile(exe, installed_exe)
                break
            except PermissionError:
                if attempt == COPY_ATTEMPTS - 1:
                    print(f"Could not replace {installed_exe}: it is still in use. Close Decky Loader and try again.", file=sys.stderr)
                    return 1
                sleep(1.0)

    profile = profile_for_sid(sid)
    plugins_dst = os.path.join(root, "plugins")
    if profile is not None:
        plugins_src = os.path.join(profile, "homebrew", "plugins")
        if os.path.isdir(plugins_src) and not os.path.exists(plugins_dst):
            shutil.copytree(plugins_src, plugins_dst)

    fd, xml_path = tempfile.mkstemp(suffix=".xml", dir=temp_dir)
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
