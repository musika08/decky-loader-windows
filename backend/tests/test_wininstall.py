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
TASK_EXE = APP_DIR + r"\python\pythonw.exe"
TASK_ARGS = '"' + APP_DIR + r'\app\main.py"'


def test_task_xml_runs_highest_at_user_logon():
    xml = wi.build_task_xml(TASK_EXE, APP_DIR, SID, TASK_ARGS)
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


def test_task_xml_runs_the_installed_runtime_on_main_py():
    xml = wi.build_task_xml(TASK_EXE, APP_DIR, SID, TASK_ARGS)
    root = ET.fromstring(xml)
    execs = root.findall("t:Actions/t:Exec", namespaces=NS)
    assert len(execs) == 1
    assert execs[0].findtext("t:Command", namespaces=NS) == r"C:\Program Files\Decky Loader\python\pythonw.exe"
    assert execs[0].findtext("t:Arguments", namespaces=NS) == r'"C:\Program Files\Decky Loader\app\main.py"'
    assert execs[0].findtext("t:WorkingDirectory", namespaces=NS) == r"C:\Program Files\Decky Loader"
    # The schema wants Command, Arguments, WorkingDirectory in this order
    assert [c.tag.split("}")[1] for c in execs[0]] == ["Command", "Arguments", "WorkingDirectory"]
    assert "wscript" not in xml.lower() and "cmd.exe" not in xml.lower() and "launch.js" not in xml
    assert "PluginLoader" not in xml


def test_task_xml_without_arguments_has_no_arguments_element():
    root = ET.fromstring(wi.build_task_xml(r"C:\x\app.exe", r"C:\x", SID))
    assert root.find("t:Actions/t:Exec/t:Arguments", namespaces=NS) is None


def test_task_xml_escapes_paths():
    xml = wi.build_task_xml(r"C:\A&B <x>\python\pythonw.exe", r"C:\A&B <x>", SID, r'"C:\A&B <x>\app\main.py"')
    root = ET.fromstring(xml)
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == r"C:\A&B <x>\python\pythonw.exe"
    assert root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS) == r'"C:\A&B <x>\app\main.py"'
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


def test_install_from_a_pyinstaller_build_is_refused_with_instructions(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    calls: list[Any] = []
    rec = Recorder()
    rc = wi.install(None, frozen=lambda: True, is_admin=lambda: False, run=rec,
                    shell_execute=lambda *a: calls.append(a) or 33, current_sid=lambda: SID, is_admin_member=lambda: True,
                    exe=str(tmp_path / "PluginLoader.exe"), program_data=str(tmp_path / "pd"), program_files=str(tmp_path / "pf"))
    assert rc == 1 and calls == [] and rec.runs == []
    err = capsys.readouterr().err
    assert "portable build and cannot be installed" in err
    assert r"run python\python.exe app\main.py --install from that folder" in err
    assert not (tmp_path / "pd").exists() and not (tmp_path / "pf").exists()


@pytest.mark.parametrize("layout", ["venv", "no app folder", "other script", "pythonw elsewhere"])
def test_install_outside_an_installer_folder_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str], layout: str):
    stage = _fake_env(tmp_path)
    exe, script = stage / "python" / "python.exe", stage / "app" / "main.py"
    if layout == "venv":
        exe, script = tmp_path / "backend" / ".venv" / "Scripts" / "python.exe", tmp_path / "backend" / "main.py"
    elif layout == "no app folder":
        shutil.rmtree(stage / "app")
    elif layout == "other script":
        script = stage / "main.py"
    else:
        exe = tmp_path / "python" / "pythonw.exe"
    calls: list[Any] = []
    rec = Recorder()
    rc = wi.install(None, frozen=lambda: False, is_admin=lambda: False, run=rec,
                    shell_execute=lambda *a: calls.append(a) or 33, current_sid=lambda: SID, is_admin_member=lambda: True,
                    exe=str(exe), script=str(script), program_data=str(tmp_path / "pd"), program_files=str(tmp_path / "pf"))
    assert rc == 1 and calls == [] and rec.runs == []
    assert "only works from an installer folder" in capsys.readouterr().err
    assert not (tmp_path / "pd").exists() and not (tmp_path / "pf").exists()


def test_stage_root_accepts_python_and_pythonw(tmp_path: Path):
    stage = _fake_env(tmp_path)
    for name in ("python.exe", "PYTHONW.EXE"):
        assert wi.stage_root(str(stage / "python" / name), str(stage / "app" / "main.py")) == str(stage)
    assert wi.stage_root(str(stage / "python" / "python3.exe"), str(stage / "app" / "main.py")) is None
    assert wi.stage_root(str(stage / "python" / "python.exe"), None) is None


def test_install_points_task_at_the_program_files_runtime(tmp_path: Path):
    src = _fake_env(tmp_path)
    xmls: list[str] = []

    class XmlRecorder(Recorder):
        def __call__(self, argv: list[str], **kwargs: Any) -> Any:
            if argv[1] == "/Create":
                with open(argv[5], encoding="utf-16") as f:
                    xmls.append(f.read())
            return super().__call__(argv, **kwargs)

    assert _install(tmp_path, src, run=XmlRecorder()) == 0
    app_dir = str(tmp_path / "Program Files" / "Decky Loader")
    task = ET.fromstring(xmls[0].encode("utf-16"))
    assert task.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == os.path.join(app_dir, "python", "pythonw.exe")
    # -I first (PYTHON* variables ignored); "Program Files" has a space, so the script path is quoted
    assert task.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS) == '-I "' + os.path.join(app_dir, "app", "main.py") + '"'
    assert task.findtext("t:Actions/t:Exec/t:WorkingDirectory", namespaces=NS) == app_dir


def test_current_user_sid_parses_whoami():
    sid = wi.current_user_sid()
    assert sid.startswith("S-1-5-")


def test_current_user_sid_parsing_with_injected_runner():
    class R:
        stdout = '"DOMAIN\\user","S-1-5-21-9-9-9-1000"\r\n'
    assert wi.current_user_sid(run=lambda *a, **k: R()) == "S-1-5-21-9-9-9-1000"


# What an installer folder holds (tools\build_win_runtime.py): the runtime and the program
STAGE_TREE = {r"python\python.exe": b"py", r"python\pythonw.exe": b"pyw", r"python\python313._pth": b"Lib\r\nDLLs\r\n..\\app\r\n",
              r"python\Lib\os.py": b"os", r"app\main.py": b"main", r"app\decky_loader\__init__.py": b"pkg"}


def _write_tree(root: Path, tree: dict[str, bytes]) -> None:
    for rel, data in tree.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)


