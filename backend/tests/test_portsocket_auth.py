import asyncio, socket
import pytest

from decky_loader.localplatform import localsocket
from decky_loader.localplatform.localsocket import PortSocket


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_default_token_is_random_hex():
    a, b = PortSocket(), PortSocket()
    assert len(a.token) == 32 and a.token != b.token
    int(a.token, 16)


def test_correct_token_roundtrip():
    async def run() -> str | None:
        port = _free_port()
        server = PortSocket(port, "abc")

        async def echo(line: str) -> str:
            return line

        await server.setup_server(echo)
        client = PortSocket(port, "abc")
        try:
            await client.write_single_line('{"hello": 1}')
            return await asyncio.wait_for(client.read_single_line(), 5)
        finally:
            # The server loop busy-spins on EOF while active (pre-existing), so stop it before the client hangs up
            server.active = False
            await client.close_socket_connection()
            await asyncio.sleep(0.1)
            if server.server_writer is not None:
                server.server_writer.close() # the base handler never closes its side
            await asyncio.wait_for(server.close_socket_connection(), 5)

    assert asyncio.run(run()) == '{"hello": 1}\n'


def _rejected(payload: bytes) -> tuple[bytes, list[str]]:
    async def run() -> tuple[bytes, list[str]]:
        port = _free_port()
        server = PortSocket(port, "abc")
        calls: list[str] = []

        async def handler(line: str) -> str:
            calls.append(line)
            return line

        await server.setup_server(handler)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(payload)
            await writer.drain()
            data = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            await asyncio.sleep(0.1)
            return data, calls
        finally:
            await server.close_socket_connection()

    return asyncio.run(run())


def test_wrong_token_is_rejected():
    data, calls = _rejected(b'nope\n{"id": "1", "method": "x", "args": []}\n')
    assert data == b""
    assert calls == []


def test_token_prefix_is_rejected():
    data, calls = _rejected(b'abcd\n{"id": "1"}\n')
    assert data == b"" and calls == []


def test_silent_client_times_out(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(localsocket, "AUTH_TIMEOUT", 0.2)
    data, calls = _rejected(b"")
    assert data == b"" and calls == []
