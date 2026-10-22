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


class LoadedTab(FakeTab):
    '''A GamepadUI tab that still has the frontend of an earlier loader run in it.'''
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def has_global_var(self, *_a: Any) -> bool:
        return True

    async def close_websocket(self) -> None:
        self.closed = True


def _reinject(main_mod: Any, monkeypatch: pytest.MonkeyPatch, restarted: bool) -> tuple[Any, LoadedTab, list[int]]:
    monkeypatch.setattr(main_mod, "ON_WINDOWS", True)
    monkeypatch.setattr(main_mod, "ON_LINUX", False)
    calls: list[int] = []

    async def restart() -> bool:
        calls.append(1)
        return restarted
    monkeypatch.setattr(main_mod, "restart_webhelper", restart)
    pm = _manager(main_mod, 0)
    pm.frontend_loaded_since = 1.0
    pm.js_ctx_tab = tab = LoadedTab()
    asyncio.run(pm.inject_javascript(tab, True))
    return pm, tab, calls


def test_windows_reinject_restarts_webhelper(main_mod: Any, monkeypatch: pytest.MonkeyPatch):
    # Like Linux: a stale frontend is cleared by restarting the webhelper, then the next tab is caught by the main loop
    pm, tab, calls = _reinject(main_mod, monkeypatch, restarted=True)
    assert calls == [1]
    assert tab.closed and pm.js_ctx_tab is None and pm.frontend_loaded_since is None
    assert tab.scripts == []


def test_windows_reinject_falls_back_when_webhelper_cannot_be_restarted(main_mod: Any, monkeypatch: pytest.MonkeyPatch):
    # Keep the tab and inject as before: the inject script restarts the JS context when deckyHasLoaded is set
    pm, tab, calls = _reinject(main_mod, monkeypatch, restarted=False)
    assert calls == [1]
    assert not tab.closed and pm.js_ctx_tab is tab
    assert len(tab.scripts) == 1 and "RestartJSContext" in tab.scripts[0]


class FakeSettings:
    def __init__(self, **values: Any) -> None:
        self.values: dict[str, Any] = dict(values)

    def getSetting(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def setSetting(self, key: str, value: Any) -> Any:
        self.values[key] = value
        return value


def test_cef_forward_migration_fresh_install(main_mod: Any):
    s = FakeSettings()
    main_mod.reset_windows_cef_forward_once(s)
    assert s.values == {"cef_forward_windows_reviewed": True}


def test_cef_forward_migration_resets_a_toggle_saved_while_it_was_a_no_op(main_mod: Any, caplog: pytest.LogCaptureFixture):
    s = FakeSettings(cef_forward=True)
    main_mod.reset_windows_cef_forward_once(s)
    assert s.values == {"cef_forward": False, "cef_forward_windows_reviewed": True}
    assert "network port" in caplog.text
    # Only once: turning it on afterwards sticks
    s.setSetting("cef_forward", True)
    main_mod.reset_windows_cef_forward_once(s)
    assert s.values["cef_forward"] is True


def test_cef_forward_migration_already_reviewed(main_mod: Any):
    s = FakeSettings(cef_forward=True, cef_forward_windows_reviewed=True)
    main_mod.reset_windows_cef_forward_once(s)
    assert s.values == {"cef_forward": True, "cef_forward_windows_reviewed": True}


@pytest.mark.parametrize("windows", [True, False])
def test_startup_migrates_before_applying_the_setting(main_mod: Any, monkeypatch: pytest.MonkeyPatch, windows: bool):
    monkeypatch.setattr(main_mod, "ON_WINDOWS", windows)
    calls: list[str] = []
    async def start(unit: str) -> bool:
        calls.append("start")
        return True
    async def stop(unit: str) -> bool:
        calls.append("stop")
        return True
    monkeypatch.setattr(main_mod, "service_start", start)
    monkeypatch.setattr(main_mod, "service_stop", stop)
    pm = main_mod.PluginManager.__new__(main_mod.PluginManager)
    pm.settings = FakeSettings(cef_forward=True)
    asyncio.run(pm.apply_cef_forward_setting())
    # Windows: a toggle saved while it did nothing is reset, so no port is opened. Linux unchanged.
    assert calls == (["stop"] if windows else ["start"])
    assert ("cef_forward_windows_reviewed" in pm.settings.values) is windows
