import asyncio, sys
from typing import Any
from unittest.mock import MagicMock
import pytest

# sc.exe is always injected: tests never query, start or stop a real service


def _sc(codes: dict[str, int], calls: list[list[str]]) -> Any:
    def run(args: list[str]) -> int:
        calls.append(args)
        return codes.get(args[0], 0)
    return run


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")
class TestSetSshdRunning:
    def _set(self, running: bool, codes: dict[str, int]) -> tuple[str | None, list[list[str]]]:
        from decky_loader.localplatform import winsshd
        calls: list[list[str]] = []
        return asyncio.run(winsshd.set_sshd_running(running, run=_sc(codes, calls))), calls

    def test_not_installed_is_reported_and_nothing_is_started(self):
        from decky_loader.localplatform import winsshd
        error, calls = self._set(True, {"query": winsshd.ERROR_SERVICE_DOES_NOT_EXIST})
        assert error == winsshd.NOT_INSTALLED and "not installed" in error
        assert calls == [["query", "sshd"]]

    def test_start_and_stop(self):
        assert self._set(True, {}) == (None, [["query", "sshd"], ["start", "sshd"]])
        assert self._set(False, {}) == (None, [["query", "sshd"], ["stop", "sshd"]])

    def test_already_in_that_state_is_fine(self):
        from decky_loader.localplatform import winsshd
        assert self._set(True, {"start": winsshd.ERROR_SERVICE_ALREADY_RUNNING})[0] is None
        assert self._set(False, {"stop": winsshd.ERROR_SERVICE_NOT_ACTIVE})[0] is None

    def test_failures_say_why(self):
        from decky_loader.localplatform import winsshd
        assert self._set(True, {"start": winsshd.ERROR_ACCESS_DENIED})[0] == winsshd.NEEDS_ADMIN
        assert self._set(True, {"start": winsshd.ERROR_SERVICE_DISABLED})[0] == winsshd.DISABLED
        assert "exit code 1053" in str(self._set(True, {"start": 1053})[0])

    def test_sc_that_cannot_run_is_reported(self):
        from decky_loader.localplatform import winsshd
        def broken(args: list[str]) -> int:
            raise FileNotFoundError("sc.exe")
        assert "Could not run sc.exe" in str(asyncio.run(winsshd.set_sshd_running(True, run=broken)))

    def test_sc_comes_from_system32(self, monkeypatch: pytest.MonkeyPatch):
        from decky_loader.localplatform import winsshd, wininstall
        monkeypatch.setattr(wininstall, "system_directory", lambda: r"C:\Sys32")
        assert winsshd._sc_exe() == r"C:\Sys32\sc.exe"  # pyright: ignore [reportPrivateUsage]


def _utilities() -> Any:
    from decky_loader.utilities import Utilities
    ctx = MagicMock()
    routes: dict[str, Any] = {}
    def add_route(name: str, fn: Any) -> None:
        routes[name] = fn
    ctx.ws.add_route = add_route
    u = Utilities(ctx)
    return u, routes


def test_ssh_routes_call_the_ssh_methods():
    # Upstream bug: both routes called allow_remote_debugging
    u, routes = _utilities()
    assert routes["utilities/start_ssh"] == u.start_ssh
    assert routes["utilities/stop_ssh"] == u.stop_ssh
    assert routes["utilities/allow_remote_debugging"] == u.allow_remote_debugging


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")
def test_windows_ssh_returns_the_reason(monkeypatch: pytest.MonkeyPatch):
    from decky_loader.localplatform import winsshd
    results: dict[bool, str | None] = {True: winsshd.NOT_INSTALLED, False: None}
    async def fake(running: bool) -> str | None:
        return results[running]
    monkeypatch.setattr(winsshd, "set_sshd_running", fake)
    u, _routes = _utilities()
    assert asyncio.run(u.start_ssh()) == winsshd.NOT_INSTALLED
    assert asyncio.run(u.stop_ssh()) is True
