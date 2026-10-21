import datetime, os, shutil, subprocess, sys
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
    (tmp_path / "ProgramData").mkdir()
    return profile, src_exe


class Recorder:
    def __init__(self):
        self.runs: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.runs.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)


class Dirs:
    '''Stands in for the ACL checks and CreateDirectoryW: directories it created count as trusted.'''
    def __init__(self):
        self.trusted: set[str] = set()
        self.created: list[str] = []

    def is_trusted(self, p):
        return p in self.trusted

    def create(self, p):
        os.mkdir(p)
        self.created.append(p)
        self.trusted.add(p)


def _install(tmp_path, src_exe, profile, dirs=None, **over):
    dirs = dirs or Dirs()
    kw = dict(is_admin=lambda: True, run=Recorder(), shell_execute=lambda *a: None,
              exe=str(src_exe), program_data=str(tmp_path / "ProgramData"),
              profile_for_sid=lambda sid: str(profile), sleep=lambda s: None,
              is_trusted=dirs.is_trusted, create_dir=dirs.create)
    kw.update(over)
    return wi.install(SID, **kw)


def _junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def test_secure_sddl_verbatim():
    assert wi.SECURE_SDDL == "O:BAG:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)"


def test_install_command_sequence_has_no_icacls(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    rec, dirs = Recorder(), Dirs()
    rc = _install(tmp_path, src_exe, profile, dirs=dirs, run=rec)

    root = str(tmp_path / "ProgramData" / "decky")
    assert rc == 0
    assert not hasattr(wi, "acl_commands") and not hasattr(wi, "acl_setup_commands")
    assert rec.runs[0] == ["schtasks", "/End", "/TN", "Decky Loader"]
    assert rec.runs[1][:4] == ["schtasks", "/Create", "/TN", "Decky Loader"]
    assert rec.runs[1][4] == "/XML" and rec.runs[1][6:] == ["/F"]
    assert os.path.dirname(rec.runs[1][5]) == root  # task XML staged inside the protected root
    assert rec.runs[2] == ["schtasks", "/Run", "/TN", "Decky Loader"]
    assert len(rec.runs) == 3
    assert dirs.created == [root, os.path.join(root, "services"), os.path.join(root, "plugins")]
    assert os.path.isfile(os.path.join(root, "services", "PluginLoader.exe"))
    assert os.path.isfile(os.path.join(root, "plugins", "p1", "plugin.json"))


def test_untrusted_root_is_renamed_aside_and_recreated(tmp_path, capsys):
    profile, src_exe = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    root.mkdir()
    (root / "evil.txt").write_text("x")
    dirs = Dirs()
    when = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
    rc = _install(tmp_path, src_exe, profile, dirs=dirs)
    assert rc == 0
    # default clock used above; also check the explicit-clock helper directly
    assert dirs.created[0] == str(root)
    asides = [p for p in os.listdir(tmp_path / "ProgramData") if p.startswith("decky.untrusted-")]
    assert len(asides) == 1
    assert (tmp_path / "ProgramData" / asides[0] / "evil.txt").is_file()
    assert not (root / "evil.txt").exists()
    assert "moved it to" in capsys.readouterr().err

    target = tmp_path / "p2"
    target.mkdir()
    wi.ensure_trusted_dir(str(target), remove_links=False, is_trusted=lambda p: False, create_dir=os.mkdir, now=lambda: when)
    assert (tmp_path / "p2.untrusted-20260102030405").is_dir() and target.is_dir()


def test_trusted_existing_root_is_reused(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    root.mkdir()
    (root / "keep.txt").write_text("x")
    dirs = Dirs()
    dirs.trusted.add(str(root))
    assert _install(tmp_path, src_exe, profile, dirs=dirs) == 0
    assert str(root) not in dirs.created
    assert (root / "keep.txt").is_file()
    assert not [p for p in os.listdir(tmp_path / "ProgramData") if "untrusted" in p]


def test_junction_root_is_renamed_as_link_only(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "victim.txt").write_text("v")
    root = tmp_path / "ProgramData" / "decky"
    _junction(root, elsewhere)
    dirs = Dirs()
    assert _install(tmp_path, src_exe, profile, dirs=dirs, is_trusted=lambda p: False if p == str(root) else dirs.is_trusted(p)) == 0
    assert (elsewhere / "victim.txt").read_text() == "v"
    assert not wi.is_reparse_point(str(root)) and root.is_dir()


def test_junction_at_services_is_removed_as_link_and_recreated(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "victim.txt").write_text("v")
    services = root / "services"
    _junction(services, elsewhere)
    dirs = Dirs()
    dirs.trusted.add(str(root))
    assert _install(tmp_path, src_exe, profile, dirs=dirs) == 0
    assert (elsewhere / "victim.txt").read_text() == "v"   # target untouched
    assert not (elsewhere / "PluginLoader.exe").exists()    # exe did not land in the target
    assert not wi.is_reparse_point(str(services)) and (services / "PluginLoader.exe").is_file()
    assert str(services) in dirs.created


def test_exe_is_staged_then_replaced_never_opened_in_place(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    installed = root / "services" / "PluginLoader.exe"
    dirs = Dirs()
    dirs.trusted.update([str(root), str(root / "services")])
    (root / "services").mkdir(parents=True)
    installed.write_bytes(b"old")
    copies, replaces = [], []

    def copyfile(src, dst):
        copies.append(dst)
        shutil.copyfile(src, dst)

    def replace(src, dst):
        replaces.append((src, dst))
        os.replace(src, dst)

    assert _install(tmp_path, src_exe, profile, dirs=dirs, copyfile=copyfile, replace=replace) == 0
    assert copies and all(c != str(installed) for c in copies)
    assert copies[0].startswith(str(installed) + ".installing-")
    assert replaces == [(copies[0], str(installed))]
    assert installed.read_bytes() == b"exe"
    assert not os.path.exists(copies[0])


def test_install_does_not_overwrite_existing_plugins(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    (root / "plugins" / "mine").mkdir(parents=True)
    dirs = Dirs()
    dirs.trusted.update([str(root), str(root / "plugins")])
    _install(tmp_path, src_exe, profile, dirs=dirs)
    assert not (root / "plugins" / "p1").exists()


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
    assert os.listdir(tmp_path / "ProgramData" / "decky" / "plugins") == []


def test_migration_skips_junctions_inside_plugins(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "passwords.txt").write_text("s")
    _junction(profile / "homebrew" / "plugins" / "link", secret)
    assert _install(tmp_path, src_exe, profile) == 0
    plugins = tmp_path / "ProgramData" / "decky" / "plugins"
    assert (plugins / "p1" / "plugin.json").is_file()
    assert not (plugins / "link").exists()
    assert (secret / "passwords.txt").is_file()


def test_migration_skips_junctioned_plugins_root(tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "passwords.txt").write_text("s")
    src = tmp_path / "homebrew_plugins"
    _junction(src, secret)
    dst = tmp_path / "dst"
    dst.mkdir()
    wi.migrate_plugins(str(src), str(dst))
    assert os.listdir(dst) == []


def test_is_trusted_dir_checks(tmp_path):
    d = str(tmp_path)
    good = lambda p: ("S-1-5-32-544", 0x1000)
    assert wi.is_trusted_dir(d, owner_and_control=good) is True
    assert wi.is_trusted_dir(d, owner_and_control=lambda p: ("S-1-5-18", 0x9000)) is True
    assert wi.is_trusted_dir(d, owner_and_control=lambda p: (SID, 0x1000)) is False       # foreign owner
    assert wi.is_trusted_dir(d, owner_and_control=lambda p: ("S-1-5-32-544", 0x0004)) is False  # unprotected DACL
    assert wi.is_trusted_dir(d, reparse=lambda p: True, owner_and_control=good) is False   # link
    assert wi.is_trusted_dir(str(tmp_path / "missing"), owner_and_control=good) is False


def test_create_secure_dir_real_protected_dacl(tmp_path):
    # Owner Administrators can only be set by an elevated token, so the integration check uses the same
    # descriptor without the owner/group clause and asserts the protection bit.
    sddl = wi.SECURE_SDDL.split("D:", 1)[1]
    d = str(tmp_path / "secured")
    wi.create_secure_dir(d, "D:" + sddl)
    owner, control = wi._owner_and_control(d)
    assert control & wi.SE_DACL_PROTECTED
    assert wi.is_trusted_dir(d, owner_and_control=lambda p: ("S-1-5-32-544", control)) is True
    assert wi.is_trusted_dir(d) == (owner in wi.TRUSTED_OWNERS)


def test_profile_path_for_sid_reads_registry_for_real_sid():
    sid = wi.current_user_sid()
    path = wi.profile_path_for_sid(sid)
    assert path is not None and os.path.isdir(path)


def test_install_same_file_skips_copy(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    installed = root / "services" / "PluginLoader.exe"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"exe")
    dirs = Dirs()
    dirs.trusted.update([str(root), str(root / "services")])
    copies: list = []
    rc = _install(tmp_path, installed, profile, dirs=dirs, copyfile=lambda a, b: copies.append((a, b)))
    assert rc == 0 and copies == []


def test_install_replace_retries_on_permission_error(tmp_path):
    profile, src_exe = _fake_env(tmp_path)
    attempts: list = []
    sleeps: list = []

    def flaky(src, dst):
        attempts.append(dst)
        if len(attempts) < 4:
            raise PermissionError("locked")
        os.replace(src, dst)

    rc = _install(tmp_path, src_exe, profile, replace=flaky, sleep=sleeps.append)
    assert rc == 0 and len(attempts) == 4 and sleeps == [1.0, 1.0, 1.0]


def test_install_replace_gives_up_after_ten_attempts(tmp_path, capsys):
    profile, src_exe = _fake_env(tmp_path)
    rec = Recorder()
    attempts: list = []
    sleeps: list = []

    def locked(src, dst):
        attempts.append(1)
        raise PermissionError("locked")

    rc = _install(tmp_path, src_exe, profile, run=rec, replace=locked, sleep=sleeps.append)
    assert rc == 1 and len(attempts) == 10 and len(sleeps) == 9
    assert "still in use" in capsys.readouterr().err
    assert not any(r[:2] == ["schtasks", "/Create"] for r in rec.runs)
    assert not [p for p in os.listdir(tmp_path / "ProgramData" / "decky" / "services") if "installing" in p]


def test_install_non_admin_elevates_for_current_sid(tmp_path):
    calls: list[tuple] = []
    rec = Recorder()
    rc = wi.install(None, is_admin=lambda: False, run=rec, shell_execute=lambda *a: calls.append(a) or 33,
                    current_sid=lambda: SID, is_admin_member=lambda: True, exe=r"C:\x\PluginLoader.exe",
                    program_data=str(tmp_path / "pd"))
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
