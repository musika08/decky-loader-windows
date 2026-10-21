import asyncio, io, os, sys, zipfile
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


def test_windows_update_leaves_new_file(tmp_path: Any, monkeypatch: Any):
    monkeypatch.chdir(tmp_path)
    running = tmp_path / "PluginLoader.exe"
    running.write_bytes(b"old")
    _run(PAYLOAD, is_zip=False)
    assert (tmp_path / "PluginLoader.exe.new").read_bytes() == PAYLOAD
    assert running.read_bytes() == b"old"


def test_windows_update_extracts_zip_member(tmp_path: Any, monkeypatch: Any):
    monkeypatch.chdir(tmp_path)
    running = tmp_path / "PluginLoader.exe"
    running.write_bytes(b"old")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("PluginLoader.exe", PAYLOAD)
    _run(buf.getvalue(), is_zip=True)
    assert (tmp_path / "PluginLoader.exe.new").read_bytes() == PAYLOAD
    assert not os.path.exists(tmp_path / "PluginLoader.exe.new.zip")
    assert running.read_bytes() == b"old"


def test_is_supported_only_when_supervised(monkeypatch: Any):
    monkeypatch.delenv("DECKY_SUPERVISED", raising=False)
    assert _updater().isSupported is False
    monkeypatch.setenv("DECKY_SUPERVISED", "1")
    assert _updater().isSupported is True
