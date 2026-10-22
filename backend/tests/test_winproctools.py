import io, os, subprocess, sys, time
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    from decky_loader.localplatform import winproctools as pt


class FakeTable:
    def __init__(self, procs: list[Any], user: str = "PC\\Me"):
        self.procs = procs
        self.user = user
        self.actions: list[tuple[int, str]] = []
        self.results: dict[int, str | None] = {}

    def list(self) -> list[Any]:
        return list(self.procs)

    def act(self, proc: Any, action: str) -> str | None:
        self.actions.append((proc.pid, action))
        if action == "suspend":
            proc.stopped = True
        elif action == "resume":
            proc.stopped = False
        elif action == "terminate":
            self.procs.remove(proc)
        return self.results.get(proc.pid)


def proc(pid: int, ppid: int, comm: str, created: float, args: str = "", stopped: bool = False) -> Any:
    return pt.Proc(pid, ppid, comm, created, stopped, lambda: args.split())


def world(**kw: Any) -> tuple[Any, Any]:
    # 10 host -> 20 game launcher -> 21, 22 game processes; 30 steam; 40 the tool itself; 5 the loader
    procs = [proc(5, 1, "pythonw", 1, "pythonw main.py --loader"),
             proc(10, 5, "pythonw", 2, "pythonw main.py --plugin-host abc"),
             proc(20, 10, "launcher", 3, "C:\\Games\\launcher.exe AppId=42"),
             proc(21, 20, "game", 4, "C:\\Games\\game.exe -fullscreen"),
             proc(22, 20, "game-helper", 5, "C:\\Games\\helper.exe"),
             proc(30, 1, "steam", 1, "C:\\Steam\\steam.exe"),
             proc(40, 10, "pythonw", 9, "pythonw main.py --compat-tool pgrep 10 game")]
    table = FakeTable(procs, **kw)
    return table, pt.Context(table, own=[40], protected=[40, 5])


def run(tool: str, args: list[str], ctx: Any) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = pt.TOOLS[tool](args, ctx, out, err)
    return code, out.getvalue(), err.getvalue()

# --- ps ---

def test_ps_ppid_pid_and_stat_like_pause_games():
    table, ctx = world()
    assert run("ps", ["--ppid", "20", "-o", "pid="], ctx) == (0, "     21\n     22\n", "")
    assert run("ps", ["--pid", "21", "-o", "ppid="], ctx)[1] == "     20\n"
    table.procs[3].stopped = True
    assert run("ps", ["--ppid", "20", "-o", "stat="], ctx)[1] == "T\nS\n"
    assert run("ps", ["--ppid", "999", "-o", "pid="], ctx) == (1, "", "")


def test_ps_formats_and_headers():
    _, ctx = world()
    code, out, _ = run("ps", ["-p", "21,22", "-o", "pid,comm"], ctx)
    assert code == 0 and out.splitlines() == ["    PID COMMAND", "     21 game", "     22 game-helper"]
    assert run("ps", ["-p", "21", "-o", "pid=Id", "--no-headers"], ctx)[1] == "     21\n"
    assert run("ps", ["-p", "21", "-o", "args="], ctx)[1] == "C:\\Games\\game.exe -fullscreen\n"
    assert run("ps", ["-C", "steam", "-o", "pid=", "-o", "user="], ctx)[1] == "     30 PC\\Me\n"
    assert run("ps", ["-e", "-o", "pid="], ctx)[1].split() == ["5", "10", "20", "21", "22", "30", "40"]


def test_ps_rejects_what_it_does_not_know():
    _, ctx = world()
    assert run("ps", ["aux"], ctx)[0] == 1
    assert run("ps", ["-o", "bogus"], ctx)[0] == 1
    assert run("ps", ["--sort", "pid"], ctx)[0] == 1


def test_ps_ppid_skips_processes_older_than_the_parent():
    # Windows keeps a dead parent's pid as ppid; pid 20 is now a newer process
    table, ctx = world()
    table.procs.append(proc(23, 20, "orphan", 2.5))
    assert run("ps", ["--ppid", "20", "-o", "pid="], ctx)[1].split() == ["21", "22"]
    # ...and its ppid shows as 1, so walking up the parents (Pause Games' appid_from_pid) can't loop
    assert run("ps", ["--pid", "23", "-o", "ppid="], ctx)[1] == "      1\n"

# --- kill ---

@pytest.mark.parametrize("spec,action", [("-SIGSTOP", "suspend"), ("-STOP", "suspend"), ("-19", "suspend"),
                                         ("-SIGCONT", "resume"), ("-SIGTERM", "terminate"), ("-9", "terminate"),
                                         ("-0", "check")])
def test_kill_maps_signals(spec: str, action: str):
    table, ctx = world()
    assert run("kill", [spec, "21", "22"], ctx)[0] == 0
    assert table.actions == [(21, action), (22, action)]


