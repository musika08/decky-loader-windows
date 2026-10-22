import datetime, os, shutil, subprocess, sys, time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    from decky_loader.localplatform import wininstall as wi
    from decky_loader.localplatform import localplatformwin as lpw

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
SID = "S-1-5-21-1-2-3-1001"


SCHTASKS = r"C:\Sys32\schtasks.exe"
APP_DIR = r"C:\Program Files\Decky Loader"


def test_task_xml_runs_highest_at_user_logon():
    xml = wi.build_task_xml(APP_DIR + r"\PluginLoader.exe", APP_DIR, SID)
    root = ET.fromstring(xml)

    def text(path: str) -> str | None:
        return root.findtext(path, namespaces=NS)

    assert text("t:Principals/t:Principal/t:RunLevel") == "HighestAvailable"
    assert text("t:Triggers/t:LogonTrigger/t:UserId") == SID
    assert text("t:Principals/t:Principal/t:UserId") == SID
    assert text("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text("t:Settings/t:ExecutionTimeLimit") == "PT0S"
    assert text("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert text("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert text("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert root.get("version") == "1.2"


def test_task_xml_starts_the_installed_exe_directly():
    xml = wi.build_task_xml(APP_DIR + r"\PluginLoader.exe", APP_DIR, SID)
    root = ET.fromstring(xml)
    execs = root.findall("t:Actions/t:Exec", namespaces=NS)
    assert len(execs) == 1
    assert execs[0].findtext("t:Command", namespaces=NS) == r"C:\Program Files\Decky Loader\PluginLoader.exe"
    assert execs[0].findtext("t:WorkingDirectory", namespaces=NS) == r"C:\Program Files\Decky Loader"
    assert execs[0].find("t:Arguments", namespaces=NS) is None
    assert "wscript" not in xml.lower() and "cmd.exe" not in xml.lower() and "launch.js" not in xml


def test_task_xml_escapes_paths():
    xml = wi.build_task_xml(r"C:\A&B <x>\PluginLoader.exe", r"C:\A&B <x>", SID)
    root = ET.fromstring(xml)
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == r"C:\A&B <x>\PluginLoader.exe"
    assert root.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=NS) == r"C:\A&B <x>"


def test_wscript_launcher_is_gone():
    for name in ("LAUNCHER_NAME", "launcher_script", "js_string", "task_arguments", "user_temp_dir", "_stale_update_files"):
        assert not hasattr(wi, name), name


def test_system_directory_and_program_files_ignore_env(monkeypatch: pytest.MonkeyPatch):
    sysdir, pf = wi.system_directory(), wi.known_program_files()
    monkeypatch.setenv("SystemRoot", r"C:\evil")
    monkeypatch.setenv("ProgramFiles", r"C:\evil")
    monkeypatch.setenv("ProgramW6432", r"C:\evil")
    assert wi.system_directory() == sysdir and os.path.isfile(os.path.join(sysdir, "cmd.exe"))
    assert wi.known_program_files() == pf and os.path.isdir(pf)
    assert wi.program_dir() == os.path.join(pf, "Decky Loader")
    assert wi.program_dir(r"D:\PF") == r"D:\PF\Decky Loader"


def test_install_from_onefile_is_refused_with_instructions(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    calls: list[Any] = []
    rec = Recorder()
    rc = wi.install(None, onedir=lambda: False, is_admin=lambda: False, run=rec,
                    shell_execute=lambda *a: calls.append(a) or 33, current_sid=lambda: SID, is_admin_member=lambda: True,
                    exe=str(tmp_path / "PluginLoader.exe"), program_data=str(tmp_path / "pd"), program_files=str(tmp_path / "pf"))
    assert rc == 1 and calls == [] and rec.runs == []
    err = capsys.readouterr().err
    assert ("download PluginLoader-win-installer.zip from the release, extract it, and run PluginLoader.exe --install "
            "from there") in err or "PluginLoader-win-arm64-installer.zip" in err
    assert not (tmp_path / "pd").exists() and not (tmp_path / "pf").exists()


def test_onefile_message_names_the_right_zip():
    assert "download PluginLoader-win-installer.zip from the release" in wi.onefile_install_message("AMD64")
    assert "download PluginLoader-win-arm64-installer.zip from the release" in wi.onefile_install_message("ARM64")
    assert "run PluginLoader.exe --install from there" in wi.onefile_install_message("AMD64")


def test_install_points_task_at_the_program_files_exe(tmp_path: Path):
    src_exe = _fake_env(tmp_path)
    xmls: list[str] = []

    class XmlRecorder(Recorder):
        def __call__(self, argv: list[str], **kwargs: Any) -> Any:
            if argv[1] == "/Create":
                with open(argv[5], encoding="utf-16") as f:
                    xmls.append(f.read())
            return super().__call__(argv, **kwargs)

    assert _install(tmp_path, src_exe, run=XmlRecorder()) == 0
    app_dir = str(tmp_path / "Program Files" / "Decky Loader")
    task = ET.fromstring(xmls[0].encode("utf-16"))
    assert task.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == os.path.join(app_dir, "PluginLoader.exe")
    assert task.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=NS) == app_dir
    assert task.find("t:Actions/t:Exec/t:Arguments", namespaces=NS) is None


def test_current_user_sid_parses_whoami():
    sid = wi.current_user_sid()
    assert sid.startswith("S-1-5-")


def test_current_user_sid_parsing_with_injected_runner():
    class R:
        stdout = '"DOMAIN\\user","S-1-5-21-9-9-9-1000"\r\n'
    assert wi.current_user_sid(run=lambda *a, **k: R()) == "S-1-5-21-9-9-9-1000"


def _fake_env(tmp_path: Path) -> Path:
    # A onedir build: PluginLoader.exe with its _internal folder
    src_exe = tmp_path / "src" / "PluginLoader" / "PluginLoader.exe"
    (src_exe.parent / "_internal" / "sub").mkdir(parents=True)
    src_exe.write_bytes(b"exe")
    (src_exe.parent / "_internal" / "python313.dll").write_bytes(b"dll")
    (src_exe.parent / "_internal" / "sub" / "data.txt").write_bytes(b"data")
    (src_exe.parent.parent / "unrelated.txt").write_bytes(b"not part of the build")
    (src_exe.parent / "stray.txt").write_bytes(b"not part of the build")
    (tmp_path / "ProgramData").mkdir()
    (tmp_path / "Program Files").mkdir()
    return src_exe


def _app_dir(tmp_path: Path) -> Path:
    return tmp_path / "Program Files" / "Decky Loader"


def _tree(d: Path) -> dict[str, bytes]:
    return {str(p.relative_to(d)): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


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
    kw = dict(is_admin=lambda: True, run=Recorder(), shell_execute=lambda *a: None, onedir=lambda: True,
              exe=str(src_exe), program_data=str(tmp_path / "ProgramData"), program_files=str(tmp_path / "Program Files"),
              sleep=lambda s: None, defer_delete=lambda p: True,
              open_trusted=dirs.open_trusted, close_dir=dirs.close, create_dir=dirs.create, kill_running=lambda exe: [],
              system_dir=lambda: r"C:\Sys32")
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
    assert rec.runs[0] == [SCHTASKS, "/End", "/TN", "Decky Loader"]
    assert rec.runs[1][:4] == [SCHTASKS, "/Create", "/TN", "Decky Loader"]
    assert rec.runs[1][4] == "/XML" and rec.runs[1][6:] == ["/F"]
    assert os.path.dirname(rec.runs[1][5]) == root  # task XML staged inside the protected root
    assert rec.runs[2] == [SCHTASKS, "/Run", "/TN", "Decky Loader"]
    assert len(rec.runs) == 3
    assert dirs.created == [root, os.path.join(root, "plugins")]
    assert sorted(os.listdir(root)) == ["plugins"]  # no services\ or tmp\ any more, and the task XML is cleaned up
    assert os.listdir(os.path.join(root, "plugins")) == []
    # The whole onedir build, and nothing else from the folder it was run from
    assert _tree(_app_dir(tmp_path)) == {"PluginLoader.exe": b"exe", r"_internal\python313.dll": b"dll",
                                         r"_internal\sub\data.txt": b"data"}
    assert sorted(os.listdir(tmp_path / "Program Files")) == ["Decky Loader"]  # no staging leftovers
    assert "are not migrated" in capsys.readouterr().out


def test_root_handle_held_open_until_install_finishes(tmp_path):
    src_exe = _fake_env(tmp_path)
    events: list[tuple[str, str]] = []
    dirs = Dirs(events)
    root = str(tmp_path / "ProgramData" / "decky")
    _install(tmp_path, src_exe, dirs=dirs, run=Recorder(events))
    closes = [i for i, e in enumerate(events) if e == ("close", root)]
    runs = [i for i, e in enumerate(events) if e[0] == "run"]
    assert len(closes) == 1 and closes[0] > runs[-1]  # closed once, only after the last schtasks call


def test_root_handle_closed_when_install_fails(tmp_path):
    src_exe = _fake_env(tmp_path)
    dirs = Dirs()
    rc = _install(tmp_path, src_exe, dirs=dirs, copyfile=lambda a, b: (_ for _ in ()).throw(OSError("x")))
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


def test_junction_at_legacy_services_is_removed_as_link_only(tmp_path: Path):
    src_exe = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "victim.txt").write_text("v")
    services = root / "services"
    _junction(services, elsewhere)
    dirs = Dirs()
    dirs.trusted.add(str(root))
    assert _install(tmp_path, src_exe, dirs=dirs) == 0
    assert (elsewhere / "victim.txt").read_text() == "v"   # target untouched
    assert not os.path.lexists(services)
    assert str(services) not in dirs.created


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


def _old_install(tmp_path: Path) -> Path:
    app = _app_dir(tmp_path)
    (app / "_internal").mkdir(parents=True)
    (app / "PluginLoader.exe").write_bytes(b"old")
    (app / "_internal" / "gone.dll").write_bytes(b"old")
    return app


def test_existing_install_is_swapped_never_written_in_place(tmp_path: Path):
    src_exe = _fake_env(tmp_path)
    app = _old_install(tmp_path)
    pf = str(tmp_path / "Program Files")
    copies: list[str] = []
    trees: list[str] = []
    renames: list[tuple[str, str]] = []

    def copyfile(src: str, dst: str) -> None:
        copies.append(dst)
        shutil.copyfile(src, dst)

    def copytree(src: str, dst: str) -> None:
        trees.append(dst)
        shutil.copytree(src, dst)

    def rename(src: str, dst: str) -> None:
        renames.append((src, dst))
        os.rename(src, dst)

    assert _install(tmp_path, src_exe, copyfile=copyfile, copytree=copytree, rename=rename) == 0
    staged = os.path.dirname(copies[0])
    assert os.path.dirname(staged) == pf and os.path.basename(staged).startswith("Decky Loader.installing-")
    assert copies == [os.path.join(staged, "PluginLoader.exe")] and trees == [os.path.join(staged, "_internal")]
    old = renames[0][1]
    assert renames == [(str(app), old), (staged, str(app))]
    assert os.path.basename(old).startswith("Decky Loader.old-")
    assert _tree(app) == {"PluginLoader.exe": b"exe", r"_internal\python313.dll": b"dll", r"_internal\sub\data.txt": b"data"}
    assert sorted(os.listdir(pf)) == ["Decky Loader"]  # the old copy and the staging dir are gone


def test_swap_retries_while_the_install_is_locked(tmp_path: Path):
    src_exe = _fake_env(tmp_path)
    app = _old_install(tmp_path)
    attempts: list[str] = []
    sleeps: list[float] = []

    def flaky(src: str, dst: str) -> None:
        attempts.append(src)
        if len(attempts) < 4:
            raise PermissionError("locked")
        os.rename(src, dst)

    assert _install(tmp_path, src_exe, rename=flaky, sleep=sleeps.append) == 0
    assert sleeps == [1.0, 1.0, 1.0]
    assert attempts[:4] == [str(app)] * 4 and len(attempts) == 5  # moved aside on the 4th try, then swapped in
    assert (app / "PluginLoader.exe").read_bytes() == b"exe"


def test_swap_gives_up_after_ten_attempts_and_keeps_the_old_install(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src_exe = _fake_env(tmp_path)
    app = _old_install(tmp_path)
    rec = Recorder()
    attempts: list[str] = []
    sleeps: list[float] = []

    def locked(src: str, dst: str) -> None:
        attempts.append(src)
        raise PermissionError("locked")

    rc = _install(tmp_path, src_exe, run=rec, rename=locked, sleep=sleeps.append)
    assert rc == 1 and len(attempts) == 10 and len(sleeps) == 9
    assert "still in use" in capsys.readouterr().err
    assert not any(r[1:2] == ["/Create"] for r in rec.runs)
    assert (app / "PluginLoader.exe").read_bytes() == b"old"
    assert sorted(os.listdir(tmp_path / "Program Files")) == ["Decky Loader"]  # staging dir removed


def test_swap_puts_the_old_install_back_when_the_new_one_cannot_move_in(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src_exe = _fake_env(tmp_path)
    app = _old_install(tmp_path)

    def rename(src: str, dst: str) -> None:
        if os.path.basename(src).startswith("Decky Loader.installing-"):
            raise OSError("disk on fire")
        os.rename(src, dst)

    assert _install(tmp_path, src_exe, rename=rename) == 1
    assert "disk on fire" in capsys.readouterr().err
    assert _tree(app) == {"PluginLoader.exe": b"old", r"_internal\gone.dll": b"old"}
    assert sorted(os.listdir(tmp_path / "Program Files")) == ["Decky Loader"]


def test_stage_and_swap_without_an_existing_install(tmp_path: Path):
    src_exe = _fake_env(tmp_path)
    app = _app_dir(tmp_path)
    assert wi.stage_and_swap(str(src_exe), str(app), token=lambda: "t", sleep=lambda s: None) is True
    assert (app / "PluginLoader.exe").read_bytes() == b"exe" and (app / "_internal" / "python313.dll").is_file()
    assert sorted(os.listdir(tmp_path / "Program Files")) == ["Decky Loader"]


def test_copy_error_returns_1_with_message(tmp_path, capsys):
    src_exe = _fake_env(tmp_path)

    def broken(src, dst):
        raise OSError("disk full")

    rec = Recorder()
    assert _install(tmp_path, src_exe, run=rec, copytree=broken) == 1
    assert "disk full" in capsys.readouterr().err
    assert not any(r[1:2] == ["/Create"] for r in rec.runs)
    assert os.listdir(tmp_path / "Program Files") == []  # the half-staged copy is removed


def test_install_refuses_a_link_at_the_program_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src_exe = _fake_env(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _junction(_app_dir(tmp_path), elsewhere)
    rec = Recorder()
    killed: list[str] = []
    assert _install(tmp_path, src_exe, run=rec, kill_running=lambda exe: killed.append(exe) or list[int]()) == 1
    assert "junction or symbolic link" in capsys.readouterr().err
    assert rec.runs == [] and killed == [] and os.listdir(elsewhere) == []


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
    app = _old_install(tmp_path)
    copies: list = []
    rc = _install(tmp_path, app / "PluginLoader.exe", copyfile=lambda a, b: copies.append((a, b)),
                  mkdir=lambda p: copies.append(p))
    assert rc == 0 and copies == []
    assert (app / "PluginLoader.exe").read_bytes() == b"old"


def test_install_non_admin_elevates_for_current_sid(tmp_path):
    calls: list[tuple] = []
    rec = Recorder()
    rc = wi.install(None, is_admin=lambda: False, run=rec, shell_execute=lambda *a: calls.append(a) or 33,
                    current_sid=lambda: SID, is_admin_member=lambda: True, exe=r"C:\x\PluginLoader.exe",
                    program_data=str(tmp_path / "pd"), program_files=str(tmp_path / "pf"), onedir=lambda: True)
    assert rc == 0
    assert rec.runs == []
    assert not (tmp_path / "pd").exists() and not (tmp_path / "pf").exists()
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
                    current_sid=lambda: SID, is_admin_member=lambda: False, exe="x.exe", program_data=str(tmp_path),
                    onedir=lambda: True)
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
                    current_sid=lambda: SID, is_admin_member=lambda: True, exe="x.exe", program_data=str(tmp_path),
                    onedir=lambda: True)
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


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    '''An installed program dir plus a ProgramData root holding the earlier layout's leftovers, plugins and settings.'''
    app = _old_install(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    for sub in ("services", "tmp", "plugins", "settings"):
        (root / sub).mkdir(parents=True)
    (root / "services" / "PluginLoader.exe").write_bytes(b"x")
    (root / "services" / "launch.js").write_bytes(b"x")
    (root / "services" / "PluginLoader.exe.new").write_bytes(b"x")
    (root / "tmp" / "_MEI1234").mkdir()
    (root / "plugins" / "P").mkdir()
    (root / "settings" / "loader.json").write_bytes(b"{}")
    return app, root


def _uninstall(tmp_path: Path, **over: Any) -> int:
    kw: dict[str, Any] = dict(is_admin=lambda: True, run=Recorder(), kill_running=lambda exe: list[int](),
                              shell_execute=lambda *a: None, program_data=str(tmp_path / "ProgramData"),
                              program_files=str(tmp_path / "Program Files"), defer_delete=lambda p: True,
                              system_dir=lambda: r"C:\Sys32", open_trusted=lambda p: p, close_dir=lambda h: None)
    kw.update(over)
    return wi.uninstall(**kw)


def test_uninstall_removes_program_dir_and_legacy_dirs_but_keeps_plugins_and_settings(tmp_path: Path):
    app, root = _layout(tmp_path)
    rec = Recorder()
    assert _uninstall(tmp_path, run=rec) == 0
    assert rec.runs == [[SCHTASKS, "/End", "/TN", "Decky Loader"], [SCHTASKS, "/Delete", "/TN", "Decky Loader", "/F"]]
    assert not app.exists()
    assert sorted(os.listdir(root)) == ["plugins", "settings"]
    assert (root / "plugins" / "P").is_dir() and (root / "settings" / "loader.json").is_file()


def test_uninstall_without_anything_installed_succeeds(tmp_path: Path):
    (tmp_path / "ProgramData").mkdir()
    assert _uninstall(tmp_path) == 0


def test_uninstall_defers_locked_files_to_reboot(tmp_path: Path):
    _layout(tmp_path)
    deferred: list[str] = []
    rc = _uninstall(tmp_path, rmtree=_fake_rmtree(["a.exe", "b.dll"]), defer_delete=lambda p: deferred.append(p) or True)
    # program dir, legacy services, legacy tmp: each reports its locked files
    assert rc == 0 and deferred == ["a.exe", "b.dll"] * 3


def test_uninstall_fails_when_deferral_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _old_install(tmp_path)
    rc = _uninstall(tmp_path, rmtree=_fake_rmtree(["a.exe"]), defer_delete=lambda p: False)
    assert rc == 1
    assert "a.exe" in capsys.readouterr().err


def test_uninstall_removes_a_link_at_the_program_dir_without_following_it(tmp_path: Path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "victim.txt").write_text("v")
    (tmp_path / "Program Files").mkdir()
    (tmp_path / "ProgramData").mkdir()
    _junction(_app_dir(tmp_path), elsewhere)
    assert _uninstall(tmp_path) == 0
    assert not os.path.lexists(_app_dir(tmp_path)) and (elsewhere / "victim.txt").read_text() == "v"


def test_uninstall_non_admin_elevates(tmp_path):
    calls: list[tuple] = []
    rec = Recorder()
    rc = wi.uninstall(is_admin=lambda: False, run=rec, shell_execute=lambda *a: calls.append(a) or 33,
                      exe=r"C:\x\PluginLoader.exe", program_data=str(tmp_path), program_files=str(tmp_path),
                      rmtree=lambda p: None)
    assert rc == 0 and rec.runs == []
    assert calls[0][1] == "runas"
    assert calls[0][3].endswith("--uninstall")


def test_install_removes_the_legacy_layout_but_keeps_plugins_and_settings(tmp_path: Path):
    src_exe = _fake_env(tmp_path)
    shutil.rmtree(tmp_path / "Program Files")
    _, root = _layout(tmp_path)
    dirs = Dirs()
    dirs.trusted.update([str(root), str(root / "plugins")])
    assert _install(tmp_path, src_exe, dirs=dirs) == 0
    assert sorted(os.listdir(root)) == ["plugins", "settings"]
    assert (root / "plugins" / "P").is_dir() and (root / "settings" / "loader.json").is_file()
    assert dirs.created == []


def test_install_continues_when_a_legacy_dir_cannot_be_removed(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src_exe = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    (root / "services").mkdir(parents=True)
    dirs = Dirs()
    dirs.trusted.add(str(root))
    rec = Recorder()

    def rmtree(path: str, **kw: Any) -> None:
        if os.path.basename(path) == "services":
            (kw.get("onexc") or kw["onerror"])(os.remove, os.path.join(path, "PluginLoader.exe"), OSError("locked"))
        else:
            shutil.rmtree(path)

    assert _install(tmp_path, src_exe, dirs=dirs, run=rec, rmtree=rmtree, defer_delete=lambda p: False) == 0
    assert "earlier install layout" in capsys.readouterr().err
    assert any(r[1:2] == ["/Create"] for r in rec.runs)


# localplatformwin path layout

def _clear(monkeypatch):
    for k in ("PRIVILEGED_PATH", "UNPRIVILEGED_PATH"):
        monkeypatch.delenv(k, raising=False)


def _installed(monkeypatch, tmp_path):
    app = tmp_path / "Program Files" / "Decky Loader"
    app.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(app / "PluginLoader.exe"))
    monkeypatch.setattr(wi, "known_program_files", lambda: str(tmp_path / "Program Files"))
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
    monkeypatch.setattr(wi, "known_program_files", lambda: str(tmp_path / "Program Files"))
    for exe in (tmp_path / "other" / "PluginLoader.exe",
                tmp_path / "decky" / "services" / "PluginLoader.exe",  # the earlier layout no longer counts
                tmp_path / "Program Files" / "Decky Loader" / "_internal" / "PluginLoader.exe",
                tmp_path / "Program Files" / "Decky Loader" / "PluginLoader-win.exe"):
        monkeypatch.setattr(sys, "executable", str(exe))
        assert lpw.installed_root() is None, exe


def test_installed_root_is_programdata_decky_for_the_program_files_exe(monkeypatch, tmp_path):
    monkeypatch.setattr(lpw, "known_program_data", lambda: str(tmp_path / "pd"))
    monkeypatch.setattr(wi, "known_program_files", lambda: str(tmp_path / "pf"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "PF" / "decky loader" / "PLUGINLOADER.EXE"))
    assert lpw.installed_root() == str(tmp_path / "pd" / "decky")
    assert lpw.installed_root(program_data=r"D:\data", program_files=str(tmp_path / "pf")) == r"D:\data\decky"


def test_installed_root_ignores_spoofed_env(monkeypatch, tmp_path):
    fake = tmp_path / "fake"
    (fake / "Decky Loader").mkdir(parents=True)
    monkeypatch.setenv("PROGRAMDATA", str(fake))
    monkeypatch.setenv("ProgramFiles", str(fake))
    monkeypatch.setenv("ProgramW6432", str(fake))
    monkeypatch.setattr(sys, "executable", str(fake / "Decky Loader" / "PluginLoader.exe"))
    assert lpw.installed_root() is None


# Stopping every running copy of the installed exe

EXE = r"C:\Program Files\Decky Loader\PluginLoader.exe"


def test_matching_pids_compares_full_image_paths_case_insensitively():
    procs: list[tuple[int, str | None]] = [(10, EXE), (11, EXE.upper()), (12, r"C:\Users\me\Downloads\PluginLoader.exe"),
             (13, EXE + ".new"), (14, r"C:\Program Files\Decky Loader\..\Decky Loader\PluginLoader.exe"), (15, None)]
    assert wi.matching_pids(procs, EXE, own=set()) == [10, 11, 14]


def test_matching_pids_skips_this_process_and_its_parent():
    assert wi.matching_pids([(1, EXE), (2, EXE), (3, EXE)], EXE, own={1, 2}) == [3]


def test_terminate_image_kills_only_matching_processes():
    killed: list[int] = []
    procs: list[tuple[int, str | None]] = [(100, EXE), (200, r"C:\Windows\explorer.exe"), (300, EXE), (os.getpid(), EXE)]
    got = wi.terminate_image(EXE, processes=lambda: procs, terminate=killed.append)
    assert got == killed == [100, 300]


def test_terminate_image_keeps_going_when_one_kill_fails():
    killed: list[int] = []

    def terminate(pid: int) -> None:
        if pid == 100:
            raise PermissionError("gone")
        killed.append(pid)

    assert wi.terminate_image(EXE, processes=lambda: [(100, EXE), (300, EXE)], terminate=terminate,
                              wait_exit=lambda _pid: False) == [300]


def test_terminate_image_counts_a_process_that_was_already_exiting(caplog: pytest.LogCaptureFixture):
    def terminate(_pid: int) -> None:
        raise PermissionError(13, "Access is denied")

    with caplog.at_level("WARNING"):
        assert wi.terminate_image(EXE, processes=lambda: [(100, EXE)], terminate=terminate,
                                  wait_exit=lambda _pid: True) == [100]
    assert "Could not stop" not in caplog.text


def test_terminate_image_warns_when_a_denied_process_keeps_running(caplog: pytest.LogCaptureFixture):
    def terminate(_pid: int) -> None:
        raise PermissionError(13, "Access is denied")

    with caplog.at_level("WARNING"):
        assert wi.terminate_image(EXE, processes=lambda: [(100, EXE)], terminate=terminate,
                                  wait_exit=lambda _pid: False) == []
    assert "Could not stop process 100" in caplog.text


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_wait_pid_exit_on_real_processes():
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert wi.wait_pid_exit(p.pid, 2000)
    q = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert not wi.wait_pid_exit(q.pid, 100)
    finally:
        q.kill()


def test_list_processes_sees_this_process():
    me = [p for pid, p in wi.list_processes() if pid == os.getpid() and p is not None]
    assert me and os.path.isabs(me[0]) and os.path.basename(me[0]).lower().startswith("python")


def test_install_stops_running_copies_after_ending_the_task(tmp_path: Path):
    src_exe = _fake_env(tmp_path)
    events: list[tuple[str, str]] = []
    rc = _install(tmp_path, src_exe, run=Recorder(events), kill_running=lambda exe: events.append(("kill", exe)) or list[int]())
    assert rc == 0
    installed = str(tmp_path / "Program Files" / "Decky Loader" / "PluginLoader.exe")
    legacy = str(tmp_path / "ProgramData" / "decky" / "services" / "PluginLoader.exe")
    assert events[:3] == [("run", "/End"), ("kill", installed), ("kill", legacy)]


def test_uninstall_stops_running_copies_after_ending_the_task(tmp_path: Path):
    events: list[tuple[str, str]] = []
    pd = tmp_path / "ProgramData"
    (pd / "decky" / "services").mkdir(parents=True)
    rc = _uninstall(tmp_path, run=Recorder(events), kill_running=lambda exe: events.append(("kill", exe)) or list[int]())
    assert rc == 0
    assert events == [("run", "/End"), ("kill", str(tmp_path / "Program Files" / "Decky Loader" / "PluginLoader.exe")),
                      ("kill", str(pd / "decky" / "services" / "PluginLoader.exe")), ("run", "/Delete")]


def test_terminate_pid_really_stops_a_process():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        wi.terminate_pid(p.pid)
        assert p.wait(10) == 1
    finally:
        p.kill()


def test_parent_pid_from_a_real_snapshot():
    assert wi.parent_pid(os.getpid()) == os.getppid()


def test_parent_pid_lookup():
    snap = lambda: [(4, 0), (10, 4), (20, 10)]
    assert wi.parent_pid(20, snapshot=snap) == 10
    assert wi.parent_pid(99, snapshot=snap) is None


def test_system_schtasks_is_real():
    assert os.path.isfile(os.path.join(wi.system_directory(), "schtasks.exe"))


def test_terminate_image_survives_a_toolhelp_failure(caplog: pytest.LogCaptureFixture):
    def broken() -> list[tuple[int, str | None]]:
        raise OSError("CreateToolhelp32Snapshot failed")

    killed: list[int] = []
    assert wi.terminate_image(EXE, processes=broken, terminate=killed.append) == []
    assert killed == [] and "Could not list running processes" in caplog.text


def test_terminate_image_spares_this_process_and_its_whole_parent_chain():
    me = os.getpid()
    tree = [(me, 50), (50, 40), (40, 30), (30, 0), (77, 30), (88, 77)]
    procs: list[tuple[int, str | None]] = [(me, EXE), (50, EXE), (40, EXE), (30, EXE), (77, EXE), (88, EXE)]
    killed: list[int] = []
    assert wi.terminate_image(EXE, processes=lambda: procs, terminate=killed.append, snapshot=lambda: tree) == [77, 88]


def test_terminate_image_still_spares_self_and_parent_when_the_tree_cannot_be_read(caplog: pytest.LogCaptureFixture):
    def broken() -> list[tuple[int, int]]:
        raise OSError("snapshot failed")

    procs: list[tuple[int, str | None]] = [(os.getpid(), EXE), (os.getppid(), EXE), (77, EXE)]
    killed: list[int] = []
    assert wi.terminate_image(EXE, processes=lambda: procs, terminate=killed.append, snapshot=broken) == [77]
    assert "Could not read the process tree" in caplog.text


def test_own_ancestry_stops_at_cycles_and_unknown_parents():
    assert wi.own_ancestry(5, [(5, 4), (4, 5)]) == {4, 5}
    assert wi.own_ancestry(5, [(5, 4)]) == {4, 5}
    assert wi.own_ancestry(5, []) == {5}


def test_own_ancestry_from_a_real_snapshot():
    chain = wi.own_ancestry(os.getpid(), wi.process_snapshot())
    assert {os.getpid(), os.getppid()} <= chain


# Legacy layout removal only after a successful swap; staging and leftovers

def test_install_keeps_the_legacy_layout_when_the_swap_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src_exe = _fake_env(tmp_path)
    _, root = _layout(tmp_path)
    dirs = Dirs()
    dirs.trusted.update([str(root), str(root / "plugins")])

    def rename(src: str, dst: str) -> None:
        if os.path.basename(src).startswith("Decky Loader.installing-"):
            raise OSError("disk on fire")
        os.rename(src, dst)

    assert _install(tmp_path, src_exe, dirs=dirs, rename=rename) == 1
    # The earlier layout still works: its exe and launcher are untouched
    assert (root / "services" / "PluginLoader.exe").is_file() and (root / "tmp").is_dir()


def test_install_removes_the_legacy_layout_only_after_the_swap(tmp_path: Path):
    src_exe = _fake_env(tmp_path)
    _, root = _layout(tmp_path)
    dirs = Dirs()
    dirs.trusted.update([str(root), str(root / "plugins")])
    seen: list[bool] = []

    def rename(src: str, dst: str) -> None:
        seen.append((root / "services").exists())
        os.rename(src, dst)

    assert _install(tmp_path, src_exe, dirs=dirs, rename=rename) == 0
    assert seen and all(seen)
    assert sorted(os.listdir(root)) == ["plugins", "settings"]


def test_stage_and_swap_does_not_discard_a_staging_dir_it_could_not_create(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src_exe = _fake_env(tmp_path)
    app = _app_dir(tmp_path)
    discarded: list[str] = []

    def mkdir(p: str) -> None:
        raise FileExistsError(p)

    ok = wi.stage_and_swap(str(src_exe), str(app), mkdir=mkdir, discard=lambda p: discarded.append(p) or list[str](),
                           listdir=lambda p: list[str](), token=lambda: "t", sleep=lambda s: None)
    assert ok is False and discarded == []
    assert "Decky Loader.installing-t" in capsys.readouterr().err


def test_stage_and_swap_still_discards_its_own_half_staged_copy(tmp_path: Path):
    src_exe = _fake_env(tmp_path)
    app = _app_dir(tmp_path)

    def broken(src: str, dst: str) -> None:
        raise OSError("disk full")

    assert wi.stage_and_swap(str(src_exe), str(app), copytree=broken, token=lambda: "t", sleep=lambda s: None) is False
    assert os.listdir(tmp_path / "Program Files") == []


def test_stage_and_swap_cleans_copies_left_by_earlier_installs(tmp_path: Path):
    src_exe = _fake_env(tmp_path)
    pf = tmp_path / "Program Files"
    app = _old_install(tmp_path)
    for leftover in ("Decky Loader.old-aaa", "Decky Loader.installing-bbb", "decky loader.OLD-ccc"):
        (pf / leftover / "_internal").mkdir(parents=True)
        (pf / leftover / "PluginLoader.exe").write_bytes(b"stale")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "victim.txt").write_text("v")
    _junction(pf / "Decky Loader.old-link", elsewhere)
    for unrelated in ("Decky Loader Extra", "Other.old-x", "Decky Loader.untrusted-1"):
        (pf / unrelated).mkdir()

    assert wi.stage_and_swap(str(src_exe), str(app), token=lambda: "t", sleep=lambda s: None) is True
    assert sorted(os.listdir(pf)) == ["Decky Loader", "Decky Loader Extra", "Decky Loader.untrusted-1", "Other.old-x"]
    assert (elsewhere / "victim.txt").read_text() == "v"  # the link was removed, not followed
    assert (app / "PluginLoader.exe").read_bytes() == b"exe"


def test_stale_copy_cleanup_is_best_effort(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    src_exe = _fake_env(tmp_path)
    pf = tmp_path / "Program Files"
    (pf / "Decky Loader.old-aaa").mkdir()
    app = _app_dir(tmp_path)

    def discard(p: str) -> list[str]:
        if p.endswith("old-aaa"):
            return [p]
        return wi.remove_tree(p)

    assert wi.stage_and_swap(str(src_exe), str(app), discard=discard, token=lambda: "t", sleep=lambda s: None) is True
    assert "Decky Loader.old-aaa" in caplog.text
    assert (app / "PluginLoader.exe").read_bytes() == b"exe"


def test_stale_copies_listing_failure_is_ignored():
    def broken(p: str) -> list[str]:
        raise OSError("gone")
    assert wi.stale_copies(APP_DIR, listdir=broken) == []
    assert wi.stale_copies(APP_DIR, listdir=lambda p: ["Decky Loader.old-1", "Decky Loader", "Decky Loader.installing-2"]) == [
        r"C:\Program Files\Decky Loader.old-1", r"C:\Program Files\Decky Loader.installing-2"]


# uninstall validates the ProgramData root before touching the legacy layout

def test_uninstall_leaves_an_untrusted_root_alone(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    app, root = _layout(tmp_path)
    opened: list[str] = []
    rc = _uninstall(tmp_path, open_trusted=lambda p: opened.append(p) or None)
    assert rc == 0 and opened == [str(root)]
    assert not app.exists()  # the program dir is still removed
    assert sorted(os.listdir(root)) == ["plugins", "services", "settings", "tmp"]
    assert "not owned and protected" in capsys.readouterr().err


def test_uninstall_holds_the_trusted_root_open_while_removing_legacy_dirs(tmp_path: Path):
    _, root = _layout(tmp_path)
    events: list[str] = []

    def rmtree(path: str, **kw: Any) -> None:
        events.append("rmtree " + os.path.basename(path))
        shutil.rmtree(path)

    rc = _uninstall(tmp_path, rmtree=rmtree, open_trusted=lambda p: events.append("open") or p,
                    close_dir=lambda h: events.append("close"))
    assert rc == 0
    assert events == ["rmtree Decky Loader", "open", "rmtree services", "rmtree tmp", "close"]
    assert sorted(os.listdir(root)) == ["plugins", "settings"]


def test_uninstall_with_the_real_check_refuses_a_user_made_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    # tmp_path is owned by the current user with an inherited DACL, so the real check must not trust it
    _, root = _layout(tmp_path)
    kw: dict[str, Any] = dict(is_admin=lambda: True, run=Recorder(), kill_running=lambda exe: list[int](),
                              shell_execute=lambda *a: None, program_data=str(tmp_path / "ProgramData"),
                              program_files=str(tmp_path / "Program Files"), defer_delete=lambda p: True,
                              system_dir=lambda: r"C:\Sys32")
    assert wi.uninstall(**kw) == 0
    assert (root / "services" / "PluginLoader.exe").is_file()
