import asyncio, sys
from typing import Any
import pytest


@pytest.fixture
def main_mod(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    # Importing the loader module resolves (and creates) the homebrew paths, so point them at tmp_path
    monkeypatch.setenv("PRIVILEGED_PATH", str(tmp_path / "priv"))
    monkeypatch.setenv("UNPRIVILEGED_PATH", str(tmp_path / "unpriv"))
    monkeypatch.setenv("CHOWN_PLUGIN_PATH", "0")
    from decky_loader import main
    async def no_sleep(_s: float) -> None:
        pass
    monkeypatch.setattr(main, "sleep", no_sleep)
    return main


class FakeUpdater:
    def __init__(self) -> None:
        self.shutdowns = 0

    async def do_shutdown(self) -> None:
        self.shutdowns += 1


class FakeTab:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    async def has_global_var(self, *_a: Any) -> bool:
        return False

    async def evaluate_js(self, script: str, *_a: Any) -> None:
        self.scripts.append(script)


def _manager(main: Any, crash_count: int) -> Any:
    pm = main.PluginManager.__new__(main.PluginManager)
    pm.reinject = True
    pm.webhelper_crash_count = crash_count
    pm.last_webhelper_exit = main.time() # the next crash lands within a minute
    pm.updater = FakeUpdater()
    return pm


@pytest.mark.parametrize("windows", [True, False])
def test_repeated_steam_restarts(main_mod: Any, monkeypatch: pytest.MonkeyPatch, windows: bool):
    monkeypatch.setattr(main_mod, "ON_WINDOWS", windows)
    pm = _manager(main_mod, 4)
    if windows:
        asyncio.run(pm.handle_crash()) # keeps running
        assert pm.updater.shutdowns == 0 and pm.reinject
    else:
        with pytest.raises(SystemExit): # Linux behaviour unchanged: stop the service and give up
            asyncio.run(pm.handle_crash())
        assert pm.updater.shutdowns == 1
    assert pm.webhelper_crash_count == 5


@pytest.mark.parametrize("windows", [True, False])
def test_fallback_injection_after_quick_crashes(main_mod: Any, monkeypatch: pytest.MonkeyPatch, windows: bool):
    monkeypatch.setattr(main_mod, "ON_WINDOWS", windows)
    monkeypatch.setattr(main_mod, "ON_LINUX", False)
    pm = _manager(main_mod, 3)
    tab = FakeTab()
    asyncio.run(pm.inject_javascript(tab, True))
    assert len(tab.scripts) == 1 and "fallback.js" in tab.scripts[0]
    if windows:
        assert pm.updater.shutdowns == 0 and pm.reinject
    else:
        assert pm.updater.shutdowns == 1 and not pm.reinject


@pytest.mark.skipif(sys.platform != "win32", reason="Windows exit codes")
def test_explicit_shutdown_still_stops_on_windows(main_mod: Any):
    from decky_loader.localplatform import localplatformwin as lp
    from aiohttp.web_runner import GracefulExit
    with pytest.raises(GracefulExit):
        asyncio.run(main_mod.Updater.do_shutdown(object())) # type: ignore
    assert lp.requested_exit_code() == 43
