import asyncio, socket, threading
from typing import Any, Callable, Coroutine
import pytest

from decky_loader.localplatform import localsocket
from decky_loader.localplatform.localsocket import PortSocket

CALL = b'{"id": "1", "method": "x", "args": []}\n'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(coro: Callable[[], Coroutine[Any, Any, Any]], timeout: float = 30) -> Any:
    '''asyncio.run in a thread, so a handler that spins without yielding fails the test instead of hanging it.'''
    result: list[Any] = []
    error: list[BaseException] = []

    def target() -> None:
        try:
            result.append(asyncio.run(coro()))
        except BaseException as e:
            error.append(e)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "event loop did not finish (busy loop?)"
    if error:
        raise error[0]
    return result[0]


@pytest.fixture
def fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(localsocket, "CONNECT_RETRIES", 2)
    monkeypatch.setattr(localsocket, "CONNECT_RETRY_DELAY", 0.05)
    monkeypatch.setattr(localsocket, "AUTH_TIMEOUT", 1.0)


def test_default_token_is_random_hex():
    a, b = PortSocket(), PortSocket()
    assert len(a.token) == 32 and a.token != b.token
    int(a.token, 16)


async def _close_server(server: PortSocket) -> None:
    await asyncio.wait_for(server.close_socket_connection(), 5)


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
            await client.close_socket_connection()
            await _close_server(server)

    assert _run(run) == '{"hello": 1}\n'


def test_server_loop_exits_on_client_disconnect():
    async def run() -> list[str]:
        port = _free_port()
        server = PortSocket(port, "abc")
        calls: list[str] = []

        async def echo(line: str) -> str:
            calls.append(line)
            return line

        await server.setup_server(echo)
        client = PortSocket(port, "abc")
        await client.write_single_line('{"a": 1}')
        assert await asyncio.wait_for(client.read_single_line(), 5) == '{"a": 1}\n'
        assert client.writer is not None
        client.writer.close()
        await asyncio.sleep(0.3)
        # The handler closed its side once it saw EOF, so the server can shut down without waiting on it
        await _close_server(server)
        return calls

    assert _run(run, timeout=15) == ['{"a": 1}\n']


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
            data = b""
            try:
                writer.write(payload)
                await writer.drain()
                data = await asyncio.wait_for(reader.read(), 5)
            except ConnectionError:
                pass
            writer.close()
            await asyncio.sleep(0.1)
            return data, calls
        finally:
            await _close_server(server)

    return _run(run)


def _only_nonce(data: bytes) -> bool:
    return data == b"" or (len(data) == 33 and data.endswith(b"\n"))


def test_wrong_proof_is_rejected():
    data, calls = _rejected(b"nope\n" + CALL)
    assert _only_nonce(data)
    assert calls == []


def test_raw_token_is_not_a_valid_proof():
    data, calls = _rejected(b"abc\n" + CALL)
    assert _only_nonce(data) and calls == []


