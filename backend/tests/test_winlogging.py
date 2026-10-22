import logging, os, sys
from pathlib import Path
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    from decky_loader.localplatform import winlogging as wl
    from decky_loader.localplatform import winsupervisor as ws

FMT = "[%(module)s][%(levelname)s]: %(message)s"


class Dirs:
    '''Stands in for the handle-based trusted-directory checks.'''
    def __init__(self, trusted_root: bool = True, ensure_error: OSError | None = None) -> None:
        self.trusted_root = trusted_root
        self.ensure_error = ensure_error
        self.events: list[tuple[str, str]] = []

    def open_trusted(self, p: str) -> Any:
        self.events.append(("open", p))
        return p if self.trusted_root else None

    def ensure_trusted(self, p: str) -> Any:
        self.events.append(("ensure", p))
        if self.ensure_error is not None:
            raise self.ensure_error
        os.makedirs(p, exist_ok=True)
        return p

    def close(self, h: Any) -> None:
        self.events.append(("close", h))

    def kw(self) -> dict[str, Any]:
        return dict(open_trusted=self.open_trusted, ensure_trusted=self.ensure_trusted, close_dir=self.close)


def _installed(tmp_path: Path, dirs: Dirs, **over: Any) -> dict[str, Any]:
    root = tmp_path / "ProgramData" / "decky"
    root.mkdir(parents=True)
    kw: dict[str, Any] = dict(installed_root=lambda: str(root), homebrew=lambda: str(tmp_path / "homebrew"), **dirs.kw())
    kw.update(over)
    return kw


@pytest.fixture
def target() -> Any:
    log = logging.getLogger("test-winlogging")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    yield log
    for h in _files(log):
        h.close()
        log.removeHandler(h)


def _files(log: logging.Logger) -> list[logging.Handler]:
    return [h for h in log.handlers if isinstance(h, logging.FileHandler)]


def test_installed_logs_under_the_trusted_root(tmp_path: Path, target: logging.Logger):
    dirs = Dirs()
    root = str(tmp_path / "ProgramData" / "decky")
    logs = os.path.join(root, "logs")
    path = wl.setup_file_logging("loader.log", FMT, target=target, **_installed(tmp_path, dirs))
    assert path == os.path.join(logs, "loader.log")
    # The root is validated and held open while logs is checked and created, then released
    assert dirs.events == [("open", root), ("ensure", logs), ("close", logs), ("close", root)]
    target.info("hello")
    for h in target.handlers:
        h.flush()
    assert Path(path).read_text(encoding="utf-8").splitlines() == ["[test_winlogging][INFO]: hello"]
    assert not (tmp_path / "homebrew").exists()


def test_installed_untrusted_root_writes_no_file(tmp_path: Path, target: logging.Logger, caplog: pytest.LogCaptureFixture):
    dirs = Dirs(trusted_root=False)
    assert wl.setup_file_logging("loader.log", FMT, target=target, **_installed(tmp_path, dirs)) is None
    assert [e[0] for e in dirs.events] == ["open"] and _files(target) == []
    assert not (tmp_path / "ProgramData" / "decky" / "logs").exists()
    assert "not owned and protected" in caplog.text


def test_installed_logs_dir_that_cannot_be_made_trusted(tmp_path: Path, target: logging.Logger):
    dirs = Dirs(ensure_error=PermissionError("denied"))
    root = str(tmp_path / "ProgramData" / "decky")
    assert wl.setup_file_logging("loader.log", FMT, target=target, **_installed(tmp_path, dirs)) is None
    assert dirs.events[-1] == ("close", root) and _files(target) == []


def test_installed_uses_the_installer_trusted_dir_logic(monkeypatch: pytest.MonkeyPatch):
    from decky_loader.localplatform import wininstall as wi
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(wi, "ensure_trusted_dir", lambda p, remove_links: calls.append((p, remove_links)) or "h")
    assert wl._ensure_trusted(r"C:\PD\decky\logs") == "h"
    assert calls == [(r"C:\PD\decky\logs", True)] # a link at logs is dropped, never followed


def test_portable_logs_in_homebrew(tmp_path: Path, target: logging.Logger):
    dirs = Dirs()
    kw = dict(installed_root=lambda: None, homebrew=lambda: str(tmp_path / "homebrew"), **dirs.kw())
    path = wl.setup_file_logging("loader.log", FMT, target=target, **kw)
    assert path == str(tmp_path / "homebrew" / "logs" / "loader.log") and os.path.isfile(path)
    assert dirs.events == []


def _junction(link: Path, target: Path) -> None:
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def test_portable_never_follows_a_junction(tmp_path: Path, target: logging.Logger):
    home = tmp_path / "homebrew"
    home.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _junction(home / "logs", elsewhere)
    kw = dict(installed_root=lambda: None, homebrew=lambda: str(home))
    assert wl.setup_file_logging("loader.log", FMT, target=target, **kw) is None
    assert os.listdir(elsewhere) == [] and _files(target) == []


