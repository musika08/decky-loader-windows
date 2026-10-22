r'''Windows stand-in for Linux's `steam-web-debug-portforward` service: forwards 0.0.0.0:8081 to Steam's CEF debugger
on 127.0.0.1:8080, inside the loader, so remote debugging works from another machine.

Off by default; turned on by the Developer "remote debugging" toggle (persisted as `cef_forward`, like on Linux) or the
crash page. Anyone on the network can then reach the debugger without authentication, as on Linux. No firewall rules
are added, so Windows may still block or ask about incoming connections.
'''
from asyncio import AbstractServer, StreamReader, StreamWriter, gather, open_connection, start_server
from logging import getLogger

logger = getLogger("winportforward")

UNIT = "steam-web-debug-portforward.service"
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8081
TARGET_HOST = "127.0.0.1"
TARGET_PORT = 8080
CHUNK = 65536

async def _pipe(reader: StreamReader, writer: StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(CHUNK)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()

class PortForward:
    '''A TCP forward from (listen_host, listen_port) to (target_host, target_port); based on Utilities.start_rdt_proxy.'''
    def __init__(self, listen_host: str = LISTEN_HOST, listen_port: int = LISTEN_PORT,
                 target_host: str = TARGET_HOST, target_port: int = TARGET_PORT) -> None:
        self.listen_host, self.listen_port = listen_host, listen_port
        self.target_host, self.target_port = target_host, target_port
        self.server: AbstractServer | None = None
        # Open connections, closed on stop: since Python 3.12 wait_closed() waits for them
        self.writers: set[StreamWriter] = set()

    @property
    def active(self) -> bool:
        return self.server is not None and self.server.is_serving()

    async def _handle(self, local_reader: StreamReader, local_writer: StreamWriter) -> None:
        self.writers.add(local_writer)
        try:
            try:
                remote_reader, remote_writer = await open_connection(self.target_host, self.target_port)
            except OSError as e:
                logger.debug(f"CEF debugger not reachable on {self.target_host}:{self.target_port}: {e}")
                local_writer.close()
                return
            self.writers.add(remote_writer)
            try:
                await gather(_pipe(local_reader, remote_writer), _pipe(remote_reader, local_writer))
            finally:
                self.writers.discard(remote_writer)
        finally:
            self.writers.discard(local_writer)

    async def start(self) -> bool:
        if self.active:
            return True
        try:
            self.server = await start_server(self._handle, self.listen_host, self.listen_port)
        except OSError as e:
            logger.error(f"Could not listen on {self.listen_host}:{self.listen_port} for CEF remote debugging: {e}")
            self.server = None
            return False
        logger.info(f"Forwarding {self.listen_host}:{self.listen_port} to the CEF debugger on {self.target_host}:{self.target_port}")
        return True

    async def stop(self) -> bool:
        if self.server is not None:
            self.server.close()
            for writer in list(self.writers):
                writer.close()
            await self.server.wait_closed()
            self.server = None
            logger.info("Stopped forwarding to the CEF debugger")
        return True

forward = PortForward()
