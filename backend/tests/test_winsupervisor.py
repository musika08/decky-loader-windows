import os, subprocess, sys
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    from decky_loader.localplatform import winsupervisor as ws


class Spawner:
    def __init__(self, codes: list[int]):
        self.codes = list(codes)
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        self.calls.append(argv)
        return self.codes.pop(0)


def run(codes: list[int], exe: str = "C:/x/PluginLoader.exe"):
    spawn = Spawner(codes)
    sleeps: list[float] = []
    detached: list[list[str]] = []
    rc = ws.run_supervisor(spawn, sleeps.append, exe, detached.append)
    return rc, spawn, sleeps, detached


def test_restart_code_respawns():
    rc, spawn, _, _ = run([42, 43])
    assert rc == 0
    assert len(spawn.calls) == 2


def test_stop_code_exits_without_respawn():
    rc, spawn, _, _ = run([43])
    assert rc == 0
    assert len(spawn.calls) == 1


def test_child_argv_is_self_command_plus_loader():
    _, spawn, _, _ = run([43])
    assert spawn.calls[0] == ws.self_command() + ["--loader"]


def test_crash_backs_off():
    _, _, sleeps, _ = run([1, 1, 43])
    assert sleeps == [1.0, 2.0]


def test_backoff_resets_after_healthy_run():
    assert ws.next_backoff(16.0, ran_for=61.0) == 1.0
    assert ws.next_backoff(16.0, ran_for=5.0) == 30.0
    assert ws.next_backoff(0.0, 5.0) == 1.0


def test_backoff_is_capped():
    assert ws.next_backoff(30.0, 1.0) == 30.0


@pytest.mark.parametrize("frozen", [True, False])
def test_restart_with_pending_update_hands_off(tmp_path, monkeypatch, frozen):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    exe = tmp_path / "PluginLoader.exe"
    new = tmp_path / "PluginLoader.exe.new"
    exe.write_bytes(b"old")
    new.write_bytes(b"new")
    rc, spawn, _, detached = run([42], str(exe))
    assert rc == 0
    assert len(spawn.calls) == 1
    waited = os.getppid() if frozen else os.getpid()
    assert detached == [[str(new), "--finish-update", str(exe), str(waited)]]


def test_failed_handoff_discards_new_and_keeps_supervising(tmp_path):
    exe = tmp_path / "PluginLoader.exe"
    new = tmp_path / "PluginLoader.exe.new"
    exe.write_bytes(b"old")
    new.write_bytes(b"not an exe")
    spawn = Spawner([42, 43])

    def boom(_argv: list[str]) -> None:
        raise OSError("not a valid exe")

    rc = ws.run_supervisor(spawn, lambda _s: None, str(exe), boom)
    assert rc == 0
    assert len(spawn.calls) == 2
    assert not new.exists()


def test_identical_new_is_not_pending(tmp_path):
    exe = tmp_path / "PluginLoader.exe"
    new = tmp_path / "PluginLoader.exe.new"
    exe.write_bytes(b"same bytes")
    new.write_bytes(b"same bytes")
    assert ws.pending_update_path(str(exe)) is None
    new.write_bytes(b"same bytez")
    assert ws.pending_update_path(str(exe)) == str(new)


def test_cleanup_deletes_identical_new_once_lock_releases(tmp_path):
    exe = tmp_path / "PluginLoader.exe"
    new = tmp_path / "PluginLoader.exe.new"
    exe.write_bytes(b"same")
    new.write_bytes(b"same")
    attempts: list[str] = []
    sleeps: list[float] = []

    def remove(p: str) -> None:
        attempts.append(p)
        if len(attempts) < 4:
            raise PermissionError()  # still locked by the finisher
        os.remove(p)

    t = ws.cleanup_stale_update(str(exe), remove=remove, sleep=sleeps.append)
    assert t is not None
    t.join(5)
    assert len(attempts) == 4
    assert len(sleeps) == 3
    assert not new.exists()


