import os, subprocess, sys
from pathlib import Path
from typing import Any, Callable
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


def run(codes: list[int], exe: str = "C:/x/PluginLoader.exe", self_update: bool | None = None):
    spawn = Spawner(codes)
    sleeps: list[float] = []
    detached: list[list[str]] = []
    rc = ws.run_supervisor(spawn, sleeps.append, exe, detached.append, self_update=self_update)
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


INSTALLED = r"C:\Program Files\Decky Loader"


def test_self_command_in_installed_mode_is_the_isolated_runtime_on_app_main_py():
    package_file = INSTALLED + r"\app\decky_loader\localplatform\winsupervisor.py"
    for interpreter in ("pythonw.exe", "python.exe"):
        exe = INSTALLED + "\\python\\" + interpreter
        assert ws.self_command(executable=exe, frozen=False, package_file=package_file, our_runtime=True) == [
            exe, "-I", INSTALLED + r"\app\main.py"]


def test_self_command_detects_our_runtime_from_the_layout(tmp_path: Path):
    # python\ and app\ side by side (an installer folder, or the installed copy): -I right after the interpreter
    for sub in ("python", r"app\decky_loader\localplatform"):
        (tmp_path / sub).mkdir(parents=True)
    exe = str(tmp_path / "python" / "pythonw.exe")
    package_file = str(tmp_path / "app" / "decky_loader" / "localplatform" / "winsupervisor.py")
    assert ws.self_command(executable=exe, frozen=False, package_file=package_file) == [exe, "-I", str(tmp_path / "app" / "main.py")]
    # The same interpreter name elsewhere (a development venv) gets no -I: it would drop the checkout from sys.path
    venv = str(tmp_path / ".venv" / "Scripts" / "python.exe")
    assert ws.self_command(executable=venv, frozen=False, package_file=package_file) == [venv, str(tmp_path / "app" / "main.py")]


def test_child_env_drops_every_python_variable_only():
    env = {"PYTHONINSPECT": "1", "PythonPath": "x", "PYTHONSTARTUP": "s", "PYTHONHOME": "h", "PATH": "p",
           "DECKY_SUPERVISED": "1", "MY_PYTHONISH": "keep", "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    assert ws.child_env(env) == {"PATH": "p", "DECKY_SUPERVISED": "1", "MY_PYTHONISH": "keep",
                                 "PYINSTALLER_RESET_ENVIRONMENT": "1"}


