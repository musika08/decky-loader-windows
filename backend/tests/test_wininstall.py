import os, subprocess, sys
import xml.etree.ElementTree as ET
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    from decky_loader.localplatform import wininstall as wi
    from decky_loader.localplatform import localplatformwin as lpw

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
SID = "S-1-5-21-1-2-3-1001"


def test_task_xml_runs_highest_at_user_logon():
    xml = wi.build_task_xml(r"C:\ProgramData\decky\services\PluginLoader.exe", r"C:\ProgramData\decky\services", SID)
    root = ET.fromstring(xml)

    def text(path: str) -> str | None:
        return root.findtext(path, namespaces=NS)

    assert text("t:Principals/t:Principal/t:RunLevel") == "HighestAvailable"
    assert text("t:Triggers/t:LogonTrigger/t:UserId") == SID
    assert text("t:Principals/t:Principal/t:UserId") == SID
    assert text("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text("t:Actions/t:Exec/t:Command") == r"C:\ProgramData\decky\services\PluginLoader.exe"
    assert text("t:Actions/t:Exec/t:WorkingDirectory") == r"C:\ProgramData\decky\services"
    assert text("t:Settings/t:ExecutionTimeLimit") == "PT0S"
    assert text("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert text("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert text("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert root.get("version") == "1.2"


def test_task_xml_escapes_paths():
    xml = wi.build_task_xml(r"C:\A&B\PluginLoader.exe", r"C:\A&B", SID)
    root = ET.fromstring(xml)
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == r"C:\A&B\PluginLoader.exe"


def test_acl_commands():
    assert wi.acl_commands(r"C:\ProgramData\decky") == [[
        "icacls", r"C:\ProgramData\decky", "/inheritance:r", "/grant:r",
        "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-32-545:(OI)(CI)RX",
    ]]


def test_current_user_sid_parses_whoami():
    sid = wi.current_user_sid()
    assert sid.startswith("S-1-5-")


def test_current_user_sid_parsing_with_injected_runner():
    class R:
        stdout = '"DOMAIN\\user","S-1-5-21-9-9-9-1000"\r\n'
    assert wi.current_user_sid(run=lambda *a, **k: R()) == "S-1-5-21-9-9-9-1000"


def _fake_env(tmp_path):
    home = tmp_path / "home"
    (home / "homebrew" / "plugins" / "p1").mkdir(parents=True)
    (home / "homebrew" / "plugins" / "p1" / "plugin.json").write_text("{}")
    src_exe = tmp_path / "src" / "PluginLoader.exe"
    src_exe.parent.mkdir()
    src_exe.write_bytes(b"exe")
    return home, src_exe


class Recorder:
    def __init__(self):
        self.runs: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.runs.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)


def test_install_issues_exact_schtasks_and_icacls_sequence(tmp_path):
    home, src_exe = _fake_env(tmp_path)
    pd = tmp_path / "ProgramData"
    rec = Recorder()
    elevations: list[tuple] = []

    rc = wi.install(SID, is_admin=lambda: True, run=rec, shell_execute=lambda *a: elevations.append(a),
                    exe=str(src_exe), program_data=str(pd), home=str(home), temp_dir=str(tmp_path))

    root = str(pd / "decky")
    installed = os.path.join(root, "services", "PluginLoader.exe")
    assert rc == 0
    assert elevations == []
    assert os.path.isfile(installed)
    assert os.path.isfile(os.path.join(root, "plugins", "p1", "plugin.json"))

    assert len(rec.runs) == 3
    assert rec.runs[0] == wi.acl_commands(root)[0]
    create = rec.runs[1]
    assert create[:4] == ["schtasks", "/Create", "/TN", "Decky Loader"]
    assert create[4] == "/XML" and create[6:] == ["/F"]
    assert rec.runs[2] == ["schtasks", "/Run", "/TN", "Decky Loader"]


def test_install_does_not_overwrite_existing_plugins(tmp_path):
    home, src_exe = _fake_env(tmp_path)
    pd = tmp_path / "ProgramData"
    (pd / "decky" / "plugins").mkdir(parents=True)
    wi.install(SID, is_admin=lambda: True, run=Recorder(), shell_execute=lambda *a: None,
               exe=str(src_exe), program_data=str(pd), home=str(home), temp_dir=str(tmp_path))
    assert not (pd / "decky" / "plugins" / "p1").exists()


def test_install_non_admin_elevates_for_current_sid(tmp_path):
    calls: list[tuple] = []
    rec = Recorder()
    rc = wi.install(None, is_admin=lambda: False, run=rec, shell_execute=lambda *a: calls.append(a) or 33,
                    current_sid=lambda: SID, exe=r"C:\x\PluginLoader.exe",
                    program_data=str(tmp_path / "pd"), home=str(tmp_path), temp_dir=str(tmp_path))
    assert rc == 0
    assert rec.runs == []
    assert not (tmp_path / "pd").exists()
    assert len(calls) == 1
    hwnd, verb, file, params, cwd, show = calls[0]
    assert hwnd is None and verb == "runas" and show == 1
    if getattr(sys, "frozen", False):
        assert file == r"C:\x\PluginLoader.exe"
        assert params == f"--install --for-sid {SID}"
    else:
        assert params.endswith(f"--install --for-sid {SID}")


def test_install_elevation_failure_returns_nonzero(tmp_path):
    rc = wi.install(None, is_admin=lambda: False, run=Recorder(), shell_execute=lambda *a: 5,
                    current_sid=lambda: SID, exe="x.exe", program_data=str(tmp_path), home=str(tmp_path), temp_dir=str(tmp_path))
    assert rc == 1


def test_uninstall_deletes_task_and_only_services_dir(tmp_path):
    pd = tmp_path / "ProgramData" / "decky"
    (pd / "services").mkdir(parents=True)
    (pd / "services" / "PluginLoader.exe").write_bytes(b"x")
    (pd / "plugins").mkdir()
    rec = Recorder()
    removed: list[str] = []
    rc = wi.uninstall(is_admin=lambda: True, run=rec, shell_execute=lambda *a: None, program_data=str(tmp_path / "ProgramData"),
                      rmtree=removed.append)
    assert rc == 0
    assert rec.runs == [["schtasks", "/Delete", "/TN", "Decky Loader", "/F"]]
    assert removed == [str(pd / "services")]
    assert (pd / "plugins").exists()


def test_uninstall_non_admin_elevates(tmp_path):
    calls: list[tuple] = []
    rec = Recorder()
    rc = wi.uninstall(is_admin=lambda: False, run=rec, shell_execute=lambda *a: calls.append(a) or 33,
                      exe=r"C:\x\PluginLoader.exe", program_data=str(tmp_path), rmtree=lambda p: None)
    assert rc == 0 and rec.runs == []
    assert calls[0][1] == "runas"
    assert calls[0][3].endswith("--uninstall")


# localplatformwin path layout

def _clear(monkeypatch):
    for k in ("PRIVILEGED_PATH", "UNPRIVILEGED_PATH"):
        monkeypatch.delenv(k, raising=False)


def test_privileged_path_env_wins(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("PRIVILEGED_PATH", str(tmp_path / "priv"))
    assert lpw.get_privileged_path() == str(tmp_path / "priv")


def test_privileged_path_installed(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    services = tmp_path / "decky" / "services"
    services.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(services / "PluginLoader.exe"))
    assert os.path.normcase(lpw.get_privileged_path()) == os.path.normcase(str(tmp_path / "decky"))


def test_privileged_path_portable(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "pd"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "PluginLoader.exe"))
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home") if p == "~" else p)
    assert lpw.get_privileged_path() == str(tmp_path / "home" / "homebrew")


def test_unprivileged_path_unchanged(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    services = tmp_path / "decky" / "services"
    services.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(services / "PluginLoader.exe"))
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home") if p == "~" else p)
    assert lpw.get_unprivileged_path() == str(tmp_path / "home" / "homebrew")


def test_installed_root_none_when_not_installed(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "other" / "PluginLoader.exe"))
    assert lpw.installed_root() is None