def _fake_env(tmp_path: Path) -> Path:
    '''An installer folder (with a space in its path) and the empty ProgramData/Program Files. Returns the folder.'''
    stage = tmp_path / "src" / "Decky Loader installer"
    _write_tree(stage, STAGE_TREE)
    (stage.parent / "unrelated.txt").write_bytes(b"not part of the build")
    (stage / "stray.txt").write_bytes(b"not part of the build")
    (tmp_path / "ProgramData").mkdir()
    (tmp_path / "Program Files").mkdir()
    return stage


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


def _no_cef_debug(sid: str) -> None:
    pass  # never touch the real Steam folder


def _install(tmp_path: Path, stage: Path, dirs: Dirs | None = None, **over: Any) -> int:
    dirs = dirs or Dirs()
    kw = dict(is_admin=lambda: True, run=Recorder(), shell_execute=lambda *a: None, frozen=lambda: False,
              exe=str(stage / "python" / "python.exe"), script=str(stage / "app" / "main.py"),
              program_data=str(tmp_path / "ProgramData"), program_files=str(tmp_path / "Program Files"),
              sleep=lambda s: None, defer_delete=lambda p: True,
              open_trusted=dirs.open_trusted, close_dir=dirs.close, create_dir=dirs.create, kill_running=lambda exe: [],
              system_dir=lambda: r"C:\Sys32", cef_debug=_no_cef_debug)
    kw.update(over)
    return wi.install(SID, **kw)


def _junction(link, target):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def test_secure_sddl_verbatim():
    assert wi.SECURE_SDDL == "O:BAG:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)"


def test_install_command_sequence_has_no_icacls_and_no_migration(tmp_path, capsys):
    src = _fake_env(tmp_path)
    rec, dirs = Recorder(), Dirs()
    rc = _install(tmp_path, src, dirs=dirs, run=rec)

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
    # The installer folder's python and app folders, and nothing else from it
    assert _tree(_app_dir(tmp_path)) == STAGE_TREE
    assert sorted(os.listdir(tmp_path / "Program Files")) == ["Decky Loader"]  # no staging leftovers
    assert "are not migrated" in capsys.readouterr().out


def test_root_handle_held_open_until_install_finishes(tmp_path):
    src = _fake_env(tmp_path)
    events: list[tuple[str, str]] = []
    dirs = Dirs(events)
    root = str(tmp_path / "ProgramData" / "decky")
    _install(tmp_path, src, dirs=dirs, run=Recorder(events))
    closes = [i for i, e in enumerate(events) if e == ("close", root)]
    runs = [i for i, e in enumerate(events) if e[0] == "run"]
    assert len(closes) == 1 and closes[0] > runs[-1]  # closed once, only after the last schtasks call


def test_root_handle_closed_when_install_fails(tmp_path):
    src = _fake_env(tmp_path)
    dirs = Dirs()
    rc = _install(tmp_path, src, dirs=dirs, copytree=lambda a, b: (_ for _ in ()).throw(OSError("x")))
    assert rc == 1
    assert str(tmp_path / "ProgramData" / "decky") in dirs.closed


def test_untrusted_root_is_renamed_aside_and_recreated(tmp_path, capsys):
    src = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    root.mkdir()
    (root / "evil.txt").write_text("x")
    dirs = Dirs()
    when = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
    assert _install(tmp_path, src, dirs=dirs) == 0
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
    src = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    root.mkdir()
    (root / "keep.txt").write_text("x")
    dirs = Dirs()
    dirs.trusted.add(str(root))
    assert _install(tmp_path, src, dirs=dirs) == 0
    assert str(root) not in dirs.created
    assert (root / "keep.txt").is_file()
    assert not [p for p in os.listdir(tmp_path / "ProgramData") if "untrusted" in p]


def test_junction_root_is_renamed_as_link_only(tmp_path):
    src = _fake_env(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "victim.txt").write_text("v")
    root = tmp_path / "ProgramData" / "decky"
    _junction(root, elsewhere)
    dirs = Dirs()
    dirs.trusted.add(str(root))  # even a junction that points at a "trusted" dir must be rejected via its reparse bit
    assert _install(tmp_path, src, dirs=dirs) == 0
    assert (elsewhere / "victim.txt").read_text() == "v"
    assert not (elsewhere / "PluginLoader.exe").exists()
    assert not wi.is_reparse_point(str(root)) and root.is_dir()


def test_junction_at_legacy_services_is_removed_as_link_only(tmp_path: Path):
    src = _fake_env(tmp_path)
    root = tmp_path / "ProgramData" / "decky"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "victim.txt").write_text("v")
    services = root / "services"
    _junction(services, elsewhere)
    dirs = Dirs()
    dirs.trusted.add(str(root))
    assert _install(tmp_path, src, dirs=dirs) == 0
    assert (elsewhere / "victim.txt").read_text() == "v"   # target untouched
    assert not os.path.lexists(services)
    assert str(services) not in dirs.created


def test_creation_race_fails_closed(tmp_path, capsys):
    src = _fake_env(tmp_path)

    def racing_create(p):
        raise FileExistsError(p)

    rc = _install(tmp_path, src, create_dir=racing_create)
    assert rc == 1 and "Could not prepare" in capsys.readouterr().err


def test_freshly_created_dir_is_revalidated(tmp_path, capsys):
    src = _fake_env(tmp_path)
    dirs = Dirs()

    def create_untrusted(p):
        os.mkdir(p)  # created, but not owned/protected as required
        dirs.created.append(p)

    rc = _install(tmp_path, src, dirs=dirs, create_dir=create_untrusted)
    assert rc == 1 and "right after creation" in capsys.readouterr().err


def _old_install(tmp_path: Path) -> Path:
    '''What the earlier installed mode left in Program Files: the onedir PluginLoader.exe and its _internal folder.'''
    app = _app_dir(tmp_path)
    (app / "_internal").mkdir(parents=True)
    (app / "PluginLoader.exe").write_bytes(b"old")
    (app / "_internal" / "gone.dll").write_bytes(b"old")
    return app


