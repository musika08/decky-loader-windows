'''Windows install: registers an elevated logon task and lays out the admin-only install directory.

Layout: `%ProgramData%\\decky\\services\\PluginLoader.exe` (privileged root `%ProgramData%\\decky`).
'''
import csv, ctypes, io, logging, os, shutil, subprocess, sys, tempfile
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

def _program_data() -> str:
    return os.environ.get("PROGRAMDATA", r"C:\ProgramData")

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

def install(for_sid: str | None, *, is_admin: Callable[[], bool] = is_admin, run: Callable[..., Any] = subprocess.run,
            shell_execute: Callable[..., int] = _shell_execute, current_sid: Callable[[], str] = current_user_sid,
            exe: str | None = None, program_data: str | None = None, home: str | None = None,
            temp_dir: str | None = None) -> int:
    exe = exe or sys.executable
    if not is_admin():
        # Resolve the SID before elevating so the task targets this user even if UAC credentials belong to another admin.
        sid = for_sid or current_sid()
        return _elevate(f"--install --for-sid {sid}", shell_execute, exe)

    sid = for_sid or current_sid()
    root = os.path.join(program_data or _program_data(), "decky")
    services = os.path.join(root, "services")
    installed_exe = os.path.join(services, EXE_NAME)
    os.makedirs(services, exist_ok=True)
    shutil.copyfile(exe, installed_exe)

    plugins_src = os.path.join(home or os.path.expanduser("~"), "homebrew", "plugins")
    plugins_dst = os.path.join(root, "plugins")
    if os.path.isdir(plugins_src) and not os.path.exists(plugins_dst):
        shutil.copytree(plugins_src, plugins_dst)

    for cmd in acl_commands(root):
        run(cmd, check=True)

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
              program_data: str | None = None, rmtree: Callable[[str], object] = lambda p: shutil.rmtree(p, ignore_errors=True)) -> int:
    if not is_admin():
        return _elevate("--uninstall", shell_execute, exe or sys.executable)

    run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)
    services = os.path.join(program_data or _program_data(), "decky", "services")
    if os.path.isdir(services):
        rmtree(services) # The running exe may be inside; whatever is locked is left behind
    return 0
