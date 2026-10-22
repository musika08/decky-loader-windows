import asyncio, sys
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")

# Tests listen on 127.0.0.1 with OS-assigned ports only: never the real 0.0.0.0:8081, which could raise a firewall prompt


async def _echo_target() -> tuple[asyncio.AbstractServer, int]:
    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while data := await reader.read(1024):
            writer.write(b"cef:" + data)
            await writer.drain()
        writer.close()
    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def _forward(target_port: int) -> Any:
    from decky_loader.localplatform.winportforward import PortForward
    return PortForward("127.0.0.1", 0, "127.0.0.1", target_port)


def _port(fwd: Any) -> int:
    return fwd.server.sockets[0].getsockname()[1]


def test_defaults_match_linux_portforward():
    from decky_loader import helpers
    from decky_loader.localplatform import winportforward as w
    assert w.UNIT == helpers.REMOTE_DEBUGGER_UNIT
    assert (w.LISTEN_HOST, w.LISTEN_PORT, w.TARGET_HOST, w.TARGET_PORT) == ("0.0.0.0", 8081, "127.0.0.1", 8080)
    assert w.forward.active is False  # off by default


def test_forwards_both_ways_and_stops():
    async def go() -> None:
        target, target_port = await _echo_target()
        fwd = _forward(target_port)
        try:
            assert await fwd.start() is True
            assert fwd.active
            assert await fwd.start() is True  # idempotent
            reader, writer = await asyncio.open_connection("127.0.0.1", _port(fwd))
            writer.write(b"hello")
            await writer.drain()
            assert await asyncio.wait_for(reader.read(1024), 5) == b"cef:hello"
            # An open DevTools session must not keep stop() waiting
            await asyncio.wait_for(fwd.stop(), 5)
            assert not fwd.active
            assert await asyncio.wait_for(reader.read(1024), 5) == b""
            writer.close()
        finally:
            await fwd.stop()
            target.close()
            await target.wait_closed()
    asyncio.run(go())


def test_unreachable_debugger_just_closes_the_client():
    async def go() -> None:
        target, target_port = await _echo_target()
        target.close()
        await target.wait_closed()  # nothing listens on target_port any more
        fwd = _forward(target_port)
        await fwd.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", _port(fwd))
            assert await asyncio.wait_for(reader.read(1024), 5) == b""
            writer.close()
        finally:
            await fwd.stop()
    asyncio.run(go())


def test_port_in_use_returns_false():
    async def go() -> None:
        blocker = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)  # pyright: ignore [reportUnknownLambdaType, reportUnknownArgumentType]
        port = blocker.sockets[0].getsockname()[1]
        from decky_loader.localplatform.winportforward import PortForward
        fwd = PortForward("127.0.0.1", port, "127.0.0.1", 1)
        try:
            assert await fwd.start() is False
            assert not fwd.active
        finally:
            blocker.close()
            await blocker.wait_closed()
    asyncio.run(go())


def test_service_calls_drive_the_forward(monkeypatch: pytest.MonkeyPatch):
    from decky_loader import helpers
    from decky_loader.localplatform import localplatformwin as lp, winportforward
    async def go() -> None:
        target, target_port = await _echo_target()
        fwd = _forward(target_port)
        monkeypatch.setattr(winportforward, "forward", fwd)
        try:
            assert await lp.service_active(helpers.REMOTE_DEBUGGER_UNIT) is False
            assert await lp.service_start(helpers.REMOTE_DEBUGGER_UNIT) is True
            assert await lp.service_active(helpers.REMOTE_DEBUGGER_UNIT) is True
            assert await lp.service_stop(helpers.REMOTE_DEBUGGER_UNIT) is True
            assert await lp.service_active(helpers.REMOTE_DEBUGGER_UNIT) is False
        finally:
            await fwd.stop()
            target.close()
            await target.wait_closed()
    asyncio.run(go())


def test_sshd_service_calls_use_the_windows_service(monkeypatch: pytest.MonkeyPatch):
    from decky_loader import helpers
    from decky_loader.localplatform import localplatformwin as lp, winsshd
    calls: list[bool] = []
    async def fake(running: bool) -> str | None:
        calls.append(running)
        return None if running else "nope"
    monkeypatch.setattr(winsshd, "set_sshd_running", fake)
    assert winsshd.UNIT == helpers.SSHD_UNIT
    assert asyncio.run(lp.service_start(helpers.SSHD_UNIT)) is True
    assert asyncio.run(lp.service_stop(helpers.SSHD_UNIT)) is False
    assert calls == [True, False]


def test_start_warns_with_the_listening_address(caplog: pytest.LogCaptureFixture):
    import logging
    async def go() -> None:
        fwd = _forward(1)
        with caplog.at_level(logging.WARNING, logger="winportforward"):
            assert await fwd.start() is True
        try:
            assert "listening on 127.0.0.1:0" in caplog.text
            assert all(r.levelno == logging.WARNING for r in caplog.records if "listening on" in r.getMessage())
        finally:
            await fwd.stop()
    asyncio.run(go())
