import asyncio
import logging
import os
from typing import Any
import pytest
from aiohttp.web import Application, Response, get
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL


@pytest.fixture
def main_mod(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    # Importing the loader module resolves (and creates) the homebrew paths, so point them at tmp_path
    monkeypatch.setenv("PRIVILEGED_PATH", str(tmp_path / "priv"))
    monkeypatch.setenv("UNPRIVILEGED_PATH", str(tmp_path / "unpriv"))
    monkeypatch.setenv("CHOWN_PLUGIN_PATH", "0")
    from decky_loader import main
    return main


class FakeSettings:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def getSetting(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def setSetting(self, key: str, value: Any) -> None:
        self.values[key] = value


class FakeBrowser:
    def __init__(self) -> None:
        self.recovered = 0

    def recover_install_workspaces(self) -> None:
        self.recovered += 1


class FakeLoader:
    def __init__(self) -> None:
        self.imports = 0
        self.plugins: dict[str, Any] = {"A": object()}

    async def import_plugins(self) -> None:
        self.imports += 1


def _manager(main: Any, flag_dir: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(main, "get_privileged_path", lambda: str(flag_dir))
    pm = main.PluginManager.__new__(main.PluginManager)
    pm.settings = FakeSettings()
    pm.plugin_browser = FakeBrowser()
    pm.plugin_loader = FakeLoader()
    return pm


def test_flag_path_is_next_to_the_settings_dir(main_mod: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_mod, "get_privileged_path", lambda: str(tmp_path))
    assert main_mod.safe_mode_flag_path() == os.path.join(str(tmp_path), "safe-mode")


def test_load_plugins_skips_when_flag_present(main_mod: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    (tmp_path / "safe-mode").write_text("") # content is ignored
    pm = _manager(main_mod, tmp_path, monkeypatch)
    with caplog.at_level(logging.INFO, logger="Main"):
        asyncio.run(pm.load_plugins())
    assert pm.plugin_loader.imports == 0
    assert "Safe mode: plugins not loaded" in caplog.text
    assert pm.settings.values == {} # pluginOrder is not rewritten from an empty plugin list


def test_load_plugins_normal_without_flag(main_mod: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    pm = _manager(main_mod, tmp_path, monkeypatch)
    with caplog.at_level(logging.INFO, logger="Main"):
        asyncio.run(pm.load_plugins())
    assert pm.plugin_loader.imports == 1
    assert pm.plugin_browser.recovered == 1
    assert "Safe mode" not in caplog.text
    assert pm.settings.values["pluginOrder"] == ["A"]


def test_flag_must_be_a_file(main_mod: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main_mod, "get_privileged_path", lambda: str(tmp_path))
    (tmp_path / "safe-mode").mkdir()
    assert main_mod.safe_mode_enabled() is False


def test_status_route_reports_flag(main_mod: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch):
    from decky_loader.helpers import csrf_middleware
    pm = _manager(main_mod, tmp_path, monkeypatch)

    async def secret(_request: Any) -> Response:
        return Response(text="secret")

    async def go() -> None:
        app = Application(middlewares=[csrf_middleware])
        app.add_routes(pm.own_routes() + [get("/methods/secret", secret)])
        async with TestClient(TestServer(app)) as client:
            res = await client.get("/status/safe-mode") # no token: public
            assert res.status == 200
            assert res.headers["Cache-Control"] == "no-store"
            assert await res.json() == {"safe_mode": False}
            (tmp_path / "safe-mode").write_text("1")
            assert await (await client.get("/status/safe-mode")).json() == {"safe_mode": True}
            (tmp_path / "safe-mode").unlink()
            assert await (await client.get("/status/safe-mode")).json() == {"safe_mode": False}
            assert (await client.post("/status/safe-mode")).status == 405 # read-only
            assert (await client.get("/methods/secret")).status == 403

    asyncio.run(go())


@pytest.mark.parametrize("raw", ["/status%2Fsafe-mode", "/status%2fsafe-mode", "/status/safe%2Dmode", "/status/%73afe-mode",
                                 "/status/safe-mode%2F", "/STATUS/safe-mode", "/status/safe-mode/../x"])
def test_safe_mode_exemption_needs_the_literal_path(main_mod: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, raw: str):
    from decky_loader.helpers import csrf_middleware
    pm = _manager(main_mod, tmp_path, monkeypatch)

    async def go() -> None:
        app = Application(middlewares=[csrf_middleware])
        app.add_routes(pm.own_routes())
        async with TestClient(TestServer(app)) as client:
            res = await client.get(URL(raw, encoded=True))
            assert res.status in (403, 404), raw
            assert res.status != 200

    asyncio.run(go())