def test_silent_client_times_out(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(localsocket, "AUTH_TIMEOUT", 0.2)
    data, calls = _rejected(b"")
    assert _only_nonce(data) and calls == []


def test_replayed_client_proof_is_rejected(fast_retry: None):
    async def run() -> tuple[bytes, list[str]]:
        # 1. A fake server captures a genuine client's handshake line
        captured: list[bytes] = []

        async def fake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(b"0" * 32 + b"\n")
            await writer.drain()
            captured.append(await reader.readline())
            writer.close()

        fake_port = _free_port()
        fake_server = await asyncio.start_server(fake, "127.0.0.1", fake_port)
        client = PortSocket(fake_port, "abc")
        await client.get_socket_connection()
        fake_server.close()

        # 2. Replaying it against a real server (fresh nonce) fails
        port = _free_port()
        server = PortSocket(port, "abc")
        calls: list[str] = []

        async def handler(line: str) -> None:
            calls.append(line)

        await server.setup_server(handler)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        data = b""
        try:
            data = await reader.readline()
            writer.write(captured[0] + CALL)
            await writer.drain()
            data = await asyncio.wait_for(reader.read(), 5)
        except ConnectionError:
            pass
        writer.close()
        await asyncio.sleep(0.1)
        await _close_server(server)
        return captured[0], calls

    line, calls = _run(run)
    assert line.count(b" ") == 1 and line.endswith(b"\n")
    assert calls == []


def test_client_rejects_server_without_token(fast_retry: None):
    async def run() -> tuple[list[bytes], bool]:
        after_proof: list[bytes] = []

        async def fake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            # Squatter: sends a nonce, reads the client's proof, answers with a made-up proof
            writer.write(b"1" * 32 + b"\n")
            await writer.drain()
            await reader.readline()
            writer.write(b"f" * 64 + b"\n")
            await writer.drain()
            try:
                after_proof.append(await asyncio.wait_for(reader.read(), 2))
            except Exception:
                pass
            writer.close()

        port = _free_port()
        fake_server = await asyncio.start_server(fake, "127.0.0.1", port)
        client = PortSocket(port, "abc")
        await client.write_single_line(CALL.decode())
        connected = client.reader is not None
        await asyncio.sleep(0.3)
        fake_server.close()
        return after_proof, connected

    after_proof, connected = _run(run)
    assert not connected
    assert all(CALL not in b and b == b"" for b in after_proof)


def test_token_never_on_the_wire():
    token = "s3cret-token-value"

    async def run() -> tuple[bytes, str | None]:
        wire = bytearray()
        port = _free_port()
        server = PortSocket(port, token)

        async def echo(line: str) -> str:
            return line

        await server.setup_server(echo)

        async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            while data := await src.read(65536):
                wire.extend(data)
                dst.write(data)
                await dst.drain()
            dst.close()

        async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            up_r, up_w = await asyncio.open_connection("127.0.0.1", port)
            await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer), return_exceptions=True)

        proxy_port = _free_port()
        proxy_server = await asyncio.start_server(proxy, "127.0.0.1", proxy_port)
        client = PortSocket(proxy_port, token)
        try:
            await client.write_single_line('{"hello": 1}')
            res = await asyncio.wait_for(client.read_single_line(), 5)
        finally:
            await client.close_socket_connection()
            await asyncio.sleep(0.2)
            proxy_server.close()
            await _close_server(server)
        return bytes(wire), res

    wire, res = _run(run)
    assert res == '{"hello": 1}\n'
    assert b'{"hello": 1}' in wire # the capture really saw the session
    assert token.encode() not in wire


@pytest.mark.skipif(not hasattr(socket, "SO_EXCLUSIVEADDRUSE"), reason="Windows-only socket option")
def test_server_port_is_exclusive():
    async def run() -> bool:
        port = _free_port()
        server = PortSocket(port, "abc")

        async def handler(line: str) -> None:
            pass

        await server.setup_server(handler)
        try:
            assert server.socket is not None
            listening = server.socket.sockets[0]
            assert listening.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) == 1
            with socket.socket() as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind(("127.0.0.1", port))
                    return False
                except OSError:
                    return True
        finally:
            await _close_server(server)

    assert _run(run)


def test_cancellation_while_connecting_is_not_swallowed(monkeypatch: pytest.MonkeyPatch):
    attempts: list[int] = []

    async def cancelled(*_a: Any, **_k: Any) -> Any:
        attempts.append(1)
        raise asyncio.CancelledError()

    monkeypatch.setattr(localsocket.asyncio, "open_connection", cancelled)
    monkeypatch.setattr(localsocket, "CONNECT_RETRY_DELAY", 0.01)

    async def go() -> None:
        with pytest.raises(asyncio.CancelledError):
            await PortSocket(_free_port(), "abc")._open_socket_if_not_exists() # pyright: ignore [reportPrivateUsage]

    _run(go)
    assert attempts == [1] # no retry loop after a cancellation


def test_connection_errors_are_still_retried(fast_retry: None, monkeypatch: pytest.MonkeyPatch):
    attempts: list[int] = []

    async def refused(*_a: Any, **_k: Any) -> Any:
        attempts.append(1)
        raise ConnectionRefusedError()

    monkeypatch.setattr(localsocket.asyncio, "open_connection", refused)

    async def go() -> bool:
        return await PortSocket(_free_port(), "abc")._open_socket_if_not_exists() # pyright: ignore [reportPrivateUsage]

    assert _run(go) is False
    assert attempts == [1, 1]
