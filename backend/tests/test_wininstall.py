import datetime, os, shutil, subprocess, sys
import xml.etree.ElementTree as ET
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    from decky_loader.localplatform import wininstall as wi
    from decky_loader.localplatform import localplatformwin as lpw

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
SID = "S-1-5-21-1-2-3-1001"


CMD = r"C:\Windows\System32\cmd.exe"
TMP = r"C:\ProgramData\decky\tmp"


def test_task_xml_runs_highest_at_user_logon():
    xml = wi.build_task_xml(r"C:\ProgramData\decky\services\PluginLoader.exe", r"C:\ProgramData\decky\services", SID, TMP, CMD)
    root = ET.fromstring(xml)

    def text(path: str) -> str | None:
        return root.findtext(path, namespaces=NS)

    assert text("t:Principals/t:Principal/t:RunLevel") == "HighestAvailable"
    assert text("t:Triggers/t:LogonTrigger/t:UserId") == SID
    assert text("t:Principals/t:Principal/t:UserId") == SID
    assert text("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text("t:Actions/t:Exec/t:WorkingDirectory") == r"C:\ProgramData\decky\services"
    assert text("t:Settings/t:ExecutionTimeLimit") == "PT0S"
    assert text("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert text("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert text("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert root.get("version") == "1.2"


def test_task_xml_runs_exe_through_cmd_with_admin_only_temp():
    xml = wi.build_task_xml(r"C:\ProgramData\decky\services\PluginLoader.exe", r"C:\ProgramData\decky\services", SID, TMP, CMD)
    root = ET.fromstring(xml)
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == r"C:\Windows\System32\cmd.exe"
    assert root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS) == (
        r'/d /v:off /s /c "set "TMP=C:\ProgramData\decky\tmp"&& set "TEMP=C:\ProgramData\decky\tmp"&& '
        r'"C:\ProgramData\decky\services\PluginLoader.exe""')


def test_task_arguments_survive_real_cmd_parsing(tmp_path):
    # The exact arguments go through the real cmd.exe; a batch file stands in for the exe and reports TMP/TEMP
    exe_dir = tmp_path / "A&B (x86) ^x"
    exe_dir.mkdir()
    probe = exe_dir / "probe.cmd"
    probe.write_text("@set TMP\r\n@set TEMP\r\n")
    tmp_dir = str(tmp_path / "t&mp dir")
    args = wi.task_arguments(str(probe), tmp_dir)
    cmd = os.path.join(wi.system_directory(), "cmd.exe")
    out = subprocess.run(f'"{cmd}" {args}', capture_output=True, text=True, timeout=30)
    assert out.stdout.splitlines() == [f"TMP={tmp_dir}", f"TEMP={tmp_dir}"], out.stderr


@pytest.mark.parametrize("bad", [r'C:\a"b\PluginLoader.exe', r"C:\%USERPROFILE%\PluginLoader.exe"])
def test_task_arguments_reject_unquotable_paths(bad):
    with pytest.raises(ValueError):
        wi.task_arguments(bad, TMP)
    with pytest.raises(ValueError):
        wi.task_arguments(r"C:\x\PluginLoader.exe", bad)


def test_task_xml_escapes_paths():
    xml = wi.build_task_xml(r"C:\A&B\PluginLoader.exe", r"C:\A&B", SID, r"C:\A&B\tmp", CMD)
    root = ET.fromstring(xml)
    assert root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS) == (
        r'/d /v:off /s /c "set "TMP=C:\A&B\tmp"&& set "TEMP=C:\A&B\tmp"&& "C:\A&B\PluginLoader.exe""')
    assert root.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=NS) == r"C:\A&B"


def test_system_directory_and_user_temp_ignore_env(monkeypatch):
    sysdir, temp = wi.system_directory(), wi.user_temp_dir()
    monkeypatch.setenv("SystemRoot", r"C:\evil")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\evil")
    assert wi.system_directory() == sysdir and os.path.isfile(os.path.join(sysdir, "cmd.exe"))
    assert wi.user_temp_dir() == temp and temp.lower().endswith(r"\temp")