def test_kill_forms_and_default():
    table, ctx = world()
    assert run("kill", ["-s", "STOP", "21"], ctx)[0] == 0
    assert run("kill", ["--signal=CONT", "21"], ctx)[0] == 0
    assert run("kill", ["-n", "9", "--", "22"], ctx)[0] == 0
    assert run("kill", ["21"], ctx)[0] == 0
    assert table.actions == [(21, "suspend"), (21, "resume"), (22, "terminate"), (21, "terminate")]


def test_kill_failures():
    table, ctx = world()
    assert run("kill", ["-STOP", "999"], ctx) == (1, "", "kill: (999) - No such process\n")
    assert run("kill", ["-STOP", "30"], ctx)[2] == "kill: (30) - Operation not permitted\n"     # Steam
    assert run("kill", ["-9", "5"], ctx)[2] == "kill: (5) - Operation not permitted\n"          # the loader
    assert run("kill", ["-USR1", "21"], ctx)[2] == "kill: (21) - Operation not supported\n"
    assert run("kill", ["-9", "-20"], ctx)[0] == 1                                                # process group
    assert run("kill", ["-BOGUS", "21"], ctx)[0] == 1
    assert run("kill", [], ctx)[0] == 1
    assert table.actions == []
    table.results[21] = "Operation not permitted"
    assert run("kill", ["-STOP", "21", "22"], ctx)[0] == 1
    assert run("kill", ["-0", "30"], ctx)[0] == 0 # checking Steam is fine


def test_kill_list():
    _, ctx = world()
    assert run("kill", ["-l", "19"], ctx)[1] == "STOP\n"
    assert run("kill", ["-l", "SIGCONT"], ctx)[1] == "18\n"
    assert "KILL" in run("kill", ["-l"], ctx)[1].split()

# --- pgrep / pkill ---

def test_pgrep_full_newest_like_pause_games():
    table, ctx = world()
    table.procs.append(proc(50, 10, "launcher", 8, "C:\\Games\\launcher.exe AppId=42"))
    assert run("pgrep", ["--full", "--newest", r"launcher\.exe.*\bAppId=42\b"], ctx) == (0, "50\n", "")
    assert run("pgrep", ["-f", "--oldest", r"AppId=42"], ctx)[1] == "20\n"
    assert run("pgrep", ["-f", r"AppId=7\b"], ctx) == (1, "", "")


def test_pgrep_matching_rules():
    _, ctx = world()
    assert run("pgrep", ["game"], ctx)[1].split() == ["21", "22"]
    assert run("pgrep", ["-x", "game"], ctx)[1].split() == ["21"]
    assert run("pgrep", ["-i", "GAME$"], ctx)[1].split() == ["21"]
    assert run("pgrep", ["-l", "-P", "20"], ctx)[1].splitlines() == ["21 game", "22 game-helper"]
    assert run("pgrep", ["-c", "game"], ctx)[1] == "2\n"
    assert run("pgrep", ["-d", ",", "game"], ctx)[1] == "21,22\n"
    assert run("pgrep", ["-a", "-x", "game"], ctx)[1] == "21 C:\\Games\\game.exe -fullscreen\n"
    # The tool's own process (its command line holds the pattern) is never a match
    assert run("pgrep", ["-f", "compat-tool"], ctx)[0] == 1
    assert run("pgrep", [], ctx)[0] == 2
    assert run("pgrep", ["("], ctx)[0] == 2
    assert run("pgrep", ["-u", "someoneelse", "game"], ctx)[0] == 1
    assert run("pgrep", ["-u", "deck", "-x", "game"], ctx)[1] == "21\n"


def test_pkill_signals_matches_but_not_protected():
    table, ctx = world()
    assert run("pkill", ["-STOP", "-f", "Games"], ctx)[0] == 0
    assert table.actions == [(20, "suspend"), (21, "suspend"), (22, "suspend")]
    table.actions.clear()
    assert run("pkill", ["-9", "steam"], ctx)[0] == 1
    assert run("pkill", ["--signal", "KILL", "pythonw"], ctx)[0] == 0 # the host itself may be killed, the loader not
    assert table.actions == [(10, "terminate")]

# --- killall / pidof ---

def test_killall():
    table, ctx = world()
    assert run("killall", ["-9", "game"], ctx) == (0, "", "")
    assert run("killall", ["game.exe"], ctx) == (1, "", "game.exe: no process found\n")
    assert run("killall", ["-q", "nothing"], ctx) == (1, "", "")
    code, _, err = run("killall", ["-9", "steam"], ctx)
    assert code == 1 and "Operation not permitted" in err
    assert run("killall", ["-s", "STOP", "-r", "^game"], ctx)[0] == 0
    assert table.actions == [(21, "terminate"), (22, "suspend")]


def test_killall_wait():
    table, ctx = world()
    slept: list[float] = []
    assert pt.killall(["-w", "game-helper"], ctx, io.StringIO(), io.StringIO(), slept.append) == 0
    assert slept == [] and 22 not in [p.pid for p in table.procs]


