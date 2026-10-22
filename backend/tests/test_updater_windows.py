import asyncio, io, os, sys, zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")

PAYLOAD = b"MZ-new-loader-binary" * 100


async def _serve(body: bytes):
    from aiohttp import web

    async def handler(_req: Any) -> Any:
        return web.Response(body=body)

    app = web.Application()
    app.router.add_get("/PluginLoader.exe", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # pyright: ignore
    return runner, f"http://127.0.0.1:{port}/PluginLoader.exe"


def _updater():
    from decky_loader.updater import Updater

    ctx = MagicMock()
    ctx.loop.create_task = lambda coro: coro.close()  # don't run background tasks such as version_reloader
    u = Updater(ctx)

    async def noop() -> None:
        pass

    u.do_restart = noop  # type: ignore
    return u


def _download(url: str, is_zip: bool):
    async def go():
        u = _updater()
        u.context.ws.emit = lambda *a, **k: asyncio.sleep(0)  # type: ignore
        u.context.loop = asyncio.get_running_loop()
        await u.download_decky_binary(url, "v1.2.3", is_zip=is_zip)

    return go


def _run(body: bytes, is_zip: bool):
    async def go():
        runner, url = await _serve(body)
        try:
            await _download(url, is_zip)()
        finally:
            await runner.cleanup()

    asyncio.run(go())


def _portable(tmp_path: Any, monkeypatch: Any) -> Any:
    # A portable copy keeps its release name; the update must land next to sys.executable whatever the cwd
    exe_dir = tmp_path / "portable"
    exe_dir.mkdir()
    running = exe_dir / "PluginLoader-win.exe"
    running.write_bytes(b"old")
    monkeypatch.setattr(sys, "executable", str(running))
    monkeypatch.chdir(tmp_path)
    return running


def test_windows_update_leaves_new_file(tmp_path: Any, monkeypatch: Any):
    running = _portable(tmp_path, monkeypatch)
    _run(PAYLOAD, is_zip=False)
    assert (running.parent / "PluginLoader-win.exe.new").read_bytes() == PAYLOAD
    assert running.read_bytes() == b"old"
    assert not (tmp_path / "PluginLoader.exe.new").exists()


def test_new_file_matches_what_the_supervisor_looks_for(tmp_path: Any, monkeypatch: Any):
    from decky_loader.localplatform import winsupervisor
    running = _portable(tmp_path, monkeypatch)
    _run(PAYLOAD, is_zip=False)
    assert winsupervisor.pending_update_path(str(running)) == str(running) + ".new"


def test_windows_update_extracts_zip_member(tmp_path: Any, monkeypatch: Any):
    running = _portable(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("PluginLoader.exe", PAYLOAD)
    _run(buf.getvalue(), is_zip=True)
    assert (running.parent / "PluginLoader-win.exe.new").read_bytes() == PAYLOAD
    assert sorted(os.listdir(running.parent)) == ["PluginLoader-win.exe", "PluginLoader-win.exe.new"]  # no leftover temp files
    assert running.read_bytes() == b"old"


def test_is_supported_only_when_supervised(monkeypatch: Any):
    monkeypatch.delenv("DECKY_SUPERVISED", raising=False)
    u = _updater()
    assert u.isSupported is False
    assert u.updaterAvailable is False
    assert "supervisor" in (u.updaterUnavailableReason or "")
    monkeypatch.setenv("DECKY_SUPERVISED", "1")
    u = _updater()
    assert u.isSupported is True
    assert u.updaterAvailable is True


def test_dropped_connection_leaves_no_new_file(tmp_path: Any, monkeypatch: Any):
    from aiohttp import web, ClientError

    running = _portable(tmp_path, monkeypatch)

    async def handler(req: Any) -> Any:
        resp = web.StreamResponse(headers={"Content-Length": str(len(PAYLOAD))})
        await resp.prepare(req)
        await resp.write(PAYLOAD[:100])
        req.transport.close()  # drop mid-body
        return resp

    async def go():
        app = web.Application()
        app.router.add_get("/PluginLoader.exe", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # pyright: ignore
        try:
            with pytest.raises(ClientError):
                await _download(f"http://127.0.0.1:{port}/PluginLoader.exe", False)()
        finally:
            await runner.cleanup()

    asyncio.run(go())
    assert not (running.parent / "PluginLoader-win.exe.new").exists()


@pytest.mark.parametrize("value,noconsole", [("0", False), ("1", True), ("", False)])
def test_noconsole_asset_only_when_flag_is_1(monkeypatch: Any, value: str, noconsole: bool):
    monkeypatch.setenv("DECKY_NOCONSOLE", value)
    assert _updater().get_remote_binary_name().endswith("-noconsole.exe") is noconsole


def test_spec_parses_noconsole_as_1_only():
    spec = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pyinstaller.spec")
    with open(spec, encoding="utf-8") as f:
        text = f.read()
    assert "noconsole = noconsole_str == '1'" in text
    assert "bool(noconsole_str)" not in text


# Installed mode (the Python runtime and source in Program Files) is updated by re-running the installer, never in-app

def _installed_layout(tmp_path: Path, monkeypatch: Any) -> Path:
    from decky_loader.localplatform import localplatformwin, wininstall
    app = tmp_path / "Program Files" / "Decky Loader"
    (app / "python").mkdir(parents=True)
    running = app / "python" / "pythonw.exe"
    running.write_bytes(b"old")
    monkeypatch.setattr(sys, "executable", str(running))
    monkeypatch.setattr(localplatformwin, "running_main_script", lambda: str(app / "app" / "main.py"))
    monkeypatch.setattr(wininstall, "known_program_files", lambda: str(tmp_path / "Program Files"))
    monkeypatch.setattr(localplatformwin, "known_program_data", lambda: str(tmp_path / "ProgramData"))
    monkeypatch.chdir(tmp_path)
    return running


@pytest.mark.parametrize("supervised", ["1", None])
def test_installed_mode_is_supported_without_the_in_app_updater(tmp_path: Path, monkeypatch: Any, supervised: str | None):
    # No "Unsupported Installation" toast in installed mode; only the in-app updater is off, with what to do instead
    _installed_layout(tmp_path, monkeypatch)
    if supervised is None:
        monkeypatch.delenv("DECKY_SUPERVISED", raising=False)
    else:
        monkeypatch.setenv("DECKY_SUPERVISED", supervised)
    u = _updater()
    assert u.isSupported is True
    assert u.updaterAvailable is False
    info = asyncio.run(u.get_version_info())
    assert info["supported"] is True
    assert info["updater_available"] is False
    assert r"tools\update-decky.ps1" in str(info["updater_unavailable_reason"])
    assert r"python\python.exe app\main.py --install" in str(info["updater_unavailable_reason"])
    assert "PluginLoader.exe" not in str(info["updater_unavailable_reason"])


def test_not_supported_for_a_onedir_build(tmp_path: Any, monkeypatch: Any):
    running = _portable(tmp_path, monkeypatch)
    (running.parent / "_internal").mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(running.parent / "_internal"), raising=False)
    monkeypatch.setenv("DECKY_SUPERVISED", "1")
    u = _updater()
    assert u.isSupported is False
    assert u.updaterAvailable is False
    assert "onedir" in (u.updaterUnavailableReason or "")


def test_portable_onefile_still_supported(tmp_path: Any, monkeypatch: Any):
    running = _portable(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "Temp" / "_MEI123"), raising=False)
    monkeypatch.setenv("DECKY_SUPERVISED", "1")
    u = _updater()
    assert u.isSupported is True
    assert u.updaterAvailable is True
    assert u.updaterUnavailableReason is None
    _run(PAYLOAD, is_zip=False)
    assert (running.parent / "PluginLoader-win.exe.new").read_bytes() == PAYLOAD


def test_installed_mode_never_writes_a_new_file(tmp_path: Path, monkeypatch: Any):
    running = _installed_layout(tmp_path, monkeypatch)
    with pytest.raises(Exception, match="not supported in installed mode"):
        _run(PAYLOAD, is_zip=False)
    assert sorted(os.listdir(running.parent)) == ["pythonw.exe"]
    assert running.read_bytes() == b"old"
    assert not (tmp_path / ".loader.version").exists()