def test_existing_install_is_swapped_never_written_in_place(tmp_path: Path):
    src = _fake_env(tmp_path)
    app = _old_install(tmp_path)
    pf = str(tmp_path / "Program Files")
    trees: list[tuple[str, str]] = []
    renames: list[tuple[str, str]] = []

    def copytree(s: str, d: str) -> None:
        trees.append((s, d))
        wi.copy_tree_plain(s, d)

    def rename(s: str, d: str) -> None:
        renames.append((s, d))
        os.rename(s, d)

    assert _install(tmp_path, src, copytree=copytree, rename=rename) == 0
    staged = os.path.dirname(trees[0][1])
    assert os.path.dirname(staged) == pf and os.path.basename(staged).startswith("Decky Loader.installing-")
    assert trees == [(str(src / "python"), os.path.join(staged, "python")), (str(src / "app"), os.path.join(staged, "app"))]
    old = renames[0][1]
    assert renames == [(str(app), old), (staged, str(app))]
    assert os.path.basename(old).startswith("Decky Loader.old-")
    # The earlier layout's PluginLoader.exe and _internal went with the old copy
    assert _tree(app) == STAGE_TREE
    assert sorted(os.listdir(pf)) == ["Decky Loader"]  # the old copy and the staging dir are gone


def test_swap_retries_while_the_install_is_locked(tmp_path: Path):
    src = _fake_env(tmp_path)
    app = _old_install(tmp_path)
    attempts: list[str] = []
    sleeps: list[float] = []

    def flaky(src: str, dst: str) -> None:
        attempts.append(src)
        if len(attempts) < 4:
            raise PermissionError("locked")
        os.rename(src, dst)

    assert _install(tmp_path, src, rename=flaky, sleep=sleeps.append) == 0
    assert sleeps == [1.0, 1.0, 1.0]
    assert attempts[:4] == [str(app)] * 4 and len(attempts) == 5  # moved aside on the 4th try, then swapped in
    assert _tree(app) == STAGE_TREE


