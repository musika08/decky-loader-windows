import os, subprocess, sys, tarfile, threading, time, types
from pathlib import Path
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    import _winapi
    from decky_loader.localplatform import winplugincompat as compat


@pytest.fixture
def fcntl() -> Any:
    return compat.make_fcntl_module()


def _open(path: Path) -> int:
    return os.open(path, os.O_RDWR | os.O_CREAT)

# --- fcntl ---

def test_flock_nb_conflict_raises_blocking_io(tmp_path: Path, fcntl: Any):
    a, b = _open(tmp_path / "l"), _open(tmp_path / "l")
    try:
        fcntl.flock(a, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            fcntl.flock(b, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(a, fcntl.LOCK_UN)
        fcntl.flock(b, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(a)
        os.close(b)


def test_lock_closed_without_unlock_is_not_remembered(tmp_path: Path, fcntl: Any):
    # fd and handle numbers get reused after close; a new fd must really take the lock
    for _ in range(20):
        fd = _open(tmp_path / "l")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(fd)
    a = _open(tmp_path / "l")
    b = _open(tmp_path / "l")
    try:
        fcntl.flock(a, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            fcntl.flock(b, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(a)
        os.close(b)


def test_relock_keeps_the_lock_held(tmp_path: Path, fcntl: Any):
    a, b = _open(tmp_path / "l"), _open(tmp_path / "l")
    try:
        fcntl.flock(a, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(a, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            fcntl.flock(b, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(b, fcntl.LOCK_UN)  # b holds nothing: no-op, a keeps its lock
        with pytest.raises(BlockingIOError):
            fcntl.flock(b, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(a)
        os.close(b)


def test_flock_relock_and_unlock_unlocked_are_noops(tmp_path: Path, fcntl: Any):
    with open(tmp_path / "l", "w") as f:
        fcntl.flock(f, fcntl.LOCK_UN)
        fcntl.flock(f, fcntl.LOCK_EX)
        fcntl.flock(f, fcntl.LOCK_SH)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def test_flock_keeps_file_readable_and_position(tmp_path: Path, fcntl: Any):
    p = tmp_path / "pid"
    with open(p, "w") as f:
        f.write("12")
        f.flush()
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write("34")
        f.flush()
        assert p.read_text() == "1234"  # another handle can still read the content


def test_flock_blocks_until_released(tmp_path: Path, fcntl: Any):
    a, b = _open(tmp_path / "l"), _open(tmp_path / "l")
    try:
        fcntl.flock(a, fcntl.LOCK_EX)
        threading.Timer(0.3, lambda: fcntl.flock(a, fcntl.LOCK_UN)).start()
        start = time.monotonic()
        fcntl.flock(b, fcntl.LOCK_EX)
        assert time.monotonic() - start >= 0.2
    finally:
        os.close(a)
        os.close(b)


def test_lockf_and_bad_args(tmp_path: Path, fcntl: Any):
    a, b = _open(tmp_path / "l"), _open(tmp_path / "l")
    try:
        fcntl.lockf(a, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(OSError):
            fcntl.lockf(b, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with pytest.raises(OSError):
            fcntl.flock(b, fcntl.LOCK_NB)
        with pytest.raises(TypeError):
            fcntl.flock("nope", fcntl.LOCK_EX)
    finally:
        os.close(a)
        os.close(b)


def test_fcntl_flags_are_noops(tmp_path: Path, fcntl: Any):
    with open(tmp_path / "f", "w") as f:
        flags = fcntl.fcntl(f, fcntl.F_GETFL)
        assert fcntl.fcntl(f, fcntl.F_SETFL, flags | 0x800) == 0
        with pytest.raises(OSError):
            fcntl.fcntl(f, fcntl.F_SETLK, b"")
        with pytest.raises(OSError):
            fcntl.ioctl(f, 0x5414, b"")

# --- pwd / grp / os ids ---

def test_pwd_grp_describe_current_user():
    pwd, grp = compat.make_pwd_grp_modules("Me", r"C:\Users\Me", 1000)
    e = pwd.getpwuid(1000)
    assert (e.pw_name, e.pw_dir, e.pw_uid, e.pw_gid, e.pw_shell) == ("Me", r"C:\Users\Me", 1000, 1000, "cmd.exe")
    assert e[5] == e.pw_dir
    assert pwd.getpwnam("deck") == pwd.getpwnam("me") == pwd.getpwuid(0) == e
    assert pwd.getpwall() == [e]
    with pytest.raises(KeyError):
        pwd.getpwuid(4242)
    with pytest.raises(KeyError):
        pwd.getpwnam("root")
    g = grp.getgrgid(1000)
    assert (g.gr_name, g.gr_gid) == ("Me", 1000)
    assert grp.getgrnam("deck") == g
    with pytest.raises(KeyError):
        grp.getgrnam("wheel")


def test_root_name_only_when_elevated():
    pwd, _ = compat.make_pwd_grp_modules("Me", r"C:\Users\Me", 0)
    assert pwd.getpwnam("root").pw_uid == 0


def test_install_modules_only_when_missing():
    mods: dict[str, Any] = {}
    made = compat.install_modules({"pwd": lambda: types.ModuleType("pwd"),
                                   "json": lambda: types.ModuleType("json")}, mods)
    assert made == ["pwd"]
    assert "json" not in mods


def test_os_ids_added_only_when_missing():
    target = types.SimpleNamespace(getgid=lambda: 7)
    compat.install_os_ids(0, target)
    assert target.getuid() == target.geteuid() == target.getegid() == 0
    assert target.getgid() == 7
    target.chown("x", 0, 0)


def test_tarfile_extracts_as_root_with_shims(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # tarfile chowns when geteuid() == 0; that must not break elevated plugins
    src = tmp_path / "a.txt"
    src.write_text("hi")
    with tarfile.open(tmp_path / "a.tar", "w") as t:
        t.add(src, "a.txt")
    for name in ("geteuid", "chown", "lchown"):
        monkeypatch.delattr(os, name, raising=False)
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(os, "chown", compat._noop_chown, raising=False) # pyright: ignore [reportPrivateUsage]
    with tarfile.open(tmp_path / "a.tar") as t:
        t.extractall(tmp_path / "out", filter="data")
    assert (tmp_path / "out" / "a.txt").read_text() == "hi"

# --- Linux paths ---

def _drive(tmp_path: Path) -> tuple[str, Path]:
    # Stand-in for C:\ ; the code joins "<drive>" + os.sep, so pass the folder without a trailing sep
    root = tmp_path / "drive"
    root.mkdir()
    return str(root), root


def test_tmp_and_home_links(tmp_path: Path):
    drive, root = _drive(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "x.txt").write_text("x")
    compat.ensure_tmp(drive)
    compat.ensure_home_links(drive, str(profile), "Me")
    assert (root / "tmp").is_dir() and not os.path.isjunction(root / "tmp")
    for name in ("Me", "deck"):
        assert os.path.isjunction(root / "home" / name)
        assert (root / "home" / name / "x.txt").read_text() == "x"
    compat.ensure_tmp(drive)
    compat.ensure_home_links(drive, str(profile), "Me")


def test_user_named_deck_gets_one_link(tmp_path: Path):
    drive, root = _drive(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    compat.ensure_home_links(drive, str(profile), "Deck")
    assert [n.lower() for n in os.listdir(root / "home")] == ["deck"]


def test_existing_entries_are_never_replaced(tmp_path: Path):
    drive, root = _drive(tmp_path)
    profile, other = tmp_path / "profile", tmp_path / "other"
    profile.mkdir()
    other.mkdir()
    (root / "home" / "deck").mkdir(parents=True)
    (root / "home" / "deck" / "keep").write_text("k")
    _winapi.CreateJunction(str(other), str(root / "home" / "Me"))
    (root / "tmp").write_text("a file")
    compat.ensure_tmp(drive)
    compat.ensure_home_links(drive, str(profile), "Me")
    assert (root / "tmp").read_text() == "a file"
    assert (root / "home" / "deck" / "keep").read_text() == "k"
    assert os.path.realpath(root / "home" / "Me") == os.path.realpath(other)


def test_home_junction_is_not_followed(tmp_path: Path):
    drive, root = _drive(tmp_path)
    profile, elsewhere = tmp_path / "profile", tmp_path / "elsewhere"
    profile.mkdir()
    elsewhere.mkdir()
    _winapi.CreateJunction(str(elsewhere), str(root / "home"))
    compat.ensure_home_links(drive, str(profile), "Me")
    assert os.listdir(elsewhere) == []


def test_cwd_moves_to_system_drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    cwd_drive = os.path.splitdrive(os.getcwd())[0]
    home_drive = os.path.splitdrive(str(home))[0]
    monkeypatch.chdir(tmp_path)
    compat.ensure_cwd_on_drive(home_drive, str(home))
    assert os.getcwd() == str(tmp_path)  # already on that drive: untouched
    moved: list[str] = []
    monkeypatch.setattr(compat.os, "chdir", moved.append)
    monkeypatch.setattr(compat.os, "getcwd", lambda: r"Q:\somewhere")
    compat.ensure_cwd_on_drive(home_drive, str(home))
    compat.ensure_cwd_on_drive("Z:", str(home))
    assert moved == [str(home), "Z:" + os.sep]
    assert cwd_drive  # sanity: tests run from a drive path

# --- PATH helpers ---

def test_shim_bin_dir_ships_only_chmod():
    assert os.listdir(compat.SHIM_BIN_DIR) == ["chmod.cmd"]


def test_prepend_shim_bin_once():
    env = {"PATH": r"C:\Windows"}
    assert compat.prepend_shim_bin(env)
    assert compat.prepend_shim_bin(env)
    assert env["PATH"] == compat.SHIM_BIN_DIR + os.pathsep + r"C:\Windows"
    assert not compat.prepend_shim_bin({}, r"C:\does\not\exist")


def test_chmod_cmd_is_found_and_succeeds():
    env = dict(os.environ)
    compat.prepend_shim_bin(env)
    r = subprocess.run("chmod +x somefile", shell=True, env=env, capture_output=True, timeout=30)
    assert r.returncode == 0

# --- program lookup ---

SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")


@pytest.fixture
def lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    '''Lets a test install the program lookup on subprocess.Popen; monkeypatch restores the original.'''
    monkeypatch.setattr(subprocess.Popen, "_execute_child", _execute_child())


def _execute_child() -> Any:
    return getattr(subprocess.Popen, "_execute_child")


def _shim_dir(tmp_path: Path, name: str = "winbin") -> Path:
    d = tmp_path / name
    d.mkdir(parents=True)
    (d / "deckyshimtest.cmd").write_text("@echo off\r\necho shim:%*\r\nexit /b 7\r\n")
    return d


def _env(*dirs: Path | str) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([*map(str, dirs), SYSTEM32])
    return env


def test_list_call_misses_cmd_without_lookup(tmp_path: Path):
    d = _shim_dir(tmp_path)
    with pytest.raises(FileNotFoundError):
        subprocess.run(["deckyshimtest"], env=_env(d), capture_output=True, timeout=30)


def test_list_call_finds_shim_cmd(tmp_path: Path, lookup: None):
    d = _shim_dir(tmp_path, "Decky Loader/winbin")  # the installed path has a space
    assert compat.install_program_lookup(bin_dir=str(d))
    r = subprocess.run(["deckyshimtest", "+x", "a b.txt", "plain"], env=_env(d), capture_output=True, text=True, timeout=30)
    assert r.returncode == 7
    assert r.stdout.strip() == 'shim:+x "a b.txt" plain'


def test_asyncio_exec_finds_shim_cmd(tmp_path: Path, lookup: None):
    import asyncio
    d = _shim_dir(tmp_path)
    compat.install_program_lookup(bin_dir=str(d))

    async def run() -> tuple[int | None, bytes]:
        p = await asyncio.create_subprocess_exec("deckyshimtest", "x", env=_env(d), stdout=subprocess.PIPE)
        out, _ = await p.communicate()
        return p.returncode, out
    code, out = asyncio.run(run())
    assert code == 7 and out.strip() == b"shim:x"


def test_install_is_idempotent(lookup: None):
    assert compat.install_program_lookup()
    wrapped = _execute_child()
    assert not compat.install_program_lookup()
    assert _execute_child() is wrapped


def test_real_chmod_list_call_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lookup: None):
    # A PATH without Git's chmod.exe, as in the plugin host; CreateProcess searches the process PATH
    monkeypatch.setenv("PATH", SYSTEM32)
    compat.prepend_shim_bin(os.environ)
    compat.install_program_lookup()
    f = tmp_path / "f"
    f.write_text("")
    assert subprocess.run(["chmod", "+x", str(f)], capture_output=True, timeout=30).returncode == 0


def test_cmd_outside_shim_dir_is_not_run(tmp_path: Path, lookup: None):
    shim, other = tmp_path / "winbin", _shim_dir(tmp_path, "other")
    shim.mkdir()
    compat.install_program_lookup(bin_dir=str(shim))
    with pytest.raises(FileNotFoundError):
        subprocess.run(["deckyshimtest"], env=_env(shim, other), capture_output=True, timeout=30)


@pytest.mark.parametrize("arg", ['a"b', "a&calc", "50%", "x|y", "a^b", "(a)", "a\nb", "!x!"])
def test_shim_cmd_refuses_cmd_special_args(tmp_path: Path, lookup: None, arg: str):
    d = _shim_dir(tmp_path)
    compat.install_program_lookup(bin_dir=str(d))
    with pytest.raises(FileNotFoundError):
        subprocess.run(["deckyshimtest", arg], env=_env(d), capture_output=True, timeout=30)


def test_com_found_anywhere_on_path(tmp_path: Path, lookup: None):
    import shutil
    d = tmp_path / "tools"
    d.mkdir()
    shutil.copy(os.path.join(SYSTEM32, "hostname.exe"), d / "deckyhosttest.com")
    compat.install_program_lookup(bin_dir=str(tmp_path / "winbin"))
    r = subprocess.run(["deckyhosttest"], env=_env(d), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip()


def test_shell_and_executable_calls_untouched(tmp_path: Path):
    seen: list[Any] = []

    class FakePopen:
        def _execute_child(self, args: Any, executable: Any, *rest: Any) -> None:
            seen.append(args)
    d = _shim_dir(tmp_path)
    compat.install_program_lookup(FakePopen, bin_dir=str(d))
    env = _env(d)
    child: Any = getattr(FakePopen(), "_execute_child")
    child(["deckyshimtest"], None, None, True, (), None, env, None, 0, True)
    child(["deckyshimtest"], "x.exe", None, True, (), None, env, None, 0, False)
    child("deckyshimtest", None, None, True, (), None, env, None, 0, False)
    child(("deckyshimtest", "a"), None, None, True, (), None, env, None, 0, False)
    assert seen[:3] == [["deckyshimtest"], ["deckyshimtest"], "deckyshimtest"]
    assert seen[3] == [str(d / "deckyshimtest.cmd"), "a"]


@pytest.mark.parametrize("argv0", ["deckyshimtest.cmd", r".\deckyshimtest", "sub/deckyshimtest", "C:deckyshimtest", "deckyshimtest.", ""])
def test_only_bare_names_are_resolved(tmp_path: Path, argv0: str):
    d = _shim_dir(tmp_path)
    assert compat.resolve_argv([argv0, "a"], _env(d), str(d)) == [argv0, "a"]


def test_names_createprocess_finds_are_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    d = _shim_dir(tmp_path)
    (d / "where.cmd").write_text("@exit /b 0\r\n")
    assert compat.resolve_argv(["where"], _env(d), str(d)) == ["where"]  # System32\where.exe
    def found(name: str) -> bool:
        return True
    monkeypatch.setattr(compat, "_found_as_exe", found)
    assert compat.resolve_argv(["deckyshimtest"], _env(d), str(d)) == ["deckyshimtest"]


def test_find_program_rules(tmp_path: Path):
    shim, a, b = tmp_path / "winbin", tmp_path / "a", tmp_path / "b"
    for d in (shim, a, b):
        d.mkdir()
    (a / "tool.cmd").write_text("")
    (a / "tool.bat").write_text("")
    (b / "tool.exe").write_text("")
    (shim / "tool.cmd").write_text("")
    env = {"Path": os.pathsep.join(["relative", str(a), str(b), str(shim)])}
    assert compat.find_program("tool", env, str(shim)) == str(b / "tool.exe")
    assert compat.find_program("tool", {"PATH": f'"{shim}"{os.pathsep}{b}'}, str(shim)) == str(shim / "tool.cmd")
    (b / "tool.com").write_text("")
    assert compat.find_program("tool", env, str(shim)) == str(b / "tool.com")  # PATHEXT order
    assert compat.find_program("tool", dict(env, PATHEXT=".EXE;.COM"), str(shim)) == str(b / "tool.exe")
    assert compat.find_program("tool", {"PATH": str(a)}, str(shim)) is None
    assert compat.find_program("tool", {}, str(shim)) is None

# --- entry point ---

def _run_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, elevated: bool) -> tuple[Path, dict[str, str], dict[str, Any]]:
    drive, root = _drive(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    mods: dict[str, Any] = {}
    real = compat.install_modules
    def install_modules(m: dict[str, Any]) -> list[str]:
        return real(m, mods)
    def install_os_ids(uid: int) -> None:
        mods["uid"] = uid
    def no_chdir(*_a: object) -> None:
        pass
    def install_program_lookup() -> None:
        mods["lookup"] = True
    monkeypatch.setattr(compat, "install_modules", install_modules)
    monkeypatch.setattr(compat, "install_program_lookup", install_program_lookup)
    monkeypatch.setattr(compat, "install_os_ids", install_os_ids)
    monkeypatch.setattr(compat, "ensure_cwd_on_drive", no_chdir)
    env = {"PATH": r"C:\Windows"}
    compat.install_plugin_compat(elevated=elevated, env=env, system_drive=drive, home=str(profile), user="Me")
    return root, env, mods


def test_install_unelevated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root, env, mods = _run_install(tmp_path, monkeypatch, False)
    assert {"fcntl", "pwd", "grp"} <= set(mods)
    assert mods["uid"] == 1000 and mods["pwd"].getpwnam("deck").pw_uid == 1000
    assert mods["lookup"]
    assert env["PATH"].startswith(compat.SHIM_BIN_DIR)
    assert (root / "tmp").is_dir() and os.path.isjunction(root / "home" / "deck")


def test_install_elevated_changes_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root, env, mods = _run_install(tmp_path, monkeypatch, True)
    assert mods["uid"] == 0 and mods["pwd"].getpwuid(0).pw_name == "Me"
    assert mods["lookup"]
    assert env["PATH"].startswith(compat.SHIM_BIN_DIR)
    assert os.listdir(root) == []


def test_install_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def boom(*_a: object) -> None:
        raise RuntimeError("boom")
    for name in ("install_modules", "install_os_ids", "prepend_shim_bin", "install_program_lookup", "ensure_cwd_on_drive", "ensure_tmp", "ensure_home_links"):
        monkeypatch.setattr(compat, name, boom)
    monkeypatch.setattr(compat, "_is_elevated", boom)
    compat.install_plugin_compat()
    compat.install_plugin_compat(elevated=False, env={}, system_drive=str(tmp_path), home=str(tmp_path), user="Me")
