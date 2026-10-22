import asyncio, sys
from typing import Any
from unittest.mock import MagicMock


def _updater() -> Any:
    from decky_loader.updater import Updater

    def drop(coro: Any) -> None:
        coro.close()  # don't run background tasks such as version_reloader

    ctx = MagicMock()
    ctx.loop.create_task = drop
    return Updater(ctx)


def test_version_info_reports_platform(monkeypatch: Any):
    # The store shows its Windows compatibility badges only when this says win32
    u = _updater()
    info = asyncio.run(u.get_version_info())
    assert info["platform"] == sys.platform

    monkeypatch.setattr(sys, "platform", "win32")
    assert asyncio.run(u.get_version_info())["platform"] == "win32"
    monkeypatch.setattr(sys, "platform", "linux")
    assert asyncio.run(u.get_version_info())["platform"] == "linux"


def test_version_info_keeps_existing_keys():
    info = asyncio.run(_updater().get_version_info())
    assert {"current", "repo", "gitBranch", "remote", "all", "updatable", "supported"} <= set(info)
