import asyncio
from typing import Any, AsyncIterator
import pytest
from aiohttp.web import Application, Response, get
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture
def main_mod(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    # Importing the loader module resolves (and creates) the homebrew paths, so point them at tmp_path
    monkeypatch.setenv("PRIVILEGED_PATH", str(tmp_path / "priv"))
    monkeypatch.setenv("UNPRIVILEGED_PATH", str(tmp_path / "unpriv"))
    monkeypatch.setenv("CHOWN_PLUGIN_PATH", "0")
    from decky_loader import main
    return main


class FakeUpdater:
    async def do_shutdown(self) -> None:
        pass


def _manager(main: Any) -> Any:
    pm = main.PluginManager.__new__(main.PluginManager)
    pm.reinject = True
    pm.webhelper_crash_count = 0
    pm.last_webhelper_exit = 0
    pm.frontend_loaded_since = None
    pm.js_ctx_tab = None
    pm.updater = FakeUpdater()
    return pm


class FakeTab:
    '''A GamepadUI tab whose page events are `events`; the event stream ending is CEF disconnecting.'''
    def __init__(self, pm: Any, events: list[dict[str, Any]], has_loaded: bool = False) -> None:
        self.pm = pm
        self.events = events
        self.has_loaded = has_loaded
        self.scripts: list[str] = []
        self.loaded_during: list[bool] = []

    async def open_websocket(self) -> None:
        pass

    async def close_websocket(self) -> None:
        pass

    async def enable(self) -> None:
        pass

    async def has_global_var(self, *_a: Any) -> bool:
        return self.has_loaded

    async def evaluate_js(self, script: str, *_a: Any) -> None:
        self.scripts.append(script)

    async def listen_for_message(self) -> AsyncIterator[dict[str, Any]]:
        self.loaded_during.append(self.pm.frontend_loaded_since is not None)
        for e in self.events:
            yield e
            self.loaded_during.append(self.pm.frontend_loaded_since is not None)


def _status(pm: Any) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        return __import__("json").loads((await pm.get_frontend_status(None)).text)
    return asyncio.run(go())


@pytest.mark.parametrize("windows,interval", [(True, 1), (False, 5)])
def test_gamepadui_poll_interval(main_mod: Any, monkeypatch: pytest.MonkeyPatch, windows: bool, interval: float):
    monkeypatch.setattr(main_mod, "ON_WINDOWS", windows)
    pm = _manager(main_mod)
    sleeps: list[float] = []

    async def no_tab() -> Any:
        raise ValueError("GamepadUI Tab not found")

    async def sleep(s: float) -> None:
        sleeps.append(s)
        if len(sleeps) == 3:
            pm.reinject = False

    monkeypatch.setattr(main_mod, "get_gamepadui_tab", no_tab)
    monkeypatch.setattr(main_mod, "sleep", sleep)
    asyncio.run(pm.loader_reinjector())
    assert sleeps == [interval] * 3


def test_frontend_status_follows_injection_and_disconnect(main_mod: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_mod, "ON_LINUX", False)
    pm = _manager(main_mod)
    assert _status(pm) == {"loaded": False, "since": None}
    tab = FakeTab(pm, [{"method": "Page.loadEventFired"}])
    tabs = [tab]

    async def next_tab() -> Any:
        if tabs:
            return tabs.pop()
        pm.reinject = False # stop after the disconnect
        raise ValueError("GamepadUI Tab not found")

    async def no_sleep(_s: float) -> None:
        pass

    monkeypatch.setattr(main_mod, "get_gamepadui_tab", next_tab)
    monkeypatch.setattr(main_mod, "sleep", no_sleep)
    before = main_mod.time()
    asyncio.run(pm.loader_reinjector())
    assert len(tab.scripts) == 1
    assert tab.loaded_during == [True, True] # injected, then loaded while the tab was connected
    assert _status(pm) == {"loaded": False, "since": None} # and no longer once CEF disconnected
    pm.set_frontend_loaded(True)
    status = _status(pm)
    assert status["loaded"] is True and before <= status["since"] <= main_mod.time()


def test_frontend_status_clears_on_reload_until_reinjected(main_mod: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_mod, "ON_LINUX", False)
    pm = _manager(main_mod)
    seen: list[bool] = []

    async def inject(tab: Any, first: bool = False, request: Any = None) -> None:
        seen.append(pm.frontend_loaded_since is not None)
        pm.set_frontend_loaded(True)

    tab = FakeTab(pm, [{"method": "Page.domContentEventFired"}, {"method": "Inspector.detached"}])
    tabs = [tab]

    async def next_tab() -> Any:
        if tabs:
            return tabs.pop()
        pm.reinject = False
        raise ValueError("GamepadUI Tab not found")

    async def no_sleep(_s: float) -> None:
        pass

    monkeypatch.setattr(pm, "inject_javascript", inject)
    monkeypatch.setattr(main_mod, "get_gamepadui_tab", next_tab)
    monkeypatch.setattr(main_mod, "sleep", no_sleep)
    asyncio.run(pm.loader_reinjector())
    # First injection, then the reload: cleared before injecting again
    assert seen == [False, False]
    assert _status(pm)["loaded"] is False # detached


def test_failed_injection_is_not_loaded(main_mod: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_mod, "ON_LINUX", False)
    pm = _manager(main_mod)

    class BrokenTab(FakeTab):
        async def evaluate_js(self, script: str, *_a: Any) -> None:
            raise RuntimeError("Websocket not opened")

    asyncio.run(pm.inject_javascript(BrokenTab(pm, [])))
    assert _status(pm) == {"loaded": False, "since": None}


def test_status_route_needs_no_token_but_others_still_do(main_mod: Any):
    from decky_loader.helpers import csrf_middleware, get_csrf_token
    pm = _manager(main_mod)
    pm.set_frontend_loaded(True)

    async def secret(_request: Any) -> Response:
        return Response(text="secret")

    async def go() -> None:
        app = Application(middlewares=[csrf_middleware])
        app.add_routes(pm.own_routes() + [get("/methods/secret", secret), get("/status/other", secret)])
        async with TestClient(TestServer(app)) as client:
            res = await client.get("/status/frontend")
            assert res.status == 200
            assert res.headers["Cache-Control"] == "no-store"
            body = await res.json()
            assert set(body) == {"loaded", "since"} and body["loaded"] is True and isinstance(body["since"], float)
            assert (await client.get("/status/frontend?x=1")).status == 200
            assert (await client.post("/status/frontend")).status == 405 # read-only
            for url in ("/methods/secret", "/status/other", "/status/frontend/../other"):
                assert (await client.get(url)).status == 403, url
            assert (await client.get("/methods/secret", headers={"X-Decky-Auth": get_csrf_token()})).status == 200

    asyncio.run(go())