def test_swap_gives_up_after_ten_attempts_and_keeps_the_old_install(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src = _fake_env(tmp_path)
    app = _old_install(tmp_path)
    rec = Recorder()
    attempts: list[str] = []
    sleeps: list[float] = []

    def locked(src: str, dst: str) -> None:
        attempts.append(src)
        raise PermissionError("locked")

    rc = _install(tmp_path, src, run=rec, rename=locked, sleep=sleeps.append)
    assert rc == 1 and len(attempts) == 10 and len(sleeps) == 9
    assert "still in use" in capsys.readouterr().err
    assert not any(r[1:2] == ["/Create"] for r in rec.runs)
    assert (app / "PluginLoader.exe").read_bytes() == b"old"
    assert sorted(os.listdir(tmp_path / "Program Files")) == ["Decky Loader"]  # staging dir removed


def test_swap_puts_the_old_install_back_when_the_new_one_cannot_move_in(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src = _fake_env(tmp_path)
    app = _old_install(tmp_path)

    def rename(src: str, dst: str) -> None:
        if os.path.basename(src).startswith("Decky Loader.installing-"):
            raise OSError("disk on fire")
        os.rename(src, dst)

    assert _install(tmp_path, src, rename=rename) == 1
    assert "disk on fire" in capsys.readouterr().err
    assert _tree(app) == {"PluginLoader.exe": b"old", r"_internal\gone.dll": b"old"}
    assert sorted(os.listdir(tmp_path / "Program Files")) == ["Decky Loader"]


def test_stage_and_swap_without_an_existing_install(tmp_path: Path):
    src = _fake_env(tmp_path)
    app = _app_dir(tmp_path)
    assert wi.stage_and_swap(str(src), str(app), token=lambda: "t", sleep=lambda s: None) is True
    assert _tree(app) == STAGE_TREE
    assert sorted(os.listdir(tmp_path / "Program Files")) == ["Decky Loader"]


def test_copy_error_returns_1_with_message(tmp_path, capsys):
    src = _fake_env(tmp_path)

    def broken(src, dst):
        raise OSError("disk full")

    rec = Recorder()
    assert _install(tmp_path, src, run=rec, copytree=broken) == 1
    assert "disk full" in capsys.readouterr().err
    assert not any(r[1:2] == ["/Create"] for r in rec.runs)
    assert os.listdir(tmp_path / "Program Files") == []  # the half-staged copy is removed


def test_install_refuses_a_link_at_the_program_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src = _fake_env(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _junction(_app_dir(tmp_path), elsewhere)
    rec = Recorder()
    killed: list[str] = []
    assert _install(tmp_path, src, run=rec, kill_running=lambda exe: killed.append(exe) or list[int]()) == 1
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


def test_install_from_the_installed_copy_skips_copy(tmp_path: Path):
    _fake_env(tmp_path)
    app = _app_dir(tmp_path)
    _write_tree(app, STAGE_TREE)
    copies: list[Any] = []
    rc = _install(tmp_path, app, copytree=lambda a, b: copies.append((a, b)), mkdir=lambda p: copies.append(p))
    assert rc == 0 and copies == []
    assert _tree(app) == STAGE_TREE


def _elevating_install(tmp_path: Path, calls: list[Any], **over: Any) -> int:
    stage = _fake_env(tmp_path)
    kw: dict[str, Any] = dict(is_admin=lambda: False, run=Recorder(), shell_execute=lambda *a: calls.append(a) or 33,
                              current_sid=lambda: SID, is_admin_member=lambda: True, frozen=lambda: False,
                              exe=str(stage / "python" / "python.exe"), script=str(stage / "app" / "main.py"),
                              program_data=str(tmp_path / "pd"), program_files=str(tmp_path / "pf"))
    kw.update(over)
    return wi.install(None, **kw)


def test_install_non_admin_elevates_the_same_runtime_and_script_for_current_sid(tmp_path: Path):
    calls: list[Any] = []
    rec = Recorder()
    assert _elevating_install(tmp_path, calls, run=rec) == 0
    assert rec.runs == []
    assert not (tmp_path / "pd").exists() and not (tmp_path / "pf").exists()
    assert len(calls) == 1
    hwnd, verb, file, params, cwd, show = calls[0]
    assert hwnd is None and verb == "runas" and show == 1
    stage = tmp_path / "src" / "Decky Loader installer"
    assert file == str(stage / "python" / "python.exe")
    # -I right after the interpreter; the script path has spaces, so it is quoted, and it comes before the arguments
    assert params == f'-I "{stage / "app" / "main.py"}" --install --for-sid {SID}'


def test_install_non_admin_account_refused_without_elevating(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    calls: list[Any] = []
    assert _elevating_install(tmp_path, calls, is_admin_member=lambda: False) == 1
    assert calls == []
    assert "requires an administrator account" in capsys.readouterr().err


def test_elevation_params_quote_paths_with_spaces():
    assert wi.elevation_params([r"C:\a b\python\python.exe", r"C:\a b\app\main.py"], ["--install", "--for-sid", SID]) == (
        r"C:\a b\python\python.exe", rf'"C:\a b\app\main.py" --install --for-sid {SID}')
    assert wi.elevation_params([r"C:\x\PluginLoader.exe"], ["--uninstall"]) == (r"C:\x\PluginLoader.exe", "--uninstall")
    assert wi.elevation_params([r"C:\x\python.exe", r"C:\x\main.py"], []) == (r"C:\x\python.exe", r"C:\x\main.py")


def test_user_in_administrators_parsing():
    class R:
        def __init__(self, out): self.stdout = out
    member = '"BUILTIN\\Administrators","Alias","S-1-5-32-544","Group used for deny only"\r\n"Everyone","Well-known group","S-1-1-0","Mandatory group"\r\n'
    other = '"Everyone","Well-known group","S-1-1-0","Mandatory group"\r\n"BUILTIN\\Users","Alias","S-1-5-32-545","Mandatory group"\r\n'
    assert wi.user_in_administrators(run=lambda *a, **k: R(member)) is True
    assert wi.user_in_administrators(run=lambda *a, **k: R(other)) is False


def test_install_elevation_failure_returns_nonzero(tmp_path: Path):
    assert _elevating_install(tmp_path, [], shell_execute=lambda *a: 5) == 1


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


def test_uninstall_non_admin_elevates(tmp_path: Path):
    calls: list[Any] = []
    rec = Recorder()
    app = _app_dir(tmp_path)
    _write_tree(app, STAGE_TREE)
    exe, script = str(app / "python" / "python.exe"), str(app / "app" / "main.py")
    rc = wi.uninstall(is_admin=lambda: False, run=rec, shell_execute=lambda *a: calls.append(a) or 33, exe=exe, script=script,
                      frozen=lambda: False, program_data=str(tmp_path), program_files=str(tmp_path), rmtree=lambda p: None)
    assert rc == 0 and rec.runs == []
    assert calls[0][1] == "runas"
    assert calls[0][2] == exe
    assert calls[0][3] == f'-I "{script}" --uninstall'


def test_uninstall_from_a_checkout_elevates_without_isolation(tmp_path: Path):
    # A development venv is not our runtime: -I would drop the checkout from sys.path
    calls: list[Any] = []
    rc = wi.uninstall(is_admin=lambda: False, run=Recorder(), shell_execute=lambda *a: calls.append(a) or 33,
                      exe=r"C:\src\backend\.venv\Scripts\python.exe", script=r"C:\src\backend\main.py", frozen=lambda: False,
                      program_data=str(tmp_path), program_files=str(tmp_path))
    assert rc == 0 and calls[0][3] == r"C:\src\backend\main.py --uninstall"


def test_uninstall_non_admin_elevates_a_portable_exe_without_a_script(tmp_path: Path):
    calls: list[Any] = []
    rc = wi.uninstall(is_admin=lambda: False, run=Recorder(), shell_execute=lambda *a: calls.append(a) or 33,
                      exe=r"C:\x\PluginLoader.exe", script=r"C:\ignored\main.py", frozen=lambda: True,
                      program_data=str(tmp_path), program_files=str(tmp_path))
    assert rc == 0 and calls[0][2:4] == (r"C:\x\PluginLoader.exe", "--uninstall")


def test_install_removes_the_legacy_layout_but_keeps_plugins_and_settings(tmp_path: Path):
    src = _fake_env(tmp_path)
    shutil.rmtree(tmp_path / "Program Files")
    _, root = _layout(tmp_path)
    dirs = Dirs()
    dirs.trusted.update([str(root), str(root / "plugins")])
    assert _install(tmp_path, src, dirs=dirs) == 0
    assert sorted(os.listdir(root)) == ["plugins", "settings"]
    assert (root / "plugins" / "P").is_dir() and (root / "settings" / "loader.json").is_file()
    assert dirs.created == []


def test_install_continues_when_a_legacy_dir_cannot_be_removed(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src = _fake_env(tmp_path)
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

    assert _install(tmp_path, src, dirs=dirs, run=rec, rmtree=rmtree, defer_delete=lambda p: False) == 0
    assert "earlier install layout" in capsys.readouterr().err
    assert any(r[1:2] == ["/Create"] for r in rec.runs)


# localplatformwin path layout

def _clear(monkeypatch):
    for k in ("PRIVILEGED_PATH", "UNPRIVILEGED_PATH"):
        monkeypatch.delenv(k, raising=False)


def _running(monkeypatch: pytest.MonkeyPatch, exe: Path | str, script: Path | str | None) -> None:
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(lpw, "running_main_script", lambda: None if script is None else str(script))


def _installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, interpreter: str = "pythonw.exe") -> None:
    app = tmp_path / "Program Files" / "Decky Loader"
    app.mkdir(parents=True)
    _running(monkeypatch, app / "python" / interpreter, app / "app" / "main.py")
    monkeypatch.setattr(wi, "known_program_files", lambda: str(tmp_path / "Program Files"))
    monkeypatch.setattr(lpw, "known_program_data", lambda: str(tmp_path))


def test_privileged_path_env_wins_in_portable_mode(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setattr(lpw, "known_program_data", lambda: str(tmp_path / "pd"))
    _running(monkeypatch, tmp_path / "elsewhere" / "PluginLoader.exe", None)
    monkeypatch.setenv("PRIVILEGED_PATH", str(tmp_path / "priv"))
    assert lpw.get_privileged_path() == str(tmp_path / "priv")


def test_privileged_path_env_ignored_in_installed_mode(monkeypatch, tmp_path):
    _clear(monkeypatch)
    _installed(monkeypatch, tmp_path)
    monkeypatch.setenv("PRIVILEGED_PATH", str(tmp_path / "evil"))
    assert os.path.normcase(lpw.get_privileged_path()) == os.path.normcase(str(tmp_path / "decky"))
    assert not (tmp_path / "evil").exists()


@pytest.mark.parametrize("interpreter", ["python.exe", "pythonw.exe"])
def test_privileged_path_installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, interpreter: str):
    _clear(monkeypatch)
    _installed(monkeypatch, tmp_path, interpreter)
    assert os.path.normcase(lpw.get_privileged_path()) == os.path.normcase(str(tmp_path / "decky"))


def test_privileged_path_portable(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setattr(lpw, "known_program_data", lambda: str(tmp_path / "pd"))
    _running(monkeypatch, tmp_path / "elsewhere" / "PluginLoader.exe", None)
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


def test_installed_root_none_when_not_installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(lpw, "known_program_data", lambda: str(tmp_path))
    monkeypatch.setattr(wi, "known_program_files", lambda: str(tmp_path / "Program Files"))
    app = tmp_path / "Program Files" / "Decky Loader"
    main = app / "app" / "main.py"
    for exe, script in ((tmp_path / "other" / "PluginLoader.exe", None),
                        (tmp_path / "decky" / "services" / "PluginLoader.exe", None),  # the earlier layouts no longer count
                        (app / "PluginLoader.exe", None),
                        (app / "PluginLoader.exe", main),
                        (app / "python" / "python.exe", None),                         # -c, or no script
                        (app / "python" / "python.exe", tmp_path / "evil" / "main.py"), # the installed runtime, other code
                        (app / "python" / "python.exe", app / "main.py"),
                        (app / "python" / "python3.exe", main),
                        (tmp_path / "Python313" / "python.exe", main),                  # another Python, installed code
                        (tmp_path / "build" / "installer" / "DeckyLoader" / "python" / "python.exe",
                         tmp_path / "build" / "installer" / "DeckyLoader" / "app" / "main.py")):  # the installer folder
        _running(monkeypatch, exe, script)
        assert lpw.installed_root() is None, (exe, script)


def test_installed_root_is_programdata_decky_for_the_program_files_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(lpw, "known_program_data", lambda: str(tmp_path / "pd"))
    monkeypatch.setattr(wi, "known_program_files", lambda: str(tmp_path / "pf"))
    # Case and separators do not matter
    _running(monkeypatch, tmp_path / "PF" / "decky loader" / "PYTHON" / "PythonW.EXE",
             str(tmp_path / "pf" / "Decky Loader" / "APP" / "Main.py").replace("\\", "/"))
    assert lpw.installed_root() == str(tmp_path / "pd" / "decky")
    assert lpw.installed_root(program_data=r"D:\data", program_files=str(tmp_path / "pf")) == r"D:\data\decky"


def test_is_installed_copy_needs_both_the_runtime_and_the_script():
    pf = r"C:\Program Files"
    run, main = pf + r"\Decky Loader\python\pythonw.exe", pf + r"\Decky Loader\app\main.py"
    assert lpw.is_installed_copy(run, main, pf) is True
    assert lpw.is_installed_copy(pf + r"\Decky Loader\python\python.exe", main, pf) is True
    assert lpw.is_installed_copy(run, None, pf) is False
    assert lpw.is_installed_copy(run, r"C:\Users\me\main.py", pf) is False
    assert lpw.is_installed_copy(r"C:\Python313\pythonw.exe", main, pf) is False


def test_installed_root_ignores_spoofed_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake = tmp_path / "fake"
    (fake / "Decky Loader").mkdir(parents=True)
    monkeypatch.setenv("PROGRAMDATA", str(fake))
    monkeypatch.setenv("ProgramFiles", str(fake))
    monkeypatch.setenv("ProgramW6432", str(fake))
    _running(monkeypatch, fake / "Decky Loader" / "python" / "pythonw.exe", fake / "Decky Loader" / "app" / "main.py")
    assert lpw.installed_root() is None


def test_running_main_script_is_the_absolute_main_file(monkeypatch: pytest.MonkeyPatch):
    import types
    main = types.ModuleType("__main__")
    main.__file__ = os.path.join("rel", "main.py")
    monkeypatch.setitem(sys.modules, "__main__", main)
    assert wi.running_main_script() == os.path.abspath(os.path.join("rel", "main.py"))
    del main.__file__
    assert wi.running_main_script() is None


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
    src = _fake_env(tmp_path)
    events: list[tuple[str, str]] = []
    rc = _install(tmp_path, src, run=Recorder(events), kill_running=lambda exe: events.append(("kill", exe)) or list[int]())
    assert rc == 0
    app = tmp_path / "Program Files" / "Decky Loader"
    legacy = str(tmp_path / "ProgramData" / "decky" / "services" / "PluginLoader.exe")
    assert events[:5] == [("run", "/End"), ("kill", str(app / "python" / "python.exe")),
                          ("kill", str(app / "python" / "pythonw.exe")), ("kill", str(app / "PluginLoader.exe")),
                          ("kill", legacy)]


def test_uninstall_stops_running_copies_after_ending_the_task(tmp_path: Path):
    events: list[tuple[str, str]] = []
    pd = tmp_path / "ProgramData"
    (pd / "decky" / "services").mkdir(parents=True)
    rc = _uninstall(tmp_path, run=Recorder(events), kill_running=lambda exe: events.append(("kill", exe)) or list[int]())
    assert rc == 0
    app = tmp_path / "Program Files" / "Decky Loader"
    assert events == [("run", "/End"), ("kill", str(app / "python" / "python.exe")), ("kill", str(app / "python" / "pythonw.exe")),
                      ("kill", str(app / "PluginLoader.exe")), ("kill", str(pd / "decky" / "services" / "PluginLoader.exe")),
                      ("run", "/Delete")]


def test_only_our_runtime_is_stopped_never_other_pythons():
    app, root = APP_DIR, r"C:\ProgramData\decky"
    procs: list[tuple[int, str | None]] = [
        (1, app + r"\python\python.exe"), (2, app + r"\python\pythonw.exe"), (3, app + r"\PluginLoader.exe"),
        (4, root + r"\services\PluginLoader.exe"),
        (10, r"C:\Users\me\AppData\Local\Programs\Python\Python313\python.exe"),
        (11, r"C:\Users\me\AppData\Local\Programs\Python\Python313\pythonw.exe"),
        (12, r"X:\src\backend\.venv\Scripts\python.exe"), (13, r"C:\Windows\py.exe"),
        (14, app + r"\python\python3.exe"), (15, app + r"\app\python.exe"), (16, r"C:\build\DeckyLoader\python\python.exe"),
        (17, None)]
    killed: list[int] = []
    for image in wi.program_images(app, root):
        wi.terminate_image(image, processes=lambda: procs, terminate=killed.append, snapshot=lambda: [])
    assert killed == [1, 2, 3, 4]


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
    src = _fake_env(tmp_path)
    _, root = _layout(tmp_path)
    dirs = Dirs()
    dirs.trusted.update([str(root), str(root / "plugins")])

    def rename(src: str, dst: str) -> None:
        if os.path.basename(src).startswith("Decky Loader.installing-"):
            raise OSError("disk on fire")
        os.rename(src, dst)

    assert _install(tmp_path, src, dirs=dirs, rename=rename) == 1
    # The earlier layout still works: its exe and launcher are untouched
    assert (root / "services" / "PluginLoader.exe").is_file() and (root / "tmp").is_dir()


def test_install_removes_the_legacy_layout_only_after_the_swap(tmp_path: Path):
    src = _fake_env(tmp_path)
    _, root = _layout(tmp_path)
    dirs = Dirs()
    dirs.trusted.update([str(root), str(root / "plugins")])
    seen: list[bool] = []

    def rename(src: str, dst: str) -> None:
        seen.append((root / "services").exists())
        os.rename(src, dst)

    assert _install(tmp_path, src, dirs=dirs, rename=rename) == 0
    assert seen and all(seen)
    assert sorted(os.listdir(root)) == ["plugins", "settings"]


def test_stage_and_swap_does_not_discard_a_staging_dir_it_could_not_create(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src = _fake_env(tmp_path)
    app = _app_dir(tmp_path)
    discarded: list[str] = []

    def mkdir(p: str) -> None:
        raise FileExistsError(p)

    ok = wi.stage_and_swap(str(src), str(app), mkdir=mkdir, discard=lambda p: discarded.append(p) or list[str](),
                           listdir=lambda p: list[str](), token=lambda: "t", sleep=lambda s: None)
    assert ok is False and discarded == []
    assert "Decky Loader.installing-t" in capsys.readouterr().err


def test_stage_and_swap_still_discards_its_own_half_staged_copy(tmp_path: Path):
    src = _fake_env(tmp_path)
    app = _app_dir(tmp_path)

    def broken(src: str, dst: str) -> None:
        raise OSError("disk full")

    assert wi.stage_and_swap(str(src), str(app), copytree=broken, token=lambda: "t", sleep=lambda s: None) is False
    assert os.listdir(tmp_path / "Program Files") == []


def test_stage_and_swap_cleans_copies_left_by_earlier_installs(tmp_path: Path):
    src = _fake_env(tmp_path)
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

    assert wi.stage_and_swap(str(src), str(app), token=lambda: "t", sleep=lambda s: None) is True
    assert sorted(os.listdir(pf)) == ["Decky Loader", "Decky Loader Extra", "Decky Loader.untrusted-1", "Other.old-x"]
    assert (elsewhere / "victim.txt").read_text() == "v"  # the link was removed, not followed
    assert _tree(app) == STAGE_TREE


def test_stale_copy_cleanup_is_best_effort(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    src = _fake_env(tmp_path)
    pf = tmp_path / "Program Files"
    (pf / "Decky Loader.old-aaa").mkdir()
    app = _app_dir(tmp_path)

    def discard(p: str) -> list[str]:
        if p.endswith("old-aaa"):
            return [p]
        return wi.remove_tree(p)

    assert wi.stage_and_swap(str(src), str(app), discard=discard, token=lambda: "t", sleep=lambda s: None) is True
    assert "Decky Loader.old-aaa" in caplog.text
    assert _tree(app) == STAGE_TREE


def test_copy_tree_plain_copies_contents_and_times_without_copy2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src = tmp_path / "src"
    _write_tree(src, STAGE_TREE)
    os.utime(src / "app" / "main.py", (1_600_000_000, 1_600_000_000))

    def no_copy2(*a: Any, **k: Any) -> Any:
        raise AssertionError("copy2 (CopyFile2) must not be used")

    monkeypatch.setattr(shutil, "copy2", no_copy2)
    wi.copy_tree_plain(str(src), str(tmp_path / "dst"))
    assert _tree(tmp_path / "dst") == STAGE_TREE
    assert os.stat(tmp_path / "dst" / "app" / "main.py").st_mtime == 1_600_000_000


def _acl(path: Path) -> list[str]:
    out = subprocess.run([os.path.join(wi.system_directory(), "icacls.exe"), str(path)], capture_output=True, text=True,
                         check=True).stdout
    lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("Successfully")]
    lines[0] = lines[0][len(str(path)):].strip()  # the first ACE shares its line with the path
    return sorted(lines)


def test_copied_files_get_the_destination_acl_not_the_source_acl(tmp_path: Path):
    # Source files carry an explicit, protected ACL (only the current user and SYSTEM). The copy must not keep it:
    # in Program Files the runtime has to inherit Users read and execute, or restricted plugin hosts cannot load it.
    sid = wi.current_user_sid()
    src = tmp_path / "src"
    wi.create_secure_dir(str(src), f"D:PAI(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)")
    _write_tree(src, {r"python\python.exe": b"py"})
    dst_parent = tmp_path / "dst"
    dst_parent.mkdir()
    (dst_parent / "fresh.txt").write_bytes(b"")
    (dst_parent / "freshdir").mkdir()
    wi.copy_tree_plain(str(src), str(dst_parent / "copy"))
    assert _acl(src / "python" / "python.exe") != _acl(dst_parent / "fresh.txt")  # the test means something
    assert _acl(dst_parent / "copy" / "python" / "python.exe") == _acl(dst_parent / "fresh.txt")
    assert _acl(dst_parent / "copy" / "python") == _acl(dst_parent / "freshdir")


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


# Steam's CEF debugger marker (<Steam>\.cef-enable-remote-debugging)

def test_install_enables_cef_debugging_for_the_target_user(tmp_path: Path):
    src = _fake_env(tmp_path)
    sids: list[str] = []
    assert _install(tmp_path, src, cef_debug=sids.append) == 0
    assert sids == [SID]


def test_failed_install_does_not_touch_steam(tmp_path: Path):
    src = _fake_env(tmp_path)
    sids: list[str] = []
    def broken(src: str, dst: str) -> None:
        raise OSError("x")
    assert _install(tmp_path, src, cef_debug=sids.append, copytree=broken) == 1
    assert sids == []


def test_uninstall_leaves_the_marker(tmp_path: Path):
    steam = tmp_path / "Steam"
    steam.mkdir()
    (steam / wi.CEF_DEBUG_MARKER).write_bytes(b"")
    assert _uninstall(tmp_path) == 0
    assert (steam / wi.CEF_DEBUG_MARKER).exists()


def _steam(tmp_path: Path) -> Path:
    steam = tmp_path / "Steam"
    steam.mkdir()
    (steam / "steam.exe").write_bytes(b"exe")
    return steam


def _enable(steam_dir: Path | None, **over: Any) -> Any:
    def find(sid: str) -> str | None:
        return None if steam_dir is None else str(steam_dir)
    kw: dict[str, Any] = dict(steam_dir=find)
    kw.update(over)
    return wi.enable_cef_remote_debugging(SID, **kw)


def test_cef_marker_created_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    steam = _steam(tmp_path)
    assert _enable(steam) == str(steam / wi.CEF_DEBUG_MARKER)
    assert (steam / wi.CEF_DEBUG_MARKER).read_bytes() == b""
    assert "next time Steam starts" in capsys.readouterr().out


def test_cef_marker_existing_file_is_left_alone(tmp_path: Path):
    steam = _steam(tmp_path)
    (steam / wi.CEF_DEBUG_MARKER).write_bytes(b"keep")
    assert _enable(steam) is None
    assert (steam / wi.CEF_DEBUG_MARKER).read_bytes() == b"keep"


def test_cef_marker_link_is_not_followed(tmp_path: Path):
    steam = _steam(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _junction(steam / wi.CEF_DEBUG_MARKER, elsewhere)
    assert _enable(steam) is None
    assert os.listdir(elsewhere) == []


def test_cef_marker_refused_when_steam_is_a_junction(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    real = _steam(tmp_path)
    link = tmp_path / "SteamLink"
    _junction(link, real)
    assert _enable(link) is None
    assert not (real / wi.CEF_DEBUG_MARKER).exists()
    assert "junction" in capsys.readouterr().out


def test_cef_marker_refused_when_a_parent_is_a_junction(tmp_path: Path):
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    _steam(real_parent)
    _junction(tmp_path / "games", real_parent)
    assert _enable(tmp_path / "games" / "Steam") is None
    assert not (real_parent / "Steam" / wi.CEF_DEBUG_MARKER).exists()


def _handle_events(events: list[str], attrs: int) -> dict[str, Any]:
    def opener(p: str) -> str:
        events.append("open")
        return p
    def query(h: str) -> tuple[int, str, int]:
        return attrs, "", 0
    def closer(h: str) -> None:
        events.append("close")
    def folder_path(h: str) -> str:
        return h  # the fake handle is the folder path
    def file_path(p: str) -> str:
        events.append("verify")
        return p
    return dict(opener=opener, query=query, closer=closer, folder_path=folder_path, file_path=file_path)


def test_cef_marker_checks_the_held_handle(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    # Swapped for a link after the path check: the attributes read from the open handle refuse it
    steam = _steam(tmp_path)
    events: list[str] = []
    rc = _enable(steam, **_handle_events(events, DIR_ATTR | REPARSE_ATTR))
    assert rc is None and events == ["open", "close"]
    assert not (steam / wi.CEF_DEBUG_MARKER).exists()
    assert "not a plain folder" in capsys.readouterr().out


def test_cef_marker_created_while_the_folder_is_held(tmp_path: Path):
    steam = _steam(tmp_path)
    events: list[str] = []
    def create(p: str) -> None:
        events.append("create")
    _enable(steam, create=create, **_handle_events(events, DIR_ATTR))
    assert events == ["open", "create", "verify", "close"]


def test_cef_marker_resolving_outside_steam_is_removed(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    # The file landed somewhere else (folder swapped despite the checks): delete exactly what was created there
    steam = _steam(tmp_path)
    elsewhere = str(tmp_path / "elsewhere" / wi.CEF_DEBUG_MARKER)
    events: list[str] = []
    removed: list[str] = []
    def create(p: str) -> None:
        events.append("create")
    def file_path(p: str) -> str:
        return elsewhere
    kw = _handle_events(events, DIR_ATTR)
    kw["file_path"] = file_path
    assert _enable(steam, create=create, remove=removed.append, **kw) is None
    assert removed == [elsewhere]
    assert events == ["open", "create", "close"]
    assert "outside" in capsys.readouterr().out


def test_real_final_paths_agree_for_a_plain_folder(tmp_path: Path):
    steam = _steam(tmp_path)
    marker = steam / wi.CEF_DEBUG_MARKER
    marker.write_bytes(b"")
    h = wi.open_dir(str(steam))
    try:
        folder = wi.final_path_of_handle(h)
    finally:
        wi.close_handle(h)
    created = wi.final_path_of_file(str(marker))
    assert os.path.normcase(os.path.dirname(created)) == os.path.normcase(folder)
    assert os.path.normcase(created).endswith(os.path.normcase(str(marker))[2:])  # \?\C:\... form


def test_cef_marker_without_steam_prints_a_note(capsys: pytest.CaptureFixture[str]):
    assert _enable(None) is None
    assert "Steam was not found" in capsys.readouterr().out


def test_cef_marker_errors_never_raise(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    steam = _steam(tmp_path)
    def denied(p: str) -> None:
        raise PermissionError("denied")
    assert _enable(steam, create=denied) is None
    assert "Could not create" in capsys.readouterr().out


def test_steam_dir_prefers_the_target_users_hive(tmp_path: Path):
    import winreg
    user_steam, machine_steam = tmp_path / "user", tmp_path / "machine"
    for d in (user_steam, machine_steam):
        d.mkdir()
        (d / "steam.exe").write_bytes(b"exe")
    reads: list[tuple[int, str, str]] = []
    def read(hive: int, key: str, value: str) -> str | None:
        reads.append((hive, key, value))
        if hive == winreg.HKEY_USERS:
            return str(user_steam).replace("\\", "/")  # Steam stores SteamPath with forward slashes
        return str(machine_steam)
    assert wi.steam_dir_for_sid(SID, read=read) == str(user_steam)
    assert reads[0] == (winreg.HKEY_USERS, SID + r"\Software\Valve\Steam", "SteamPath")


def test_steam_dir_falls_back_to_the_machine_install(tmp_path: Path):
    import winreg
    machine_steam = tmp_path / "machine"
    machine_steam.mkdir()
    (machine_steam / "steam.exe").write_bytes(b"exe")
    def read(hive: int, key: str, value: str) -> str | None:
        if hive == winreg.HKEY_USERS:
            return str(tmp_path / "no-steam-here")
        return str(machine_steam) if "WOW6432Node" in key else None
    assert wi.steam_dir_for_sid(SID, read=read) == str(machine_steam)
    def nothing(hive: int, key: str, value: str) -> str | None:
        return None
    def relative(hive: int, key: str, value: str) -> str | None:
        return r"relative\steam"
    assert wi.steam_dir_for_sid(SID, read=nothing) is None
    assert wi.steam_dir_for_sid(SID, read=relative) is None


# -I on every launch of our runtime

def test_self_invocation_isolates_only_our_runtime(tmp_path: Path):
    stage = _fake_env(tmp_path)
    exe, script = str(stage / "python" / "pythonw.exe"), str(stage / "app" / "main.py")
    assert wi.self_invocation(exe, script) == [exe, "-I", script]
    assert wi.self_invocation(r"C:\venv\python.exe", r"C:\src\main.py") == [r"C:\venv\python.exe", r"C:\src\main.py"]
    assert wi.self_invocation("x.exe", "m.py", our_runtime=True) == ["x.exe", "-I", "m.py"]


@pytest.mark.parametrize("argv, isolated", [
    (["py.exe", "-I", "main.py"], True),
    (["py.exe", "-B", "-I", "main.py", "--install"], True),
    (["py.exe", "-IB", "main.py"], True),
    (["py.exe", "-X", "utf8", "-I", "main.py"], True),
    (["py.exe", "main.py", "-I"], False),             # a script argument, not an interpreter option
    (["py.exe", "main.py"], False),
    (["py.exe", "-c", "-I"], False),
    (["py.exe", "-m", "x", "-I"], False),
    (["py.exe", "-X", "I", "main.py"], False),         # the value of -X, not -I
    (["py.exe", "-W", "Ignore", "main.py"], False),
    (["py.exe", "--check-hash-based-pycs", "always", "main.py"], False),
    (["py.exe"], False),
])
def test_launched_isolated_reads_only_interpreter_options(argv: list[str], isolated: bool):
    assert wi.launched_isolated(argv) is isolated


def test_isolation_error_only_for_our_runtime_without_dash_i(tmp_path: Path):
    stage = _fake_env(tmp_path)
    exe, script = str(stage / "python" / "python.exe"), str(stage / "app" / "main.py")
    assert wi.isolation_error(exe, script, [exe, "-I", script]) is None
    message = wi.isolation_error(exe, script, [exe, script, "--install"])
    assert message is not None and "must be started with -I" in message
    # A development checkout is never refused
    assert wi.isolation_error(r"C:\venv\python.exe", r"C:\src\main.py", [r"C:\venv\python.exe", r"C:\src\main.py"]) is None


def test_main_py_checks_isolation_before_dispatching():
    import ast
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    source = main_py.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = sorted((n.lineno, ast.unparse(n.func)) for n in ast.walk(tree) if isinstance(n, ast.Call))
    names = [c for _, c in calls]
    first_dispatch = min(i for i, c in enumerate(names) if c in ("main", "finish_update", "winsupervisor.setup_logging"))
    assert names.index("wininstall.isolation_error") < first_dispatch
    assert "run_logged" in source  # install and uninstall failures are logged


# Copying refuses links

def test_copy_tree_plain_refuses_a_junction_inside_the_stage(tmp_path: Path):
    src = tmp_path / "src"
    _write_tree(src, STAGE_TREE)
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "token.txt").write_bytes(b"x")
    _junction(src / "app" / "decky_loader" / "link", secret)
    with pytest.raises(wi.ReparsePointError, match="junction or symbolic link"):
        wi.copy_tree_plain(str(src), str(tmp_path / "dst"))
    assert not (tmp_path / "dst" / "app" / "decky_loader" / "link").exists()


def test_copy_tree_plain_refuses_a_junction_as_the_source(tmp_path: Path):
    real = tmp_path / "real"
    _write_tree(real, {"a.txt": b"a"})
    _junction(tmp_path / "link", real)
    with pytest.raises(wi.ReparsePointError):
        wi.copy_tree_plain(str(tmp_path / "link"), str(tmp_path / "dst"))
    assert not (tmp_path / "dst").exists()


def test_install_fails_cleanly_on_a_link_in_the_stage(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    src = _fake_env(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _junction(src / "python" / "Lib" / "evil", elsewhere)
    rec = Recorder()
    assert _install(tmp_path, src, run=rec) == 1
    assert "junction or symbolic link" in capsys.readouterr().err
    assert not any(r[1:2] == ["/Create"] for r in rec.runs)
    assert os.listdir(tmp_path / "Program Files") == []  # the half-staged copy is removed


# Install failures are logged (the elevated console closes)

def test_run_logged_writes_output_and_traceback_on_failure(capsys: pytest.CaptureFixture[str]):
    logs: list[str] = []

    def work() -> int:
        print("Could not replace the program folder", file=sys.stderr)
        raise RuntimeError("boom")

    rc = wi.run_logged("install", ["py.exe", "-I", "main.py", "--install"], work, elevated=lambda: True,
                       write_log=lambda text: logs.append(text) or r"C:\pd\decky\logs\install.log",
                       now=lambda: datetime.datetime(2026, 1, 2, 3, 4, 5))
    assert rc == 1 and len(logs) == 1
    assert "2026-01-02T03:04:05 install failed (exit code 1)" in logs[0]
    assert "command: py.exe -I main.py --install" in logs[0]
    assert "Could not replace the program folder" in logs[0] and "RuntimeError: boom" in logs[0]
    err = capsys.readouterr().err
    assert "Could not replace the program folder" in err and r"Details: C:\pd\decky\logs\install.log" in err


def test_run_logged_keeps_the_exit_code_and_logs_nothing_on_success():
    logs: list[str] = []
    assert wi.run_logged("uninstall", ["x"], lambda: 0, elevated=lambda: True, write_log=logs.append) == 0
    assert wi.run_logged("uninstall", ["x"], lambda: 3, elevated=lambda: True,
                         write_log=lambda t: logs.append(t) or None) == 3
    assert len(logs) == 1 and "exit code 3" in logs[0]
    assert sys.stdout is not None and not isinstance(sys.stdout, wi._Tee)  # pyright: ignore [reportPrivateUsage]


def test_run_logged_unelevated_runs_plainly():
    logs: list[str] = []
    assert wi.run_logged("install", ["x"], lambda: 1, elevated=lambda: False, write_log=logs.append) == 1
    assert logs == []


def test_write_install_log_appends_in_the_log_dir(tmp_path: Path):
    logs = tmp_path / "logs"
    logs.mkdir()
    assert wi.write_install_log("one\n", log_dir=lambda: str(logs)) == str(logs / "install.log")
    assert wi.write_install_log("two\n", log_dir=lambda: str(logs)) == str(logs / "install.log")
    assert (logs / "install.log").read_text(encoding="utf-8") == "one\ntwo\n"


def test_write_install_log_falls_back_to_a_new_temp_file(tmp_path: Path):
    def broken() -> str | None:
        raise OSError("denied")
    for log_dir in (lambda: None, broken):
        path = wi.write_install_log("why\n", log_dir=log_dir, temp_dir=lambda: str(tmp_path))
        assert path is not None and os.path.dirname(path) == str(tmp_path)
        assert os.path.basename(path).startswith("decky-install-") and path.endswith(".log")
        assert Path(path).read_text(encoding="utf-8") == "why\n"
    assert len(list(tmp_path.glob("decky-install-*.log"))) == 2