def test_install_points_task_at_admin_only_temp(tmp_path):
    src_exe = _fake_env(tmp_path)
    xmls: list[str] = []

    class XmlRecorder(Recorder):
        def __call__(self, argv, **kwargs):
            if argv[1] == "/Create":
                with open(argv[5], encoding="utf-16") as f:
                    xmls.append(f.read())
            return super().__call__(argv, **kwargs)

    assert _install(tmp_path, src_exe, run=XmlRecorder(), system_dir=lambda: r"C:\Sys32") == 0
    root = str(tmp_path / "ProgramData" / "decky")
    task = ET.fromstring(xmls[0].encode("utf-16"))
    assert task.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == r"C:\Sys32\cmd.exe"
    assert task.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS) == wi.task_arguments(
        os.path.join(root, "services", "PluginLoader.exe"), os.path.join(root, "tmp"))
    assert os.path.isdir(os.path.join(root, "tmp"))


def test_current_user_sid_parses_whoami():
    sid = wi.current_user_sid()
    assert sid.startswith("S-1-5-")


def test_current_user_sid_parsing_with_injected_runner():
    class R:
        stdout = '"DOMAIN\\user","S-1-5-21-9-9-9-1000"\r\n'
    assert wi.current_user_sid(run=lambda *a, **k: R()) == "S-1-5-21-9-9-9-1000"


def _fake_env(tmp_path):
    src_exe = tmp_path / "src" / "PluginLoader.exe"
    src_exe.parent.mkdir()
    src_exe.write_bytes(b"exe")
    (tmp_path / "ProgramData").mkdir()
    return src_exe


class Recorder:
    def __init__(self, events=None):
        self.runs: list[list[str]] = []
        self.events = events

    def __call__(self, argv, **kwargs):
        self.runs.append(list(argv))
        if self.events is not None:
            self.events.append(("run", argv[1] if len(argv) > 1 else ""))
        return subprocess.CompletedProcess(argv, 0)


DIR_ATTR = 0x10
REPARSE_ATTR = 0x400


class Dirs:
    '''Stands in for the handle-based ACL checks and CreateDirectoryW. Directories it created count as trusted;
    a path is a "link" if it really is a reparse point on disk.'''
    def __init__(self, events=None):
        self.trusted: set[str] = set()
        self.created: list[str] = []
        self.opened: list[str] = []
        self.closed: list[str] = []
        self.events = events

    def open(self, p):
        if not os.path.lexists(p):
            raise FileNotFoundError(p)
        self.opened.append(p)
        return p

    def query(self, h):
        attrs = DIR_ATTR | (REPARSE_ATTR if wi.is_reparse_point(h) else 0)
        return attrs, ("S-1-5-32-544" if h in self.trusted else "S-1-5-21-1-2-3-1001"), 0x1000

    def close(self, h):
        self.closed.append(h)
        if self.events is not None:
            self.events.append(("close", h))

    def create(self, p):
        os.mkdir(p)
        self.created.append(p)
        self.trusted.add(p)

    def open_trusted(self, p):
        return wi.open_trusted_dir(p, opener=self.open, query=self.query, closer=self.close)


def _install(tmp_path, src_exe, dirs=None, **over):
    dirs = dirs or Dirs()
    kw = dict(is_admin=lambda: True, run=Recorder(), shell_execute=lambda *a: None,
              exe=str(src_exe), program_data=str(tmp_path / "ProgramData"), sleep=lambda s: None,
              open_trusted=dirs.open_trusted, close_dir=dirs.close, create_dir=dirs.create, kill_running=lambda exe: [])
    kw.update(over)
    return wi.install(SID, **kw)


def _junction(link, target):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def test_secure_sddl_verbatim():
    assert wi.SECURE_SDDL == "O:BAG:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)"