def test_spawned_loader_gets_no_python_variables(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PYTHONINSPECT", "1")
    monkeypatch.setenv("PYTHONWARNINGS", "default")
    seen: list[dict[str, str] | None] = []

    def run(argv: list[str], env: dict[str, str] | None, cwd: str | None) -> int:
        seen.append(env)
        return 43

    ws.make_spawn(r"C:\d\python\pythonw.exe", run=run)(["a", "--loader"])
    assert seen[0] is not None and not [k for k in seen[0] if k.upper().startswith("PYTHON")]
    assert seen[0]["DECKY_SUPERVISED"] == "1"


def test_apply_build_config_sets_only_the_hook_keys(tmp_path: Path):
    path = tmp_path / "build_config.json"
    path.write_text('{"DECKY_REPO": "me/fork", "DECKY_BRANCH": "win", "DECKY_PACKAGED_BUILD": "1", '
                    '"DECKY_NOCONSOLE": "1", "PATH": "evil", "DECKY_X": 5}', encoding="utf-8")
    env: dict[str, str] = {"DECKY_REPO": "old", "PATH": "p"}
    applied = ws.apply_build_config(str(path), env)
    assert env == {"DECKY_REPO": "me/fork", "DECKY_BRANCH": "win", "DECKY_PACKAGED_BUILD": "1", "DECKY_NOCONSOLE": "1", "PATH": "p"}
    assert applied == {k: env[k] for k in ws.BUILD_CONFIG_KEYS}


def test_apply_build_config_without_or_with_a_broken_file_changes_nothing(tmp_path: Path):
    env: dict[str, str] = {}
    assert ws.apply_build_config(str(tmp_path / "missing.json"), env) == {} and env == {}
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert ws.apply_build_config(str(tmp_path / "bad.json"), env) == {} and env == {}
    (tmp_path / "list.json").write_text('["DECKY_REPO"]', encoding="utf-8")
    assert ws.apply_build_config(str(tmp_path / "list.json"), env) == {} and env == {}


def test_build_config_keys_match_the_pyinstaller_runtime_hook():
    spec = (Path(__file__).resolve().parent.parent / "pyinstaller.spec").read_text(encoding="utf-8")
    for key in ws.BUILD_CONFIG_KEYS:
        assert f"os.environ['{key}']" in spec, key


def test_self_command_of_a_pyinstaller_build_is_the_exe_alone():
    assert ws.self_command(executable=r"C:\p\PluginLoader-win.exe", frozen=True) == [r"C:\p\PluginLoader-win.exe"]


def test_self_command_defaults_to_this_interpreter_and_the_checkout_main_py():
    cmd = ws.self_command()
    assert cmd[0] == sys.executable
    assert os.path.normcase(cmd[1]) == os.path.normcase(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"))


def test_supervisor_from_source_spawns_the_loader_with_its_interpreter_and_main_py(monkeypatch: pytest.MonkeyPatch):
    exe = INSTALLED + r"\python\pythonw.exe"
    monkeypatch.setattr(sys, "executable", exe)
    monkeypatch.delattr(sys, "frozen", raising=False)
    _, spawn, _, _ = run([43], exe=exe)
    assert spawn.calls[0] == [exe, ws.self_command()[1], "--loader"]


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
def test_restart_with_pending_update_hands_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen: bool):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    exe = tmp_path / "PluginLoader.exe"
    new = tmp_path / "PluginLoader.exe.new"
    exe.write_bytes(b"old")
    new.write_bytes(b"new")
    rc, spawn, _, detached = run([42], str(exe), self_update=True)
    assert rc == 0
    assert len(spawn.calls) == 1
    waited = os.getppid() if frozen else os.getpid()
    assert detached == [[str(new), "--finish-update", str(exe), str(waited)]]


def test_from_source_a_pending_update_is_never_swapped_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # From source `exe` is the Python interpreter: a stray python.exe.new must not replace it
    monkeypatch.delattr(sys, "frozen", raising=False)
    exe = tmp_path / "python.exe"
    new = tmp_path / "python.exe.new"
    exe.write_bytes(b"python")
    new.write_bytes(b"not python")
    rc, spawn, _, detached = run([42, 43], str(exe))
    assert rc == 0 and len(spawn.calls) == 2 and detached == []
    assert new.read_bytes() == b"not python" and exe.read_bytes() == b"python"


def test_failed_handoff_discards_new_and_keeps_supervising(tmp_path: Path):
    exe = tmp_path / "PluginLoader.exe"
    new = tmp_path / "PluginLoader.exe.new"
    exe.write_bytes(b"old")
    new.write_bytes(b"not an exe")
    spawn = Spawner([42, 43])

    def boom(_argv: list[str]) -> None:
        raise OSError("not a valid exe")

    rc = ws.run_supervisor(spawn, lambda _s: None, str(exe), boom, self_update=True)
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
    import _winapi # pyright: ignore [reportMissingImports]
    try:
        h = _winapi.OpenProcess(0x00100000, False, pid) # SYNCHRONIZE
    except OSError:
        return True
    try:
        return _winapi.WaitForSingleObject(h, wait_ms) == 0
    finally:
        _winapi.CloseHandle(h)


def _wait_for_file(path: Path, seconds: float = 30) -> str:
    import time
    deadline = time.monotonic() + seconds
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.2)
    return path.read_text()


