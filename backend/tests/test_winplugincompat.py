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


def test_find_program_skips_rooted_entries_without_drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Rooted entries resolve against the current drive (C:\usr\local\bin); never look there
    looked: list[str] = []

    def isfile(path: str) -> bool:
        looked.append(path)
        return True
    monkeypatch.setattr(compat.os.path, "isfile", isfile)
    env = {"PATH": os.pathsep.join(["/usr/local/bin", r"\home\deck\bin", "C:relative"])}
    assert compat.find_program("tool", env, str(tmp_path / "winbin")) is None
    assert looked == []
    unc = r"\\server\share\bin"
    assert compat.find_program("tool", {"PATH": unc}, str(tmp_path / "winbin")) is not None

# --- Linux tools, user switches, scripts ---

def _nothing_known(name: str) -> str | None:
    return None


def _config(**kw: Any) -> Any:
    defaults: dict[str, Any] = {"user": "Me", "self_command": lambda: ["DECKY", "main.py"],
                                "interpreter": lambda: ["PY", "-I"], "known": _nothing_known}
    return compat.CompatConfig(**{**defaults, **kw})


def _tool(name: str, *args: str) -> list[str]:
    return ["DECKY", "main.py", "--compat-tool", name, str(os.getpid()), *args]


@pytest.mark.parametrize("arg,name", [("ps", "ps"), ("/usr/bin/ps", "ps"), ("/bin/kill", "kill"), (b"pgrep", "pgrep"),
                                      ("/usr/sbin/sudo", "sudo"), ("/opt/bin/ps", None), ("C:/usr/bin/ps", None),
                                      ("PS", None), ("ps.exe", None), ("chmod", None), (None, None)])
def test_tool_name(arg: Any, name: str | None):
    assert compat.tool_name(arg) == name


@pytest.mark.parametrize("argv,elevated,inner", [
    (["sudo", "-u", "Me", "-E", r"C:\p\cp.py", "-p", "1"], False, [r"C:\p\cp.py", "-p", "1"]), # DeckyParty
    (["sudo", "-u", "deck", "steam", "steam://x"], False, ["steam", "steam://x"]),            # Quick User Switcher
    (["sudo", "-ume", "x"], False, ["x"]),
    (["sudo", "--user=ME", "x"], False, ["x"]),
    (["sudo", "-u", "#1000", "x"], False, ["x"]),
    (["sudo", "-nE", "-u", "Me", "--", "-x"], False, ["-x"]),
    (["sudo", "x"], False, None),                        # root without administrator rights
    (["sudo", "x"], True, ["x"]),
    (["sudo", "-u", "root", "x"], True, ["x"]),
    (["sudo", "-u", "#0", "x"], True, ["x"]),
    (["sudo", "-u", "Me", "x"], True, None),             # an elevated host never drops to the user here
    (["sudo", "-u", "bob", "x"], False, None),
    (["sudo", "-i"], False, None),
    (["sudo", "-u", "Me", "-s", "x"], False, None),
    (["sudo", "-u", "Me"], False, None),
    (["sudo", "-u", "Me", "A=1", "x"], False, None),
    (["runuser", "-u", "Me", "--", "x", "a"], False, ["x", "a"]),
    (["runuser", "-l", "Me", "-c", "x a 'b c'"], False, ["x", "a", "b c"]),
    (["runuser", "-u", "bob", "x"], False, None),
    (["su", "Me", "-c", "steam steam://open/games"], False, ["steam", "steam://open/games"]),
    (["su", "-", "deck", "-c", "x"], False, ["x"]),
    (["su", "-c", "x | grep y", "Me"], False, None),     # needs a shell
    (["su", "-", "Me"], False, None),                    # interactive
    (["su", "-c", "x"], False, None),
    (["su", "-c", "x"], True, ["x"]),
    (["su", "-s", "/bin/sh", "Me", "-c", "x"], False, None),
    (["ls", "-l"], False, ["ls", "-l"]),
])
def test_unwrap_user_switch(argv: list[str], elevated: bool, inner: list[str] | None):
    assert compat.unwrap_user_switch(argv, "Me", elevated) == inner


@pytest.mark.parametrize("command,words", [
    ("date '+%Y-%m-%d %H:%M:%S'", ["date", "+%Y-%m-%d %H:%M:%S"]),
    ('timedatectl show -p "NTP"', ["timedatectl", "show", "-p", "NTP"]),
    ("ps aux | grep x", None), ("echo $HOME", None), ("x 'open", None), ("", None), (r"C:\x", None), ("ls *", None),
])
def test_split_simple_command(command: str, words: list[str] | None):
    assert compat.split_simple_command(command) == words