def test_install_command_sequence_has_no_icacls_and_no_migration(tmp_path, capsys):
    src_exe = _fake_env(tmp_path)
    rec, dirs = Recorder(), Dirs()
    rc = _install(tmp_path, src_exe, dirs=dirs, run=rec)

    root = str(tmp_path / "ProgramData" / "decky")
    assert rc == 0
    assert not hasattr(wi, "acl_commands") and not hasattr(wi, "migrate_plugins") and not hasattr(wi, "profile_path_for_sid")
    assert rec.runs[0] == ["schtasks", "/End", "/TN", "Decky Loader"]
    assert rec.runs[1][:4] == ["schtasks", "/Create", "/TN", "Decky Loader"]
    assert rec.runs[1][4] == "/XML" and rec.runs[1][6:] == ["/F"]
    assert os.path.dirname(rec.runs[1][5]) == root  # task XML staged inside the protected root
    assert rec.runs[2] == ["schtasks", "/Run", "/TN", "Decky Loader"]
    assert len(rec.runs) == 3
    assert dirs.created == [root, os.path.join(root, "services"), os.path.join(root, "plugins"), os.path.join(root, "tmp")]
    assert os.path.isfile(os.path.join(root, "services", "PluginLoader.exe"))
    assert os.listdir(os.path.join(root, "plugins")) == []
    assert "are not migrated" in capsys.readouterr().out


def test_root_handle_held_open_until_install_finishes(tmp_path):
    src_exe = _fake_env(tmp_path)
    events: list = []
    dirs = Dirs(events)
    root = str(tmp_path / "ProgramData" / "decky")
    _install(tmp_path, src_exe, dirs=dirs, run=Recorder(events))
    closes = [i for i, e in enumerate(events) if e == ("close", root)]
    runs = [i for i, e in enumerate(events) if e[0] == "run"]
    assert len(closes) == 1 and closes[0] > runs[-1]  # closed once, only after the last schtasks call


def test_root_handle_closed_when_install_fails(tmp_path):
    src_exe = _fake_env(tmp_path)
    dirs = Dirs()
    rc = _install(tmp_path, src_exe, dirs=dirs, replace=lambda a, b: (_ for _ in ()).throw(PermissionError("x")))
    assert rc == 1
    assert str(tmp_path / "ProgramData" / "decky") in dirs.closed


def test_untrusted_root_is_renamed_aside_and_recreated(tmp_path, capsys):
    src_exe = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    root.mkdir()
    (root / "evil.txt").write_text("x")
    dirs = Dirs()
    when = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
    assert _install(tmp_path, src_exe, dirs=dirs) == 0
    assert dirs.created[0] == str(root)
    asides = [p for p in os.listdir(tmp_path / "ProgramData") if p.startswith("decky.untrusted-")]
    assert len(asides) == 1
    assert (tmp_path / "ProgramData" / asides[0] / "evil.txt").is_file()
    assert not (root / "evil.txt").exists()
    assert "moved it to" in capsys.readouterr().err

    target = tmp_path / "p2"
    target.mkdir()
    wi.ensure_trusted_dir(str(target), remove_links=False, open_trusted=dirs.open_trusted, create_dir=dirs.create, now=lambda: when)
    assert (tmp_path / "p2.untrusted-20260102030405").is_dir() and target.is_dir()


def test_trusted_existing_root_is_reused(tmp_path):
    src_exe = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    root.mkdir()
    (root / "keep.txt").write_text("x")
    dirs = Dirs()
    dirs.trusted.add(str(root))
    assert _install(tmp_path, src_exe, dirs=dirs) == 0
    assert str(root) not in dirs.created
    assert (root / "keep.txt").is_file()
    assert not [p for p in os.listdir(tmp_path / "ProgramData") if "untrusted" in p]


def test_junction_root_is_renamed_as_link_only(tmp_path):
    src_exe = _fake_env(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "victim.txt").write_text("v")
    root = tmp_path / "ProgramData" / "decky"
    _junction(root, elsewhere)
    dirs = Dirs()
    dirs.trusted.add(str(root))  # even a junction that points at a "trusted" dir must be rejected via its reparse bit
    assert _install(tmp_path, src_exe, dirs=dirs) == 0
    assert (elsewhere / "victim.txt").read_text() == "v"
    assert not (elsewhere / "PluginLoader.exe").exists()
    assert not wi.is_reparse_point(str(root)) and root.is_dir()


