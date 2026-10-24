"""get_session must accept the very long response headers GitHub sends on release downloads.

github.com/<owner>/<repo>/releases/download/... answers with a Content-Security-Policy header longer than
aiohttp's default 8190-byte field limit, which made store installs from GitHub release assets fail (seen live
2026-10-24 installing Control Center from the Windows plugin store)."""
import asyncio

from decky_loader.helpers import get_session


def test_session_allows_long_headers():
    async def go():
        async with get_session() as s:
            assert s._max_field_size >= 65536
            assert s._max_line_size >= 65536
    asyncio.run(go())


def test_session_caller_can_still_override():
    async def go():
        async with get_session(max_field_size=100000) as s:
            assert s._max_field_size == 100000
    asyncio.run(go())
