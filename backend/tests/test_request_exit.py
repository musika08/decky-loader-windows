import asyncio, socket, sys
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("service,expected", [("restart", 42), ("stop", 43)])
def test_service_calls_run_on_shutdown_and_record_code(service: str, expected: int):
    from aiohttp import web
    from decky_loader.localplatform import localplatformwin as lp

    events: list[str] = []

    async def on_shutdown(_app: Any) -> None:
        events.append("on_shutdown")

    async def trigger() -> None:
        # Raised from a task, as the websocket handlers do in the real loader
        if service == "restart":
            await lp.service_restart("plugin_loader")
        else:
            await lp.service_stop("plugin_loader")

    async def on_startup(_app: Any) -> None:
        asyncio.get_running_loop().call_later(0.1, lambda: asyncio.get_running_loop().create_task(trigger()))

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    loop = asyncio.new_event_loop()
    web.run_app(app, host="127.0.0.1", port=_free_port(), loop=loop, handle_signals=False, print=None)
    loop.close()

    assert events == ["on_shutdown"]
    assert lp.requested_exit_code() == expected