def test_junction_at_services_is_removed_as_link_and_recreated(tmp_path):
    src_exe = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "victim.txt").write_text("v")
    services = root / "services"
    _junction(services, elsewhere)
    dirs = Dirs()
    dirs.trusted.update([str(root), str(services)])
    assert _install(tmp_path, src_exe, dirs=dirs) == 0
    assert (elsewhere / "victim.txt").read_text() == "v"   # target untouched
    assert not (elsewhere / "PluginLoader.exe").exists()    # exe did not land in the target
    assert not wi.is_reparse_point(str(services)) and (services / "PluginLoader.exe").is_file()
    assert str(services) in dirs.created


def test_creation_race_fails_closed(tmp_path, capsys):
    src_exe = _fake_env(tmp_path)

    def racing_create(p):
        raise FileExistsError(p)

    rc = _install(tmp_path, src_exe, create_dir=racing_create)
    assert rc == 1 and "Could not prepare" in capsys.readouterr().err


def test_freshly_created_dir_is_revalidated(tmp_path, capsys):
    src_exe = _fake_env(tmp_path)
    dirs = Dirs()

    def create_untrusted(p):
        os.mkdir(p)  # created, but not owned/protected as required
        dirs.created.append(p)

    rc = _install(tmp_path, src_exe, dirs=dirs, create_dir=create_untrusted)
    assert rc == 1 and "right after creation" in capsys.readouterr().err


def test_exe_is_staged_then_replaced_never_opened_in_place(tmp_path):
    src_exe = _fake_env(tmp_path)
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

    assert _install(tmp_path, src_exe, dirs=dirs, copyfile=copyfile, replace=replace) == 0
    assert copies and all(c != str(installed) for c in copies)
    assert copies[0].startswith(str(installed) + ".installing-")
    assert replaces == [(copies[0], str(installed))]
    assert installed.read_bytes() == b"exe"
    assert not os.path.exists(copies[0])


def test_copy_error_returns_1_with_message(tmp_path, capsys):
    src_exe = _fake_env(tmp_path)

    def broken(src, dst):
        raise OSError("disk full")

    rec = Recorder()
    assert _install(tmp_path, src_exe, run=rec, copyfile=broken) == 1
    assert "disk full" in capsys.readouterr().err
    assert not any(r[:2] == ["schtasks", "/Create"] for r in rec.runs)


def test_open_trusted_dir_checks_on_the_handle():
    closed: list = []
    ok = ("S-1-5-32-544", 0x1000)

    def check(attrs, owner, control):
        return wi.open_trusted_dir("p", opener=lambda p: "H", query=lambda h: (attrs, owner, control), closer=closed.append)

    assert check(0x10, *ok) == "H"
    assert check(0x10, "S-1-5-18", 0x9000) == "H"
    assert closed == []
    assert check(0x10, "S-1-5-21-9", 0x1000) is None         # foreign owner
    assert check(0x10, "S-1-5-32-544", 0x0004) is None       # unprotected DACL
    assert check(0x10 | 0x400, *ok) is None                  # reparse point
    assert check(0x20, *ok) is None                          # not a directory
    assert closed == ["H"] * 4
    assert wi.open_trusted_dir("p", opener=lambda p: (_ for _ in ()).throw(FileNotFoundError()), closer=closed.append) is None