def test_pending_update_path(tmp_path):
    exe = tmp_path / "PluginLoader.exe"
    new = tmp_path / "PluginLoader.exe.new"
    assert ws.pending_update_path(str(exe)) is None
    new.write_bytes(b"")
    assert ws.pending_update_path(str(exe)) is None
    new.write_bytes(b"x")
    assert ws.pending_update_path(str(exe)) == str(new)


def test_finish_update_retries_locked_target():
    calls: list[tuple[str, str]] = []
    failures = [PermissionError(), PermissionError()]

    def copy(src: str, dst: str) -> None:
        calls.append((src, dst))
        if failures:
            raise failures.pop()

    launched: list[str] = []
    rc = ws.finish_update("T.exe", 0, copy=copy, launch=launched.append, sleep=lambda _s: None)
    assert rc == 0
    assert len(calls) == 3
    assert calls[0] == (sys.executable, "T.exe")
    assert launched == ["T.exe"]


def test_finish_update_gives_up_but_relaunches_old_version():
    calls: list[int] = []

    def copy(src: str, dst: str) -> None:
        calls.append(1)
        raise PermissionError()

    launched: list[str] = []
    rc = ws.finish_update("T.exe", 0, copy=copy, launch=launched.append, sleep=lambda _s: None)
    assert rc == 1
    assert len(calls) == 30
    assert launched == ["T.exe"]


def test_finish_update_waits_for_dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    launched: list[str] = []
    rc = ws.finish_update("T.exe", p.pid, copy=lambda s, d: None, launch=launched.append, sleep=lambda _s: None)
    assert rc == 0
    assert launched == ["T.exe"]


def test_supervisor_integration_real_spawn():
    """Real run_supervisor loop with real subprocesses exiting 42 then 43."""
    codes = [42, 43]
    spawned: list[int] = []

    def spawn(_argv: list[str]) -> int:
        code = codes.pop(0)
        spawned.append(code)
        return subprocess.run([sys.executable, "-c", f"import sys; sys.exit({code})"]).returncode

    rc = ws.run_supervisor(spawn, lambda _s: None, "C:/nonexistent/PluginLoader.exe", lambda _a: None)
    assert rc == 0
    assert spawned == [42, 43]


# The loader child runs in a kill-on-close job owned by the supervisor

def _pid_gone(pid: int, wait_ms: int = 5000) -> bool:
    import _winapi
    try:
        h = _winapi.OpenProcess(0x00100000, False, pid) # SYNCHRONIZE
    except OSError:
        return True
    try:
        return _winapi.WaitForSingleObject(h, wait_ms) == 0
    finally:
        _winapi.CloseHandle(h)


def _wait_for_file(path, seconds: float = 30):
    import time
    deadline = time.monotonic() + seconds
    while not os.path.exists(path) and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.2)
    with open(path) as f:
        return f.read()


def test_run_in_job_returns_exit_code_and_uses_cwd_and_env(tmp_path):
    from decky_loader.localplatform import winprocess
    out = tmp_path / "out.txt"
    script = f"import os, sys; open({str(out)!r}, 'w').write(os.getcwd() + '|' + os.environ['X_PROBE']); sys.exit(42)"
    rc = winprocess.run_in_job([sys.executable, "-c", script], {**os.environ, "X_PROBE": "yes"}, str(tmp_path))
    assert rc == 42
    assert out.read_text() == f"{tmp_path}|yes"