def test_portable_never_follows_a_linked_log_file(tmp_path: Path, target: logging.Logger):
    logs = tmp_path / "homebrew" / "logs"
    logs.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _junction(logs / "loader.log", elsewhere)
    kw = dict(installed_root=lambda: None, homebrew=lambda: str(tmp_path / "homebrew"))
    assert wl.setup_file_logging("loader.log", FMT, target=target, **kw) is None
    assert _files(target) == []


def test_log_file_rotates(tmp_path: Path, target: logging.Logger, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(wl, "LOG_MAX_BYTES", 200)
    kw = dict(installed_root=lambda: None, homebrew=lambda: str(tmp_path / "homebrew"))
    path = wl.setup_file_logging("loader.log", FMT, target=target, **kw)
    assert path is not None
    for i in range(100):
        target.info("line %s %s", i, "x" * 40)
    names = sorted(os.listdir(os.path.dirname(path)))
    assert names == ["loader.log", "loader.log.1", "loader.log.2", "loader.log.3"]
    assert wl.LOG_BACKUP_COUNT == 3


def test_open_failure_writes_no_file(tmp_path: Path, target: logging.Logger, monkeypatch: pytest.MonkeyPatch):
    def broken(*_a: Any, **_k: Any) -> Any:
        raise PermissionError("denied")
    monkeypatch.setattr(wl, "RotatingFileHandler", broken)
    kw = dict(installed_root=lambda: None, homebrew=lambda: str(tmp_path / "homebrew"))
    assert wl.setup_file_logging("loader.log", FMT, target=target, **kw) is None


def test_formats_match_the_loader_console(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRIVILEGED_PATH", str(tmp_path / "priv"))
    monkeypatch.setenv("UNPRIVILEGED_PATH", str(tmp_path / "unpriv"))
    monkeypatch.setenv("CHOWN_PLUGIN_PATH", "0")
    from decky_loader import main
    assert ws.LOG_FORMAT == main.LOG_FORMAT


def test_loader_main_logs_to_loader_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRIVILEGED_PATH", str(tmp_path / "priv"))
    monkeypatch.setenv("UNPRIVILEGED_PATH", str(tmp_path / "unpriv"))
    monkeypatch.setenv("CHOWN_PLUGIN_PATH", "0")
    from decky_loader import main
    calls: list[tuple[str, str]] = []

    class Stop(Exception):
        pass

    def fake_setup(name: str, fmt: str) -> str:
        calls.append((name, fmt))
        return "x"

    def stop(*_a: Any) -> Any:
        raise Stop()

    monkeypatch.setattr(wl, "setup_file_logging", fake_setup)
    monkeypatch.setattr(main, "setproctitle", lambda *_a: None)
    monkeypatch.setattr(main, "setthreadtitle", lambda *_a: None)
    monkeypatch.setattr(main, "get_system_pythonpaths", stop) # right after logging is set up
    with pytest.raises(Stop):
        main.main()
    assert calls == [("loader.log", main.LOG_FORMAT)]


def test_supervisor_logs_to_console_and_supervisor_log():
    calls: list[Any] = []
    path = ws.setup_logging(basic_config=lambda **kw: calls.append(kw), log_level=lambda: 20,
                            file_logging=lambda name, fmt: calls.append((name, fmt)) or r"C:\x\supervisor.log")
    assert path == r"C:\x\supervisor.log"
    assert calls == [dict(level=20, format=ws.LOG_FORMAT), ("supervisor.log", ws.LOG_FORMAT)]


def test_supervisor_logging_set_up_before_the_lock(tmp_path: Path):
    # backend/main.py: the supervisor branch sets up logging first, so "another supervisor is running" is logged too
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert src.index("winsupervisor.setup_logging()") < src.index("winsupervisor.acquire_supervisor_lock()")


def test_supervisor_survives_an_invalid_log_level(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    calls: list[Any] = []
    with caplog.at_level(logging.WARNING, logger="winsupervisor"):
        path = ws.setup_logging(basic_config=lambda **kw: calls.append(kw),
                                file_logging=lambda name, fmt: calls.append((name, fmt)) or None)
    assert path is None
    assert calls == [dict(level=logging.INFO, format=ws.LOG_FORMAT), ("supervisor.log", ws.LOG_FORMAT)]
    assert "Invalid LOG_LEVEL 'VERBOSE', using INFO" in caplog.text


def test_supervisor_uses_a_valid_log_level(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    calls: list[Any] = []
    ws.setup_logging(basic_config=lambda **kw: calls.append(kw), file_logging=lambda name, fmt: None)
    assert calls == [dict(level=logging.DEBUG, format=ws.LOG_FORMAT)]