def test_real_handle_reports_directory_and_junction(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    _junction(link, real)

    h = wi.open_dir(str(real))
    try:
        attrs, owner, control = wi.query_dir(h)
    finally:
        wi.close_handle(h)
    assert attrs & wi.FILE_ATTRIBUTE_DIRECTORY and not attrs & wi.FILE_ATTRIBUTE_REPARSE_POINT
    assert owner.startswith("S-1-")

    h = wi.open_dir(str(link))
    try:
        attrs, _, _ = wi.query_dir(h)
    finally:
        wi.close_handle(h)
    assert attrs & wi.FILE_ATTRIBUTE_REPARSE_POINT        # reported via the handle path, not followed
    assert wi.open_trusted_dir(str(link)) is None


def test_open_dir_has_no_share_delete(tmp_path):
    d = tmp_path / "held"
    d.mkdir()
    h = wi.open_dir(str(d))
    try:
        with pytest.raises(OSError):
            os.rename(d, tmp_path / "moved")
        with pytest.raises(OSError):
            os.rmdir(d)
    finally:
        wi.close_handle(h)
    os.rename(d, tmp_path / "moved")


def test_create_secure_dir_real_protected_dacl(tmp_path):
    # Owner Administrators can only be set by an elevated token, so the integration check uses the same
    # descriptor without the owner/group clause and asserts the protection bit through the handle path.
    sddl = wi.SECURE_SDDL.split("D:", 1)[1]
    d = str(tmp_path / "secured")
    wi.create_secure_dir(d, "D:" + sddl)
    h = wi.open_dir(d)
    try:
        _, owner, control = wi.query_dir(h)
    finally:
        wi.close_handle(h)
    assert control & wi.SE_DACL_PROTECTED
    handle = wi.open_trusted_dir(d)
    assert (handle is not None) == (owner in wi.TRUSTED_OWNERS)
    if handle is not None:
        wi.close_handle(handle)


def test_install_same_file_skips_copy(tmp_path):
    _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    installed = root / "services" / "PluginLoader.exe"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"exe")
    dirs = Dirs()
    dirs.trusted.update([str(root), str(root / "services")])
    copies: list = []
    rc = _install(tmp_path, installed, dirs=dirs, copyfile=lambda a, b: copies.append((a, b)))
    assert rc == 0 and copies == []


def test_install_replace_retries_on_permission_error(tmp_path):
    src_exe = _fake_env(tmp_path)
    attempts: list = []
    sleeps: list = []

    def flaky(src, dst):
        attempts.append(dst)
        if len(attempts) < 4:
            raise PermissionError("locked")
        os.replace(src, dst)

    rc = _install(tmp_path, src_exe, replace=flaky, sleep=sleeps.append)
    assert rc == 0 and len(attempts) == 4 and sleeps == [1.0, 1.0, 1.0]


def test_install_replace_gives_up_after_ten_attempts(tmp_path, capsys):
    src_exe = _fake_env(tmp_path)
    rec = Recorder()
    attempts: list = []
    sleeps: list = []

    def locked(src, dst):
        attempts.append(1)
        raise PermissionError("locked")

    rc = _install(tmp_path, src_exe, run=rec, replace=locked, sleep=sleeps.append)
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
    rc = wi.uninstall(is_admin=lambda: True, run=rec, kill_running=lambda exe: [], shell_execute=lambda *a: None,
                      program_data=str(tmp_path / "ProgramData"), defer_delete=lambda p: True)
    assert rc == 0
    assert rec.runs == [["schtasks", "/End", "/TN", "Decky Loader"], ["schtasks", "/Delete", "/TN", "Decky Loader", "/F"]]
    assert not (pd / "services").exists()
    assert (pd / "plugins").exists()


def test_uninstall_defers_locked_files_to_reboot(tmp_path):
    pd = tmp_path / "ProgramData"
    (pd / "decky" / "services").mkdir(parents=True)
    deferred: list[str] = []
    rc = wi.uninstall(is_admin=lambda: True, run=Recorder(), kill_running=lambda exe: [], program_data=str(pd),
                      rmtree=_fake_rmtree(["a.exe", "b.dll"]), defer_delete=lambda p: deferred.append(p) or True)
    assert rc == 0 and deferred == ["a.exe", "b.dll"]


