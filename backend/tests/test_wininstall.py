import os, shutil, subprocess, sys
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
    profile = tmp_path / "profile"
    (profile / "homebrew" / "plugins" / "p1").mkdir(parents=True)
    (profile / "homebrew" / "plugins" / "p1" / "plugin.json").write_text("{}")
    src_exe = tmp_path / "src" / "PluginLoader.exe"
    src_exe.parent.mkdir()
    src_exe.write_bytes(b"exe")
    return profile, src_exe


class Recorder:
    def __init__(self, events=None):
        self.runs: list[list[str]] = []
        self.events = events

    def __call__(self, argv, **kwargs):
        self.runs.append(list(argv))
        if self.events is not None:
            self.events.append(("run", list(argv)))
        return subprocess.CompletedProcess(argv, 0)


def _install(tmp_path, src_exe, profile, **over):
    kw = dict(is_admin=lambda: True, run=Recorder(), shell_execute=lambda *a: None,
              exe=str(src_exe), program_data=str(tmp_path / "ProgramData"), temp_dir=str(tmp_path),
              profile_for_sid=lambda sid: str(profile), sleep=lambda s: None)
    kw.update(over)
    return wi.install(SID, **kw)


def test_install_issues_exact_ordered_command_sequence(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    rec = Recorder()
    rc = _install(tmp_path, src_exe, profile, run=rec)

    root = str(tmp_path / "ProgramData" / "decky")
    assert rc == 0
    assert os.path.isfile(os.path.join(root, "services", "PluginLoader.exe"))
    assert os.path.isfile(os.path.join(root, "plugins", "p1", "plugin.json"))

    assert rec.runs[0] == ["schtasks", "/End", "/TN", "Decky Loader"]
    assert rec.runs[1] == ["icacls", root, "/setowner", "*S-1-5-32-544", "/T", "/C"]
    assert rec.runs[2] == ["icacls", root, "/reset", "/T", "/C"]
    assert rec.runs[3] == wi.acl_commands(root)[0]
    assert rec.runs[3] == ["icacls", root, "/inheritance:r", "/grant:r",
                           "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-32-545:(OI)(CI)RX"]
    assert rec.runs[4][:4] == ["schtasks", "/Create", "/TN", "Decky Loader"]
    assert rec.runs[4][4] == "/XML" and rec.runs[4][6:] == ["/F"]
    assert rec.runs[5] == ["schtasks", "/Run", "/TN", "Decky Loader"]
    assert len(rec.runs) == 6
    assert wi.acl_setup_commands(root) == rec.runs[1:4]


def test_every_icacls_call_precedes_any_copy(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    events: list[tuple] = []
    rec = Recorder(events)
    root = tmp_path / "ProgramData" / "decky"

    def copyfile(src, dst):
        events.append(("copy", dst))
        # The directory must already exist and be locked when the exe lands
        assert any(e[0] == "run" and e[1][0] == "icacls" for e in events)
        shutil.copyfile(src, dst)

    _install(tmp_path, src_exe, profile, run=rec, copyfile=copyfile)
    kinds = [e[0] if e[0] == "copy" else e[1][0] for e in events]
    assert kinds.index("copy") > max(i for i, k in enumerate(kinds) if k == "icacls")
    assert root.is_dir()


def test_install_does_not_overwrite_existing_plugins(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    (tmp_path / "ProgramData" / "decky" / "plugins").mkdir(parents=True)
    _install(tmp_path, src_exe, profile)
    assert not (tmp_path / "ProgramData" / "decky" / "plugins" / "p1").exists()


def test_install_migrates_plugins_from_target_sid_profile(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    asked: list[str] = []

    def lookup(sid):
        asked.append(sid)
        return str(profile)

    _install(tmp_path, src_exe, profile, profile_for_sid=lookup)
    assert asked == [SID]
    assert (tmp_path / "ProgramData" / "decky" / "plugins" / "p1" / "plugin.json").is_file()


def test_install_skips_migration_without_profile(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    assert _install(tmp_path, src_exe, profile, profile_for_sid=lambda s: None) == 0
    assert not (tmp_path / "ProgramData" / "decky" / "plugins").exists()


def test_profile_path_for_sid_reads_registry_for_real_sid():
    sid = wi.current_user_sid()
    path = wi.profile_path_for_sid(sid)
    assert path is not None and os.path.isdir(path)


def test_install_same_file_skips_copy(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    installed = tmp_path / "ProgramData" / "decky" / "services" / "PluginLoader.exe"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"exe")
    copies: list = []
    rc = _install(tmp_path, installed, profile, copyfile=lambda a, b: copies.append((a, b)))
    assert rc == 0 and copies == []


def test_install_copy_retries_on_permission_error(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    attempts: list = []
    sleeps: list = []

    def flaky(src, dst):
        attempts.append(dst)
        if len(attempts) < 4:
            raise PermissionError("locked")
        shutil.copyfile(src, dst)

    rc = _install(tmp_path, src_exe, profile, copyfile=flaky, sleep=sleeps.append)
    assert rc == 0 and len(attempts) == 4 and sleeps == [1.0, 1.0, 1.0]


def test_install_copy_gives_up_after_ten_attempts(tmp_path, capsys):
    profile, src_exe = _fake_env(tmp_path)
    rec = Recorder()
    attempts: list = []

    def locked(src, dst):
        attempts.append(1)
        raise PermissionError("locked")

    sleeps: list = []
    rc = _install(tmp_path, src_exe, profile, run=rec, copyfile=locked, sleep=sleeps.append)
    assert rc == 1 and len(attempts) == 10 and len(sleeps) == 9
    assert "still in use" in capsys.readouterr().err
    assert not any(r[:2] == ["schtasks", "/Create"] for r in rec.runs)


def test_install_non_admin_elevates_for_current_sid(tmp_path):
    calls: list[tuple] = []
    rec = Recorder()
    rc = wi.install(None, is_admin=lambda: False, run=rec, shell_execute=lambda *a: calls.append(a) or 33,
                    current_sid=lambda: SID, is_admin_member=lambda: True, exe=r"C:\x\PluginLoader.exe",
                    program_data=str(tmp_path / "pd"), temp_dir=str(tmp_path))
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


def test_install_non_admin_account_refused_without_elevating(tmp_path, capsys):
    calls: list = []
    rc = wi.install(None, is_admin=lambda: False, run=Recorder(), shell_execute=lambda *a: calls.append(a) or 33,
                    current_sid=lambda: SID, is_admin_member=lambda: False, exe="x.exe", program_data=str(tmp_path))
    assert rc == 1 and calls == []
    assert "requires an administrator account" in capsys.readouterr().err


def test_user_in_administrators_parsing():
    class R:
        def __init__(self, out): self.stdout = out
    member = '"BUILTIN\\Administrators","Alias","S-1-5-32-544","Group used for deny only"\r\n"Everyone","Well-known group","S-1-1-0","Mandatory group"\r\n'
    other = '"Everyone","Well-known group","S-1-1-0","Mandatory group"\r\n"BUILTIN\\Users","Alias","S-1-5-32-545","Mandatory group"\r\n'
    assert wi.user_in_administrators(run=lambda *a, **k: R(member)) is True
    assert wi.user_in_administrators(run=lambda *a, **k: R(other)) is False


def test_install_elevation_failure_returns_nonzero(tmp_path):
    rc = wi.install(None, is_admin=lambda: False, run=Recorder(), shell_execute=lambda *a: 5,
                    current_sid=lambda: SID, is_admin_member=lambda: True, exe="x.exe", program_data=str(tmp_path))
    assert rc == 1


def test_known_program_data_ignores_env(monkeypatch):
    real = wi.known_program_data()
    monkeypatch.setenv("PROGRAMDATA", r"C:\evil")
    assert wi.known_program_data() == real
    assert os.path.isdir(real)


def _fake_rmtree(errors):
    def rmtree(path, **kw):
        handler = kw.get("onexc") or kw.get("onerror")
        for p in errors:
            handler(os.remove, p, OSError("locked"))
    return rmtree


def test_uninstall_ends_then_deletes_task_and_only_services_dir(tmp_path):
    pd = tmp_path / "ProgramData" / "decky"
    (pd / "services").mkdir(parents=True)
    (pd / "services" / "PluginLoader.exe").write_bytes(b"x")
    (pd / "plugins").mkdir()
    rec = Recorder()
    rc = wi.uninstall(is_admin=lambda: True, run=rec, shell_execute=lambda *a: None,
                      program_data=str(tmp_path / "ProgramData"), defer_delete=lambda p: True)
    assert rc == 0
    assert rec.runs == [["schtasks", "/End", "/TN", "Decky Loader"], ["schtasks", "/Delete", "/TN", "Decky Loader", "/F"]]
    assert not (pd / "services").exists()
    assert (pd / "plugins").exists()


def test_uninstall_defers_locked_files_to_reboot(tmp_path):
    pd = tmp_path / "ProgramData"
    (pd / "decky" / "services").mkdir(parents=True)
    deferred: list[str] = []
    rc = wi.uninstall(is_admin=lambda: True, run=Recorder(), program_data=str(pd),
                      rmtree=_fake_rmtree(["a.exe", "b.dll"]), defer_delete=lambda p: deferred.append(p) or True)
    assert rc == 0 and deferred == ["a.exe", "b.dll"]


def test_uninstall_fails_when_deferral_fails(tmp_path, capsys):
    pd = tmp_path / "ProgramData"
    (pd / "decky" / "services").mkdir(parents=True)
    rc = wi.uninstall(is_admin=lambda: True, run=Recorder(), program_data=str(pd),
                      rmtree=_fake_rmtree(["a.exe"]), defer_delete=lambda p: False)
    assert rc == 1
    assert "a.exe" in capsys.readouterr().err


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


def _installed(monkeypatch, tmp_path):
    services = tmp_path / "decky" / "services"
    services.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(services / "PluginLoader.exe"))
    monkeypatch.setattr(lpw, "known_program_data", lambda: str(tmp_path))


def test_privileged_path_env_wins_in_portable_mode(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setattr(lpw, "known_program_data", lambda: str(tmp_path / "pd"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "PluginLoader.exe"))
    monkeypatch.setenv("PRIVILEGED_PATH", str(tmp_path / "priv"))
    assert lpw.get_privileged_path() == str(tmp_path / "priv")


def test_privileged_path_env_ignored_in_installed_mode(monkeypatch, tmp_path):
    _clear(monkeypatch)
    _installed(monkeypatch, tmp_path)
    monkeypatch.setenv("PRIVILEGED_PATH", str(tmp_path / "evil"))
    assert os.path.normcase(lpw.get_privileged_path()) == os.path.normcase(str(tmp_path / "decky"))
    assert not (tmp_path / "evil").exists()


def test_privileged_path_installed(monkeypatch, tmp_path):
    _clear(monkeypatch)
    _installed(monkeypatch, tmp_path)
    assert os.path.normcase(lpw.get_privileged_path()) == os.path.normcase(str(tmp_path / "decky"))


def test_privileged_path_portable(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setattr(lpw, "known_program_data", lambda: str(tmp_path / "pd"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "PluginLoader.exe"))
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home") if p == "~" else p)
    assert lpw.get_privileged_path() == str(tmp_path / "home" / "homebrew")


def test_unprivileged_path_unchanged(monkeypatch, tmp_path):
    _clear(monkeypatch)
    _installed(monkeypatch, tmp_path)
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home") if p == "~" else p)
    assert lpw.get_unprivileged_path() == str(tmp_path / "home" / "homebrew")


def test_unprivileged_path_env_honored(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("UNPRIVILEGED_PATH", str(tmp_path / "u"))
    assert lpw.get_unprivileged_path() == str(tmp_path / "u")


def test_installed_root_none_when_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(lpw, "known_program_data", lambda: str(tmp_path))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "other" / "PluginLoader.exe"))
    assert lpw.installed_root() is None


def test_installed_root_ignores_spoofed_programdata_env(monkeypatch, tmp_path):
    fake = tmp_path / "fake"
    (fake / "decky" / "services").mkdir(parents=True)
    monkeypatch.setenv("PROGRAMDATA", str(fake))
    monkeypatch.setattr(sys, "executable", str(fake / "decky" / "services" / "PluginLoader.exe"))
    assert lpw.installed_root() is None