def test_run_in_job_returns_exit_code_and_uses_cwd_and_env(tmp_path: Path):
    from decky_loader.localplatform import winprocess
    out = tmp_path / "out.txt"
    script = f"import os, sys; open({str(out)!r}, 'w').write(os.getcwd() + '|' + os.environ['X_PROBE']); sys.exit(42)"
    rc = winprocess.run_in_job([sys.executable, "-c", script], {**os.environ, "X_PROBE": "yes"}, str(tmp_path))
    assert rc == 42
    assert out.read_text() == f"{tmp_path}|yes"


def test_run_in_job_kills_leftover_descendants(tmp_path: Path):
    from decky_loader.localplatform import winprocess
    pid_file = tmp_path / "pid"
    script = ("import subprocess, sys; "
              "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              f"open({str(pid_file)!r}, 'w').write(str(p.pid)); sys.exit(3)")
    assert winprocess.run_in_job([sys.executable, "-c", script], dict(os.environ), None) == 3
    assert _pid_gone(int(pid_file.read_text()))


def test_loader_dies_when_supervisor_is_killed(tmp_path: Path):
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
    calls: list[tuple[list[str], dict[str, str] | None, str | None]] = []

    def run(argv: list[str], env: dict[str, str] | None, cwd: str | None) -> int:
        calls.append((argv, env, cwd))
        return 42

    assert ws.make_spawn(r"C:\d\PluginLoader.exe", run=run)(["a", "--loader"]) == 42
    argv, env, cwd = calls[0]
    assert argv == ["a", "--loader"]
    assert env is not None and env["DECKY_SUPERVISED"] == "1" and env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert cwd == (r"C:\d" if getattr(sys, "frozen", False) else None)


# Relaunch after an update: through the scheduled task when installed, so the loader stays elevated and tracked

class TaskRunner:
    def __init__(self, failure: BaseException | None = None):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []
        self.failure = failure

    def __call__(self, argv: list[str], **kwargs: Any) -> "subprocess.CompletedProcess[bytes]":
        self.calls.append(list(argv))
        self.kwargs.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return subprocess.CompletedProcess(argv, 0)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def test_relaunch_installed_uses_the_scheduled_task():
    run, direct, clk = TaskRunner(), list[list[str]](), FakeClock()
    seen: list[str] = []

    def running(target: str) -> bool:
        seen.append(target)
        return True

    ws.relaunch("T.exe", installed=lambda: True, run=run, direct=direct.append, system_dir=lambda: r"C:\Sys32",
                running=running, sleep=clk.sleep, clock=clk.clock)
    assert run.calls == [[r"C:\Sys32\schtasks.exe", "/Run", "/TN", "Decky Loader"]]
    assert run.kwargs[0]["creationflags"] == 0x08000000 == subprocess.CREATE_NO_WINDOW
    assert run.kwargs[0]["check"] is True
    assert seen == ["T.exe"] and clk.sleeps == []
    assert direct == []


def test_relaunch_waits_for_the_task_to_start_the_exe():
    run, direct, clk = TaskRunner(), list[list[str]](), FakeClock()
    answers = [False, False, False, True]
    ws.relaunch("T.exe", installed=lambda: True, run=run, direct=direct.append, system_dir=lambda: r"C:\Sys32",
                running=lambda t: answers.pop(0), sleep=clk.sleep, clock=clk.clock)
    assert clk.sleeps == [0.25, 0.25, 0.25] and direct == []


def test_relaunch_launches_directly_when_the_task_never_starts_the_exe(caplog: pytest.LogCaptureFixture):
    run, direct, clk = TaskRunner(), list[list[str]](), FakeClock()
    polls: list[float] = []

    def running(target: str) -> bool:
        polls.append(clk.now)
        return False

    ws.relaunch("T.exe", installed=lambda: True, run=run, direct=direct.append, system_dir=lambda: r"C:\Sys32",
                running=running, sleep=clk.sleep, clock=clk.clock)
    assert direct == [["T.exe"]]
    assert polls[0] == 0.0 and polls[-1] == 10.0 and clk.now == 10.0
    assert "did not start" in caplog.text


def test_image_running_sees_a_real_process_from_that_image():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert ws.image_running(sys.executable) is True
        assert ws.image_running(sys.executable + ".missing") is False
    finally:
        p.kill()
        p.wait(10)


@pytest.mark.parametrize("failure", [subprocess.CalledProcessError(1, "schtasks"), FileNotFoundError("schtasks")])
def test_relaunch_installed_falls_back_to_direct_launch(failure: BaseException):
    run, direct = TaskRunner(failure), list[list[str]]()
    ws.relaunch("T.exe", installed=lambda: True, run=run, direct=direct.append, system_dir=lambda: r"C:\Sys32",
                running=lambda t: pytest.fail("no polling after a failed schtasks"), sleep=lambda s: None)
    assert len(run.calls) == 1
    assert direct == [["T.exe"]]


def test_relaunch_portable_launches_directly():
    run, direct = TaskRunner(), list[list[str]]()
    ws.relaunch("T.exe", installed=lambda: False, run=run, direct=direct.append, system_dir=lambda: r"C:\Sys32")
    assert run.calls == [] and direct == [["T.exe"]]


def test_finish_update_relaunches_through_relaunch(monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []

    def fake_relaunch(target: str, **_kw: Any) -> None:
        seen.append(target)

    monkeypatch.setattr(ws, "relaunch", fake_relaunch)
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


def _no_std_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)


def test_attach_parent_console_rebinds_when_stdout_is_none(monkeypatch: pytest.MonkeyPatch):
    _no_std_streams(monkeypatch)
    k, opened = FakeKernel32(True), list[tuple[str, str]]()

    def opener(path: str, mode: str) -> str:
        opened.append((path, mode))
        return f"stream{len(opened)}"

    assert ws.attach_parent_console(kernel32=k, opener=opener) is True
    assert k.calls == [-1]
    assert opened == [("CONOUT$", "w"), ("CONOUT$", "w")]
    assert str(sys.stdout) == "stream1" and str(sys.stderr) == "stream2"


def test_attach_parent_console_ignores_failure(monkeypatch: pytest.MonkeyPatch):
    _no_std_streams(monkeypatch)
    k, opened = FakeKernel32(False), list[str]()

    def opener(path: str, mode: str) -> None:
        opened.append(path)

    assert ws.attach_parent_console(kernel32=k, opener=opener) is False
    assert k.calls == [-1] and opened == []
    assert sys.stdout is None and sys.stderr is None # pyright: ignore [reportUnnecessaryComparison]


def test_attach_parent_console_ignores_open_errors(monkeypatch: pytest.MonkeyPatch):
    _no_std_streams(monkeypatch)

    def opener(path: str, mode: str) -> None:
        raise OSError("no console")

    assert ws.attach_parent_console(kernel32=FakeKernel32(True), opener=opener) is False
    assert sys.stdout is None # pyright: ignore [reportUnnecessaryComparison]


def test_attach_parent_console_leaves_a_console_build_alone():
    k = FakeKernel32(True)
    out = sys.stdout
    assert ws.attach_parent_console(kernel32=k, opener=lambda p, m: None) is False
    assert k.calls == [] and sys.stdout is out


# Onefile builds: our code runs in the bootloader's child, so the user's terminal is the bootloader's parent

class ScriptedKernel32:
    def __init__(self, results: dict[int, bool]):
        self.results = results
        self.calls: list[int] = []

    def AttachConsole(self, pid: int) -> int:
        self.calls.append(pid)
        return 1 if self.results.get(pid, False) else 0


def _frozen_attach(monkeypatch: pytest.MonkeyPatch, k: ScriptedKernel32, parent_of: Callable[[int], int | None]) -> tuple[bool, list[int]]:
    _no_std_streams(monkeypatch)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    looked_up: list[int] = []

    def lookup(pid: int) -> int | None:
        looked_up.append(pid)
        return parent_of(pid)

    ok = ws.attach_parent_console(kernel32=k, opener=lambda p, m: f"{p}:{m}", ppid=lambda: 500, parent_of=lookup)
    return ok, looked_up


def test_frozen_attaches_to_the_bootloaders_parent_first(monkeypatch: pytest.MonkeyPatch):
    k = ScriptedKernel32({900: True, -1: True})
    ok, looked_up = _frozen_attach(monkeypatch, k, lambda pid: 900)
    assert ok is True and looked_up == [500] and k.calls == [900]
    assert str(sys.stdout) == "CONOUT$:w"


def test_frozen_falls_back_to_the_direct_parent(monkeypatch: pytest.MonkeyPatch):
    k = ScriptedKernel32({900: False, -1: True})
    ok, _ = _frozen_attach(monkeypatch, k, lambda pid: 900)
    assert ok is True and k.calls == [900, -1]


def _gone(pid: int) -> int | None:
    return None


def _idle(pid: int) -> int | None:
    return 0


def _snapshot_fails(pid: int) -> int | None:
    raise OSError("snapshot")


@pytest.mark.parametrize("parent_of", [_gone, _idle, _snapshot_fails])
def test_frozen_without_a_known_grandparent_uses_the_direct_parent(monkeypatch: pytest.MonkeyPatch, parent_of: Callable[[int], int | None]):
    k = ScriptedKernel32({-1: True})
    ok, _ = _frozen_attach(monkeypatch, k, parent_of)
    assert ok is True and k.calls == [-1]


def test_frozen_with_no_console_anywhere_prints_nowhere(monkeypatch: pytest.MonkeyPatch):
    k = ScriptedKernel32({})
    ok, _ = _frozen_attach(monkeypatch, k, lambda pid: 900)
    assert ok is False and k.calls == [900, -1]
    assert sys.stdout is None # pyright: ignore [reportUnnecessaryComparison]


def test_not_frozen_never_looks_up_the_grandparent(monkeypatch: pytest.MonkeyPatch):
    _no_std_streams(monkeypatch)
    monkeypatch.delattr(sys, "frozen", raising=False)
    k, looked_up = ScriptedKernel32({-1: True}), list[int]()

    def lookup(pid: int) -> int | None:
        looked_up.append(pid)
        return 900

    assert ws.attach_parent_console(kernel32=k, opener=lambda p, m: None, ppid=lambda: 500, parent_of=lookup) is True
    assert looked_up == [] and k.calls == [-1]


def test_finish_update_branch_never_attaches_a_console():
    # Closing a console window must not kill the finisher mid-copy, so main.py gives that branch no console
    import ast
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    tree = ast.parse(main_py.read_text(encoding="utf-8"))
    branches = [n for n in ast.walk(tree) if isinstance(n, ast.If) and "--finish-update" in ast.unparse(n.test)]
    assert len(branches) == 1
    calls = {ast.unparse(c.func) for b in branches[0].body for c in ast.walk(b) if isinstance(c, ast.Call)}
    assert "cli_console" not in calls and "finish_update" in calls


# Single-instance supervisor lock

class FakeMutexKernel32:
    def __init__(self, handle: int | None, error: int):
        self.handle, self.error = handle, error
        self.calls: list[tuple[Any, bool, str]] = []

    def CreateMutexW(self, attrs: Any, initial_owner: bool, name: str) -> int | None:
        self.calls.append((attrs, initial_owner, name))
        return self.handle


def _lock(k: FakeMutexKernel32) -> tuple[bool, list[str]]:
    order: list[str] = []
    def clear() -> None:
        order.append("clear")
    def last() -> int:
        order.append("get")
        return k.error
    return ws.acquire_supervisor_lock(kernel32=k, last_error=last, clear_last_error=clear), order


def test_supervisor_lock_acquired_when_the_mutex_is_new(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ws, "_supervisor_lock", None)
    k = FakeMutexKernel32(1234, 0)
    ok, order = _lock(k)
    assert ok is True
    # Not inheritable (no SECURITY_ATTRIBUTES), not owned, the fixed session-local name
    assert k.calls == [(None, False, r"Local\DeckyLoader-Supervisor")]
    assert order == ["clear", "get"]
    assert ws._supervisor_lock == 1234 # pyright: ignore [reportPrivateUsage]


def test_supervisor_lock_refused_when_another_supervisor_holds_it(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setattr(ws, "_supervisor_lock", None)
    ok, _ = _lock(FakeMutexKernel32(1234, ws.ERROR_ALREADY_EXISTS))
    assert ok is False
    assert ws._supervisor_lock is None # pyright: ignore [reportPrivateUsage]
    assert [r.getMessage() for r in caplog.records] == ["Another Decky Loader supervisor is already running in this session, exiting"]


def test_supervisor_lock_refused_when_the_mutex_exists_but_cannot_be_opened(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ws, "_supervisor_lock", None)
    ok, _ = _lock(FakeMutexKernel32(None, ws.ERROR_ACCESS_DENIED))
    assert ok is False


def test_supervisor_lock_failure_still_starts_decky(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ws, "_supervisor_lock", None)
    ok, _ = _lock(FakeMutexKernel32(None, 87))
    assert ok is True
    assert ws._supervisor_lock is None # pyright: ignore [reportPrivateUsage]


def test_supervisor_branch_takes_the_lock_before_anything_else():
    import ast
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    tree = ast.parse(main_py.read_text(encoding="utf-8"))
    branches = [n for n in ast.walk(tree) if isinstance(n, ast.If) and ast.unparse(n.test) == "len(sys.argv) == 1"]
    assert len(branches) == 1
    # Only this branch's own body, in source order (ast.walk is breadth-first)
    nodes = [c for stmt in branches[0].body for c in ast.walk(stmt) if isinstance(c, ast.Call)]
    calls = [ast.unparse(c.func) for c in sorted(nodes, key=lambda c: (c.lineno, c.col_offset))]
    first = calls.index("winsupervisor.acquire_supervisor_lock")
    assert first < calls.index("winsupervisor.cleanup_stale_update") and first < calls.index("winsupervisor.run_supervisor")


_BACKEND = Path(__file__).resolve().parent.parent

# Runs the real main.py supervisor branch with everything after the lock replaced by a tripwire
_RUN_MAIN = r'''
import os, runpy, sys
sys.path.insert(0, sys.argv[1])
from decky_loader.localplatform import winsupervisor as ws
marker = sys.argv[2]
def tripwire(*a, **k):
    open(marker, "w").write("spawned")
    raise SystemExit(99)
ws.cleanup_stale_update = tripwire
ws.make_spawn = tripwire
ws.run_supervisor = tripwire
main_py = os.path.join(sys.argv[1], "main.py")
sys.argv = [main_py]
runpy.run_path(main_py, run_name="__main__")
'''

def _create_mutex(name: str, sddl: str | None = None) -> int:
    import ctypes
    from ctypes import wintypes
    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wintypes.BOOL)]
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateMutexW.restype = ctypes.c_void_p
    sa: Any = None
    if sddl is not None:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        sd = ctypes.c_void_p()
        assert advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(sd), None)
        sa = ctypes.byref(SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), sd, False))
    handle = k.CreateMutexW(sa, False, name)
    assert handle, ctypes.get_last_error()
    return handle


