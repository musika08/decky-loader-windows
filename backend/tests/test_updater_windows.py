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
    assert _updater().isSupported is False
    monkeypatch.setenv("DECKY_SUPERVISED", "1")
    assert _updater().isSupported is True


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