def test_uninstall_fails_when_deferral_fails(tmp_path, capsys):
    pd = tmp_path / "ProgramData"
    (pd / "decky" / "services").mkdir(parents=True)
    rc = wi.uninstall(is_admin=lambda: True, run=Recorder(), kill_running=lambda exe: [], program_data=str(pd),
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


# Stopping every running copy of the installed exe

EXE = r"C:\ProgramData\decky\services\PluginLoader.exe"


def test_matching_pids_compares_full_image_paths_case_insensitively():
    procs = [(10, EXE), (11, EXE.upper()), (12, r"C:\Users\me\Downloads\PluginLoader.exe"),
             (13, EXE + ".new"), (14, r"C:\ProgramData\decky\services\..\services\PluginLoader.exe"), (15, None)]
    assert wi.matching_pids(procs, EXE, own=set()) == [10, 11, 14]


def test_matching_pids_skips_this_process_and_its_parent():
    assert wi.matching_pids([(1, EXE), (2, EXE), (3, EXE)], EXE, own={1, 2}) == [3]


def test_terminate_image_kills_only_matching_processes():
    killed: list[int] = []
    procs = [(100, EXE), (200, r"C:\Windows\explorer.exe"), (300, EXE), (os.getpid(), EXE)]
    got = wi.terminate_image(EXE, processes=lambda: procs, terminate=killed.append)
    assert got == killed == [100, 300]


def test_terminate_image_keeps_going_when_one_kill_fails():
    killed: list[int] = []

    def terminate(pid):
        if pid == 100:
            raise PermissionError("gone")
        killed.append(pid)

    assert wi.terminate_image(EXE, processes=lambda: [(100, EXE), (300, EXE)], terminate=terminate) == [300]


def test_list_processes_sees_this_process():
    me = [p for pid, p in wi.list_processes() if pid == os.getpid()]
    assert me and os.path.isabs(me[0]) and os.path.basename(me[0]).lower().startswith("python")


def test_install_stops_running_copies_after_ending_the_task(tmp_path):
    src_exe = _fake_env(tmp_path)
    events: list = []
    rc = _install(tmp_path, src_exe, run=Recorder(events), kill_running=lambda exe: events.append(("kill", exe)) or [])
    assert rc == 0
    installed = str(tmp_path / "ProgramData" / "decky" / "services" / "PluginLoader.exe")
    assert events[:2] == [("run", "/End"), ("kill", installed)]


def test_uninstall_stops_running_copies_after_ending_the_task(tmp_path):
    events: list = []
    pd = tmp_path / "ProgramData"
    (pd / "decky" / "services").mkdir(parents=True)
    rc = wi.uninstall(is_admin=lambda: True, run=Recorder(events), program_data=str(pd), defer_delete=lambda p: True,
                      kill_running=lambda exe: events.append(("kill", exe)) or [])
    assert rc == 0
    assert events == [("run", "/End"), ("kill", str(pd / "decky" / "services" / "PluginLoader.exe")), ("run", "/Delete")]


def test_install_deletes_stale_update_files(tmp_path):
    src_exe = _fake_env(tmp_path)
    services = tmp_path / "ProgramData" / "decky" / "services"
    services.mkdir(parents=True)
    dirs = Dirs()
    dirs.trusted.update([str(services.parent), str(services)])
    for name in ("PluginLoader.exe.new", "PluginLoader.exe.download", "PluginLoader.exe.download.exe", "keep.txt"):
        (services / name).write_bytes(b"stale")
    assert _install(tmp_path, src_exe, dirs=dirs) == 0
    assert sorted(os.listdir(services)) == ["PluginLoader.exe", "keep.txt"]


def test_install_fails_when_a_stale_update_cannot_be_removed(tmp_path, capsys, monkeypatch):
    src_exe = _fake_env(tmp_path)
    services = tmp_path / "ProgramData" / "decky" / "services"
    services.mkdir(parents=True)
    dirs = Dirs()
    dirs.trusted.update([str(services.parent), str(services)])
    (services / "PluginLoader.exe.new").write_bytes(b"stale")
    rec = Recorder()

    def remove(p):
        raise PermissionError("locked")

    assert _install(tmp_path, src_exe, dirs=dirs, run=rec, remove=remove) == 1
    assert "PluginLoader.exe.new" in capsys.readouterr().err
    assert not any(r[:2] == ["schtasks", "/Create"] for r in rec.runs)


def test_terminate_pid_really_stops_a_process():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        wi.terminate_pid(p.pid)
        assert p.wait(10) == 1
    finally:
        p.kill()