def _close(handle: int) -> None:
    import _winapi # pyright: ignore [reportMissingImports]
    _winapi.CloseHandle(handle)


def _lock_in_subprocess(name: str) -> subprocess.CompletedProcess[str]:
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from decky_loader.localplatform import winsupervisor as ws; "
            "sys.exit(10 if ws.acquire_supervisor_lock(name=sys.argv[2]) else 11)")
    return subprocess.run([sys.executable, "-c", code, str(_BACKEND), name], capture_output=True, text=True, timeout=60)


def test_real_second_supervisor_exits_without_spawning(tmp_path: Path):
    import time
    held = _create_mutex(ws.SUPERVISOR_MUTEX)
    marker = tmp_path / "spawned"
    try:
        started = time.monotonic()
        proc = subprocess.run([sys.executable, "-c", _RUN_MAIN, str(_BACKEND), str(marker)], capture_output=True,
                              text=True, timeout=60)
        elapsed = time.monotonic() - started
    finally:
        _close(held)
    assert proc.returncode == 0, proc.stderr
    assert not marker.exists()
    assert elapsed < 20
    assert "Another Decky Loader supervisor is already running in this session" in proc.stderr


def test_real_lock_is_taken_when_free_and_released_when_the_holder_exits():
    import secrets
    name = "Local\\DeckyLoader-Test-" + secrets.token_hex(8)
    assert _lock_in_subprocess(name).returncode == 10
    # The first holder has exited, so the lock is free again
    assert _lock_in_subprocess(name).returncode == 10
    held = _create_mutex(name)
    try:
        assert _lock_in_subprocess(name).returncode == 11
    finally:
        _close(held)