@pytest.mark.parametrize("command,result", [
    ("openssl req -nodes -subj '/C=US/O=KDE/OU=KDE Connect/CN=abc'",
     'openssl req -nodes -subj "/C=US/O=KDE/OU=KDE Connect/CN=abc"'),                      # Decky Notifications
    ("'C:\\p\\bin\\hash' \"C:\\roms\\a b.zip\"", '"C:\\p\\bin\\hash" "C:\\roms\\a b.zip"'),  # Emuchievements
    ("x 'C:\\dir\\'", 'x "C:\\dir\\\\"'),
    ("echo 'it'\\''s'", 'echo "it"\'"s"'),
    ("echo pre'a b'post", 'echo pre"a b"post'),
    ("echo 'a%PATH%b'", "echo 'a%PATH%b'"),
    ("echo 'a\"b'", "echo 'a\"b'"),
    ("echo 'a & calc'", "echo 'a & calc'"),
    ("echo 'a\nb'", "echo 'a\nb'"),
    ('echo "it\'s"', 'echo "it\'s"'),
    ("echo it's", "echo it's"),
    ('echo "open', 'echo "open'),
    ("no quotes", "no quotes"),
])
def test_translate_posix_quotes(command: str, result: str):
    assert compat.translate_posix_quotes(command) == result


def test_rewrite_argv_tools():
    cfg = _config()
    assert compat.rewrite_argv(["ps", "--ppid", "1", "-o", "pid="], None, cfg) == _tool("ps", "--ppid", "1", "-o", "pid=")
    assert compat.rewrite_argv(("/usr/bin/timedatectl", "show"), None, cfg) == _tool("timedatectl", "show")
    assert compat.rewrite_argv([b"kill", b"-STOP", b"5"], None, cfg) == _tool("kill", "-STOP", "5")
    assert compat.rewrite_argv(["ping", "-c", "3", "-W", "2", "8.8.8.8"], None, cfg) == _tool("ping", "-c", "3", "-W", "2", "8.8.8.8")
    assert compat.rewrite_argv(["ping", "-n", "1", "host"], None, cfg) == ["ping", "-n", "1", "host"] # System32 ping.exe
    assert compat.rewrite_argv(["sudo", "-u", "Me", "kill", "-STOP", "5"], None, cfg) == _tool("kill", "-STOP", "5")
    assert compat.rewrite_argv(["sudo", "-u", "bob", "ls"], None, cfg) == _tool("sudo", "-u", "bob", "ls")
    assert compat.rewrite_argv([], None, cfg) == []
    assert compat.rewrite_argv("ps -e", None, cfg) == "ps -e"


def test_rewrite_argv_python_scripts(tmp_path: Path):
    cfg = _config()
    script, shebang, bash = tmp_path / "copyparty-sfx.py", tmp_path / "tool", tmp_path / "run"
    script.write_text("print(1)")
    shebang.write_bytes(b"#!/usr/bin/env python3 -u\nprint(1)\n")
    bash.write_bytes(b"#!/bin/bash\nexec \"$@\"\n")
    assert compat.rewrite_argv(["sudo", "-u", "Me", "-E", str(script), "-p", "3923"], None, cfg) == ["PY", "-I", str(script), "-p", "3923"]
    assert compat.rewrite_argv([str(shebang)], None, cfg) == ["PY", "-I", str(shebang)]
    assert compat.rewrite_argv([str(bash)], None, cfg) == [str(bash)]
    assert compat.rewrite_argv(["copyparty-sfx.py"], None, cfg) == ["copyparty-sfx.py"] # not a full path
    assert compat.rewrite_argv([str(tmp_path / "missing.py")], None, cfg) == [str(tmp_path / "missing.py")]
    frozen = _config(interpreter=lambda: None)
    assert compat.rewrite_argv([str(script)], None, frozen) == [str(script)]


def test_rewrite_shell():
    cfg = _config(self_command=lambda: [sys.executable, "main.py"])
    pid = str(os.getpid())
    assert compat.rewrite_shell("timedatectl show -p NTP", None, cfg) == \
        ([sys.executable, "main.py", "--compat-tool", "timedatectl", pid, "show", "-p", "NTP"], False)
    assert compat.rewrite_shell("date '+%Y %H'", None, cfg) == \
        ([sys.executable, "main.py", "--compat-tool", "date", pid, "+%Y %H"], False)
    assert compat.rewrite_shell("openssl req -subj '/O=KDE Connect'", None, cfg) == ('openssl req -subj "/O=KDE Connect"', True)
    # The program in place of sudo can't be found: cmd.exe reports that as before, with an exit code
    assert compat.rewrite_shell("sudo -u Me deckynothere 'a b'", None, cfg) == ('sudo -u Me deckynothere "a b"', True)
    assert compat.rewrite_shell("ping -n 1 host", None, cfg) == ("ping -n 1 host", True)
    assert compat.rewrite_shell("ps -e | findstr x", None, cfg) == ("ps -e | findstr x", True)
    assert compat.rewrite_shell(["ps"], None, cfg) == (["ps"], True)


