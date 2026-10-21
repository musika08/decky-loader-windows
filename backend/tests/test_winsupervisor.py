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


def test_restart_with_pending_update_hands_off(tmp_path):
    exe = tmp_path / "PluginLoader.exe"
    new = tmp_path / "PluginLoader.exe.new"
    exe.write_bytes(b"old")
    new.write_bytes(b"new")
    rc, spawn, _, detached = run([42], str(exe))
    assert rc == 0
    assert len(spawn.calls) == 1
    assert detached == [[str(new), "--finish-update", str(exe), str(os.getpid())]]


def test_pending_update_path(tmp_path):
    exe = tmp_path / "PluginLoader.exe"
    new = tmp_path / "PluginLoader.exe.new"
    assert ws.pending_update_path(str(exe)) is None
    new.write_bytes(b"")
    assert ws.pending_update_path(str(exe)) is None
    new.write_bytes(b"x")
    assert ws.pending_update_path(str(exe)) == str(new)


def test_cleanup_stale_update(tmp_path):
    exe = tmp_path / "PluginLoader.exe"
    new = tmp_path / "PluginLoader.exe.new"
    new.write_bytes(b"x")
    ws.cleanup_stale_update(str(exe))
    assert not new.exists()
    ws.cleanup_stale_update(str(exe))  # missing file is fine


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


def test_finish_update_gives_up_after_five_failures():
    calls: list[int] = []

    def copy(src: str, dst: str) -> None:
        calls.append(1)
        raise PermissionError()

    launched: list[str] = []
    rc = ws.finish_update("T.exe", 0, copy=copy, launch=launched.append, sleep=lambda _s: None)
    assert rc == 1
    assert len(calls) == 5
    assert launched == []


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