def test_real_lock_refused_for_a_mutex_this_user_cannot_open():
    import secrets
    name = "Local\\DeckyLoader-Test-" + secrets.token_hex(8)
    held = _create_mutex(name, sddl="D:") # Empty DACL: nobody, us included, gets MUTEX_ALL_ACCESS
    try:
        proc = _lock_in_subprocess(name)
    finally:
        _close(held)
    assert proc.returncode == 11, proc.stderr


# Onedir detection (installed mode installs only a onedir build)

def _frozen_as(monkeypatch: pytest.MonkeyPatch, exe: Path, meipass: Path | None) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    if meipass is None:
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    else:
        monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)


def test_onedir_build_is_meipass_internal_next_to_the_exe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    exe = tmp_path / "Decky Loader" / "PluginLoader.exe"
    (exe.parent / "_internal").mkdir(parents=True)
    _frozen_as(monkeypatch, exe, exe.parent / "_internal")
    assert ws.is_onedir_build() is True
    # Case and separators do not matter
    _frozen_as(monkeypatch, exe, Path(str(exe.parent / "_INTERNAL").upper().replace("\\", "/")))
    assert ws.is_onedir_build() is True


def test_onefile_build_is_not_onedir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    exe = tmp_path / "portable" / "PluginLoader.exe"
    _frozen_as(monkeypatch, exe, tmp_path / "Temp" / "_MEI12345")
    assert ws.is_onedir_build() is False
    _frozen_as(monkeypatch, exe, exe.parent / "other")
    assert ws.is_onedir_build() is False
    _frozen_as(monkeypatch, exe, None)
    assert ws.is_onedir_build() is False


def test_not_frozen_is_not_onedir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    assert ws.is_onedir_build() is False


def test_onedir_attaches_to_the_direct_parent_without_a_grandparent_lookup(monkeypatch: pytest.MonkeyPatch):
    # No bootloader process in onedir: the terminal the user typed into is our direct parent
    _no_std_streams(monkeypatch)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    k, looked_up = ScriptedKernel32({900: True, -1: True}), list[int]()

    def lookup(pid: int) -> int | None:
        looked_up.append(pid)
        return 900

    ok = ws.attach_parent_console(kernel32=k, opener=lambda p, m: f"{p}:{m}", ppid=lambda: 500, parent_of=lookup,
                                  onedir=lambda: True)
    assert ok is True and looked_up == [] and k.calls == [-1]


def test_spec_builds_onedir_only_when_asked():
    spec = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pyinstaller.spec")
    with open(spec, encoding="utf-8") as f:
        text = f.read()
    assert "onedir = os.getenv('DECKY_ONEDIR', '0') == '1'" in text
    assert "exclude_binaries=True" in text and "COLLECT(" in text