def test_known_programs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    exe = tmp_path / "Tailscale" / "tailscale.exe"
    exe.parent.mkdir()
    exe.write_text("")
    assert compat.known_program("tailscale", lambda: str(tmp_path)) == str(exe)
    assert compat.known_program("tailscale", lambda: str(tmp_path / "none")) is None
    assert compat.known_program("other", lambda: str(tmp_path)) is None
    assert compat.known_program("tailscale", lambda: None) is None
    def not_found(name: str) -> bool:
        return False
    monkeypatch.setattr(compat, "_found_as_exe", not_found)
    def known(name: str) -> str | None:
        return compat.known_program(name, lambda: str(tmp_path))
    assert compat.resolve_argv(["tailscale", "status"], {"PATH": SYSTEM32}, known=known) == [str(exe), "status"]
    other = tmp_path / "onpath"
    other.mkdir()
    (other / "tailscale.exe").write_text("")
    assert compat.resolve_argv(["tailscale", "up"], {"PATH": str(other)}, known=known) == [str(other / "tailscale.exe"), "up"]


def test_program_files_dir_is_the_real_one(monkeypatch: pytest.MonkeyPatch):
    real = compat.program_files_dir()
    assert real and os.path.isdir(real)
    monkeypatch.setenv("ProgramFiles", r"C:\Users\Public\fake")
    assert compat.program_files_dir() == real

# --- the tools through real subprocess calls ---

@pytest.fixture
def tools(lookup: None) -> None:
    compat.install_program_lookup(config=compat.CompatConfig(user=os.environ.get("USERNAME", "")))


def test_ps_call_runs_deckys_ps(tools: None):
    r = subprocess.run(["ps", "--pid", str(os.getpid()), "-o", "ppid="], capture_output=True, text=True, timeout=60)
    assert (r.returncode, r.stdout.strip()) == (0, str(os.getppid())), r.stderr


def test_shell_date_and_asyncio_timedatectl(tools: None):
    import asyncio, datetime
    r = subprocess.run("date '+%Y-%m'", shell=True, capture_output=True, text=True, timeout=60)
    assert r.stdout == datetime.date.today().strftime("%Y-%m") + "\n", r.stderr

    async def run() -> bytes:
        p = await asyncio.create_subprocess_exec("timedatectl", "show", "-p", "Timezone", stdout=subprocess.PIPE)
        out, _ = await p.communicate()
        return out
    assert asyncio.run(run()).startswith(b"Timezone=")


def test_sudo_python_script_and_refusal(tools: None, tmp_path: Path):
    import json
    script = tmp_path / "args.py"
    script.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    r = subprocess.run(["sudo", "-u", os.environ["USERNAME"], "-E", str(script), "a b", 'c"d'], capture_output=True,
                       text=True, timeout=60)
    assert json.loads(r.stdout) == ["a b", 'c"d'], r.stderr
    r = subprocess.run(["sudo", "-u", "someone-else", "whoami"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "another user" in r.stderr


def test_kill_stop_cont_through_subprocess(tools: None):
    from decky_loader.localplatform import winproctools
    python = getattr(sys, "_base_executable", sys.executable)
    child = subprocess.Popen([python, "-c", "import time\nend = time.time() + 60\nwhile time.time() < end: time.sleep(0.05)"])
    try:
        time.sleep(0.5)
        def stopped() -> bool:
            return next(p.stopped for p in winproctools.snapshot() if p.pid == child.pid)
        assert subprocess.run(["kill", "-SIGSTOP", str(child.pid)], timeout=60).returncode == 0
        assert stopped()
        r = subprocess.run(["ps", "--ppid", str(os.getpid()), "-o", "pid=", "-o", "stat="], capture_output=True, text=True, timeout=60)
        assert f"{child.pid} T" in [" ".join(line.split()) for line in r.stdout.splitlines()]
        assert subprocess.run(["kill", "-SIGCONT", str(child.pid)], timeout=60).returncode == 0
        assert not stopped()
        assert subprocess.run(["pkill", "-9", "-P", str(os.getpid()), "-f", "time.sleep"], timeout=60).returncode == 0
        assert child.wait(timeout=10) == 1
    finally:
        child.kill()

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
    def install_program_lookup(config: Any = None) -> None:
        mods["lookup"] = config
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
    assert (mods["lookup"].elevated, mods["lookup"].user) == (False, "Me")
    assert env["PATH"].startswith(compat.SHIM_BIN_DIR)
    assert (root / "tmp").is_dir() and os.path.isjunction(root / "home" / "deck")


def test_install_elevated_changes_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root, env, mods = _run_install(tmp_path, monkeypatch, True)
    assert mods["uid"] == 0 and mods["pwd"].getpwuid(0).pw_name == "Me"
    assert (mods["lookup"].elevated, mods["lookup"].user) == (True, "Me")
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