def test_run_in_job_kills_leftover_descendants(tmp_path):
    from decky_loader.localplatform import winprocess
    pid_file = tmp_path / "pid"
    script = ("import subprocess, sys; "
              "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              f"open({str(pid_file)!r}, 'w').write(str(p.pid)); sys.exit(3)")
    assert winprocess.run_in_job([sys.executable, "-c", script], dict(os.environ), None) == 3
    assert _pid_gone(int(pid_file.read_text()))


def test_loader_dies_when_supervisor_is_killed(tmp_path):
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pid_file = tmp_path / "pid"
    loader = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    supervisor = ("import sys; from decky_loader.localplatform import winprocess; "
                  f"winprocess.run_in_job([sys.executable, '-c', {loader!r}], None, None)")
    sup = subprocess.Popen([sys.executable, "-c", supervisor], env={**os.environ, "PYTHONPATH": backend})
    try:
        loader_pid = int(_wait_for_file(pid_file))
        assert not _pid_gone(loader_pid, 0)
        sup.kill()
        sup.wait(10)
        assert _pid_gone(loader_pid)
    finally:
        sup.kill()


def test_make_spawn_runs_the_loader_in_a_job():
    calls: list = []

    def run(argv, env, cwd):
        calls.append((argv, env, cwd))
        return 42

    assert ws.make_spawn(r"C:\d\PluginLoader.exe", run=run)(["a", "--loader"]) == 42
    argv, env, cwd = calls[0]
    assert argv == ["a", "--loader"]
    assert env["DECKY_SUPERVISED"] == "1" and env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert cwd == (r"C:\d" if getattr(sys, "frozen", False) else None)


# Relaunch after an update: through the scheduled task when installed, so the loader stays elevated and tracked

class TaskRunner:
    def __init__(self, result=None):
        self.calls: list[list[str]] = []
        self.result = result

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if isinstance(self.result, BaseException):
            raise self.result
        return subprocess.CompletedProcess(argv, self.result or 0)


def test_relaunch_installed_uses_the_scheduled_task():
    run, direct = TaskRunner(), []
    ws.relaunch("T.exe", installed=lambda: True, run=run, direct=direct.append, system_dir=lambda: r"C:\Sys32")
    assert run.calls == [[r"C:\Sys32\schtasks.exe", "/Run", "/TN", "Decky Loader"]]
    assert direct == []


@pytest.mark.parametrize("failure", [subprocess.CalledProcessError(1, "schtasks"), FileNotFoundError("schtasks")])
def test_relaunch_installed_falls_back_to_direct_launch(failure):
    run, direct = TaskRunner(failure), []
    ws.relaunch("T.exe", installed=lambda: True, run=run, direct=direct.append, system_dir=lambda: r"C:\Sys32")
    assert len(run.calls) == 1
    assert direct == [["T.exe"]]


def test_relaunch_portable_launches_directly():
    run, direct = TaskRunner(), []
    ws.relaunch("T.exe", installed=lambda: False, run=run, direct=direct.append, system_dir=lambda: r"C:\Sys32")
    assert run.calls == [] and direct == [["T.exe"]]


def test_finish_update_relaunches_through_relaunch(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(ws, "relaunch", lambda target, **kw: seen.append(target))
    assert ws.finish_update("T.exe", 0, copy=lambda s, d: None, sleep=lambda _s: None) == 0
    assert seen == ["T.exe"]


# Windowed (-noconsole) builds: CLI modes attach to the parent console so their output is visible

class FakeKernel32:
    def __init__(self, ok: bool):
        self.ok = ok
        self.calls: list[int] = []

    def AttachConsole(self, pid: int) -> int:
        self.calls.append(pid)
        return 1 if self.ok else 0


def test_attach_parent_console_rebinds_when_stdout_is_none(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    k, opened = FakeKernel32(True), []

    def opener(path, mode):
        opened.append((path, mode))
        return f"stream{len(opened)}"

    assert ws.attach_parent_console(kernel32=k, opener=opener) is True
    assert k.calls == [-1]
    assert opened == [("CONOUT$", "w"), ("CONOUT$", "w")]
    assert sys.stdout == "stream1" and sys.stderr == "stream2"


def test_attach_parent_console_ignores_failure(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    k, opened = FakeKernel32(False), []
    assert ws.attach_parent_console(kernel32=k, opener=lambda p, m: opened.append(p)) is False
    assert k.calls == [-1] and opened == []
    assert sys.stdout is None and sys.stderr is None


def test_attach_parent_console_ignores_open_errors(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    def opener(p, m):
        raise OSError("no console")

    assert ws.attach_parent_console(kernel32=FakeKernel32(True), opener=opener) is False
    assert sys.stdout is None


def test_attach_parent_console_leaves_a_console_build_alone():
    k = FakeKernel32(True)
    out = sys.stdout
    assert ws.attach_parent_console(kernel32=k, opener=lambda p, m: None) is False
    assert k.calls == [] and sys.stdout is out
