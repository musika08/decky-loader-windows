import asyncio, hashlib, hmac, secrets, socket, sys, time
from typing import Any, Callable, Coroutine
import random

from .localplatform import ON_WINDOWS

BUFFER_LIMIT = 2 ** 20  # 1 MiB
AUTH_TIMEOUT = 5.0  # seconds each side of the PortSocket handshake waits for the other
NONCE_BYTES = 16  # sent as 32 hex chars
CONNECT_RETRIES = 10
CONNECT_RETRY_DELAY = 2.0

class UnixSocket:
    def __init__(self):
        '''
        on_new_message takes 1 string argument.
        It's return value gets used, if not None, to write data to the socket.
        Method should be async
        '''
        self.socket_addr = f"/tmp/plugin_socket_{time.time()}"
        self.on_new_message = None
        self.socket = None
        self.reader = None
        self.writer = None
        self.server_writer = None
        self.open_lock = asyncio.Lock()
        self.active = True

    async def setup_server(self, on_new_message: Callable[[str], Coroutine[Any, Any, Any]]):
        try:
            self.on_new_message = on_new_message
            self.socket = await asyncio.start_unix_server(self._listen_for_method_call, path=self.socket_addr, limit=BUFFER_LIMIT)
        except asyncio.CancelledError:
            await self.close_socket_connection()
            raise

    async def _open_socket_if_not_exists(self):
        if not self.reader:
            retries = 0
            while retries < 10:
                try:
                    self.reader, self.writer = await asyncio.open_unix_connection(self.socket_addr, limit=BUFFER_LIMIT)
                    return True
                except:
                    await asyncio.sleep(2)
                    retries += 1
            return False
        else:
            return True

    async def get_socket_connection(self):
        async with self.open_lock:
            if not await self._open_socket_if_not_exists():
                return None, None
            
            return self.reader, self.writer
    
    async def close_socket_connection(self):
        if self.writer != None:
            self.writer.close()

        self.reader = None

        if self.socket:
            self.socket.close()
            await self.socket.wait_closed()
        
        self.active = False

    async def read_single_line(self) -> str|None:
        reader, _ = await self.get_socket_connection()

        try:
            assert reader
        except AssertionError:
            return

        return await self._read_single_line(reader)

    async def write_single_line(self, message : str):
        _, writer = await self.get_socket_connection()

        try:
            assert writer
        except AssertionError:
            return

        await self._write_single_line(writer, message)

    async def _read_single_line(self, reader: asyncio.StreamReader) -> str|None:
        line = bytearray()
        while self.active:
            try:
                line.extend(await reader.readuntil())
            except asyncio.LimitOverrunError:
                line.extend(await reader.read(reader._limit)) # pyright: ignore [reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue]
                continue
            except asyncio.IncompleteReadError:
                return None
            except asyncio.CancelledError:
                raise
            else:
                break

        return line.decode("utf-8")
    
    async def _write_single_line(self, writer: asyncio.StreamWriter, message : str):
        if not message.endswith("\n"):
            message += "\n"

        writer.write(message.encode("utf-8"))
        await writer.drain()
    
    async def write_single_line_server(self, message: str):
        if self.server_writer is None:
            return
        await self._write_single_line(self.server_writer, message)

    async def _listen_for_method_call(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.server_writer = writer
        while self.active and self.on_new_message:

            def _(task: asyncio.Task[str|None]):
                res = task.result()
                if res is not None:
                    asyncio.create_task(self._write_single_line(writer, res))

            line = await self._read_single_line(reader)
            if line is None:
                break
            asyncio.create_task(self.on_new_message(line)).add_done_callback(_)
            
class PortSocket (UnixSocket):
    def __init__(self, port: int | None = None, token: str | None = None):
        '''
        on_new_message takes 1 string argument.
        It's return value gets used, if not None, to write data to the socket.
        Method should be async

        Any local process can reach a TCP port, and one could bind it before the real server does.
        So both ends prove they know `token` with a nonce challenge-response before any message flows,
        and the token itself never crosses the wire.
        '''
        super().__init__()
        self.host = "127.0.0.1"
        self.port = port if port is not None else random.sample(range(40000, 60000), 1)[0]
        self.token = token if token is not None else secrets.token_hex(16)

    def _mac(self, *parts: bytes) -> str:
        return hmac.new(self.token.encode("utf-8"), b"|".join(parts), hashlib.sha256).hexdigest()

    async def setup_server(self, on_new_message: Callable[[str], Coroutine[Any, Any, Any]]):
        try:
            self.on_new_message = on_new_message
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if sys.platform == "win32":
                    # Nobody else may bind this port while we hold it, not even with SO_REUSEADDR
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind((self.host, self.port))
                sock.listen()
                sock.setblocking(False)
            except BaseException:
                sock.close()
                raise
            self.socket = await asyncio.start_server(self._listen_for_method_call, sock=sock, limit=BUFFER_LIMIT)
        except asyncio.CancelledError:
            await self.close_socket_connection()
            raise

    async def _client_handshake(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        server_nonce = (await reader.readline()).rstrip(b"\n")
        if not _is_nonce(server_nonce):
            raise ConnectionError("bad server nonce")
        client_nonce = secrets.token_hex(NONCE_BYTES).encode("ascii")
        writer.write(client_nonce + b" " + self._mac(b"client", server_nonce, client_nonce).encode("ascii") + b"\n")
        await writer.drain()
        proof = (await reader.readline()).rstrip(b"\n")
        if not hmac.compare_digest(proof, self._mac(b"server", client_nonce, server_nonce).encode("ascii")):
            raise ConnectionError("server failed to prove it knows the token")

    async def _server_handshake(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        server_nonce = secrets.token_hex(NONCE_BYTES).encode("ascii")
        writer.write(server_nonce + b"\n")
        await writer.drain()
        parts = (await reader.readline()).rstrip(b"\n").split(b" ")
        if len(parts) != 2 or not _is_nonce(parts[0]):
            return False
        client_nonce, proof = parts
        if not hmac.compare_digest(proof, self._mac(b"client", server_nonce, client_nonce).encode("ascii")):
            return False
        writer.write(self._mac(b"server", client_nonce, server_nonce).encode("ascii") + b"\n")
        await writer.drain()
        return True

    async def _open_socket_if_not_exists(self):
        if not self.reader:
            retries = 0
            while retries < CONNECT_RETRIES:
                try:
                    reader, writer = await asyncio.open_connection(host=self.host, port=self.port, limit=BUFFER_LIMIT)
                    try:
                        await asyncio.wait_for(self._client_handshake(reader, writer), AUTH_TIMEOUT)
                    except BaseException:
                        writer.close()
                        raise
                    self.reader, self.writer = reader, writer
                    return True
                except Exception: # not a bare except: a cancellation must stop the retry loop, not be swallowed
                    await asyncio.sleep(CONNECT_RETRY_DELAY)
                    retries += 1
            return False
        else:
            return True

    async def _listen_for_method_call(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            try:
                ok = await asyncio.wait_for(self._server_handshake(reader, writer), AUTH_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except Exception: # timeout, oversized line, reset connection
                ok = False
            if not ok:
                return

            self.server_writer = writer

            def _(task: asyncio.Task[str|None]):
                res = task.result()
                if res is not None:
                    asyncio.create_task(self._write_single_line(writer, res))

            while self.active:
                handler = self.on_new_message
                if handler is None:
                    break
                line = await self._read_single_line(reader)
                if not line: # EOF: the client hung up. The base loop would spin here.
                    break
                asyncio.create_task(handler(line)).add_done_callback(_)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

def _is_nonce(b: bytes) -> bool:
    return len(b) == 2 * NONCE_BYTES and all(c in b"0123456789abcdef" for c in b)

if ON_WINDOWS:
    class LocalSocket (PortSocket):  # type: ignore
        pass
else:
    class LocalSocket (UnixSocket):
        pass