def test_pidof():
    _, ctx = world()
    assert run("pidof", ["game", "game-helper"], ctx) == (0, "21 22\n", "")
    assert run("pidof", ["-s", "pythonw"], ctx)[1] == "10\n" # newest that isn't this tool
    assert run("pidof", ["-o", "10", "pythonw"], ctx)[1] == "5\n"
    assert run("pidof", ["nothing"], ctx) == (1, "", "")

# --- the real table ---

def test_own_and_protected_walks_the_parent_chain():
    raw = [pt.RawProcess(1, 0, 1, 1, "loader", False, 0), pt.RawProcess(2, 1, 1, 2, "host", False, 0),
           pt.RawProcess(3, 2, 1, 3, "launcher", False, 0), pt.RawProcess(4, 3, 1, 4, "tool", False, 0),
           pt.RawProcess(9, 4, 1, 0, "older than its child", False, 0)]
    assert pt.own_and_protected(raw, 4, 2) == ([4, 3], [4, 3, 1])
    assert pt.own_and_protected(raw, 4, 77) == ([4], [4, 3, 2, 1])
    raw[0] = pt.RawProcess(1, 0, 1, 5, "pid reused by a newer process", False, 0)
    assert pt.own_and_protected(raw, 4, 2) == ([4, 3], [4, 3])


def test_real_table_hides_other_sessions_and_users():
    me = os.getpid()
    raw = [pt.RawProcess(me, 1, 1, 10, "python.exe", False, 0), pt.RawProcess(100, me, 1, 11, "game.exe", True, 5 * 10**7),
           pt.RawProcess(200, 1, 0, 1, "svchost.exe", False, 0), pt.RawProcess(300, 1, 1, 1, "csrss.exe", False, 0),
           pt.RawProcess(400, 1, 1, 1, "other.exe", False, 0), pt.RawProcess(0, 0, 0, 0, "", False, 0)]
    owners = {me: b"me", 100: b"me", 200: b"me", 300: None, 400: b"other"}
    table = pt.WinProcessTable(lambda: raw, owners.get)
    procs = table.list()
    assert [(p.pid, p.comm, p.stopped) for p in procs] == sorted([(100, "game", True), (me, "python", False)])
    assert next(p for p in procs if p.pid == 100).cpu_seconds() == 5.0


def test_snapshot_sees_this_process():
    snap = {p.pid: p for p in pt.snapshot()}
    me = snap[os.getpid()]
    assert me.ppid == os.getppid() and me.name.lower().startswith("python") and not me.stopped
    assert 4 in snap # System


def _sleeper() -> subprocess.Popen[bytes]:
    # The base interpreter: a venv's python.exe is a launcher, and killing it could leave the real one running
    python = getattr(sys, "_base_executable", sys.executable)
    return subprocess.Popen([python, "-c", "import time\nend = time.time() + 60\nwhile time.time() < end: time.sleep(0.05)"])


def _stopped(pid: int) -> bool:
    return next(p.stopped for p in pt.snapshot() if p.pid == pid)


def test_suspend_resume_and_kill_a_real_child():
    child = _sleeper()
    try:
        time.sleep(0.5)
        table = pt.WinProcessTable()
        ctx = pt.Context(table, own=[], protected=[])
        out, err = io.StringIO(), io.StringIO()
        assert pt.kill(["-SIGSTOP", str(child.pid)], ctx, out, err) == 0, err.getvalue()
        assert _stopped(child.pid)
        assert pt.kill(["-SIGSTOP", str(child.pid)], ctx, out, err) == 0 # already stopped: no second suspend
        assert pt.ps(["--pid", str(child.pid), "-o", "stat="], ctx, out, err) == 0 and out.getvalue() == "T\n"
        assert pt.kill(["-SIGCONT", str(child.pid)], ctx, out, err) == 0
        assert not _stopped(child.pid)
        assert child.poll() is None
        assert pt.kill(["-9", str(child.pid)], ctx, out, err) == 0
        assert child.wait(timeout=10) == 1
        assert pt.kill(["-9", str(child.pid)], ctx, out, err) == 1
    finally:
        child.kill()


def test_resume_undoes_several_suspends():
    child = _sleeper()
    try:
        time.sleep(0.5)
        ntdll, kernel32, _ = pt._api() # pyright: ignore [reportPrivateUsage]
        handle = kernel32.OpenProcess(pt.PROCESS_SUSPEND_RESUME, False, child.pid)
        assert handle
        ntdll.NtSuspendProcess(handle)
        ntdll.NtSuspendProcess(handle)
        kernel32.CloseHandle(handle)
        assert _stopped(child.pid)
        ctx = pt.Context(pt.WinProcessTable())
        assert pt.kill(["-CONT", str(child.pid)], ctx, io.StringIO(), io.StringIO()) == 0
        assert not _stopped(child.pid)
    finally:
        child.kill()


def test_reused_pid_is_not_signalled():
    child = _sleeper()
    try:
        table = pt.WinProcessTable()
        listed = next(p for p in table.list() if p.pid == child.pid)
        listed.handle -= 1 # as if the pid now named a different, newer process
        assert table.act(listed, "terminate") == pt.NO_SUCH_PROCESS
        assert child.poll() is None
    finally:
        child.kill()
