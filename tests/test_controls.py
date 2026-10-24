"""Ported from Overdrive ba0307a tests/test_controls.py (touchscreen and controller parts)."""
import asyncio

import pytest

from fake_helper import FakeClient, fail, ok
from traction import controls
from traction.settings import Settings

RESTARTED = "Traction helper restarted, try again"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fresh_cache():
    controls.reset_cache()
    yield
    controls.reset_cache()


def settings(tmp_path, **fields):
    s = Settings.load(str(tmp_path / "settings.json"))
    for k, v in fields.items():
        setattr(s, k, v)
    return s


def handler(reply):
    async def h(args):
        return reply
    return h


def test_transport_failure_matches_traction_texts():
    assert controls.transport_failure(fail(RESTARTED))
    assert controls.transport_failure(fail("Traction helper did not answer in 10 s."))
    assert controls.transport_failure(fail("Traction helper is not running (starting)."))
    assert not controls.transport_failure(fail("Controller not found"))
    assert not controls.transport_failure(ok({}))


def test_touchscreen_toggle(tmp_path, monkeypatch):
    async def go():
        now = [1000.0]
        monkeypatch.setattr(controls, "clock", lambda: now[0])
        c = FakeClient()
        c.handlers["get_touchscreen"] = handler(ok({"found": True, "enabled": True, "instanceId": "x"}))
        count = lambda: len([x for x in c.calls if x[0] == "get_touchscreen"])
        assert (await controls.read_controls(c))["touchscreen"] == {"found": True, "enabled": True}
        assert (await controls.set_touchscreen(c, False))["ok"]
        assert c.writes() == [("set_touchscreen", {"enabled": False})]
        # C4: the cache is dropped, so the next read asks the real device (never shows the requested value)
        st = await controls.read_controls(c)
        assert st["touchscreen"] == {"found": True, "enabled": True} and count() == 2
        # the change has not landed yet: each read asks again (the panel polls for up to 15 s)
        now[0] += 1.5
        await controls.read_controls(c)
        assert count() == 3
        c.handlers["get_touchscreen"] = handler(ok({"found": True, "enabled": False, "instanceId": "x"}))
        now[0] += 1.5
        assert (await controls.read_controls(c))["touchscreen"] == {"found": True, "enabled": False}
        assert count() == 4
        now[0] += 1.5
        await controls.read_controls(c)                                # landed: cached again
        assert count() == 4
        c.handlers["set_touchscreen"] = handler(fail("no device"))
        assert not (await controls.set_touchscreen(c, True))["ok"]
        await controls.read_controls(c)                                # a refused write keeps the cache
        assert count() == 4
    run(go())


def test_touchscreen_rereads_stop_after_15_seconds(tmp_path, monkeypatch):
    async def go():
        now = [1000.0]
        monkeypatch.setattr(controls, "clock", lambda: now[0])
        c = FakeClient()
        c.handlers["get_touchscreen"] = handler(ok({"found": True, "enabled": True, "instanceId": "x"}))
        count = lambda: len([x for x in c.calls if x[0] == "get_touchscreen"])
        assert (await controls.set_touchscreen(c, False))["ok"]
        await controls.read_controls(c)
        now[0] += 16                                                   # never landed: back to the 30 s cache
        await controls.read_controls(c)
        await controls.read_controls(c)
        assert count() == 1
    run(go())


def test_failed_touchscreen_read_keeps_the_last_status(tmp_path, monkeypatch):
    async def go():
        now = [1000.0]
        monkeypatch.setattr(controls, "clock", lambda: now[0])
        c = FakeClient()
        good = handler(ok({"found": True, "enabled": True, "instanceId": "x"}))
        bad = handler(fail("PowerShell did not answer in 5 seconds."))
        count = lambda: len([x for x in c.calls if x[0] == "get_touchscreen"])
        # no good reading yet: a failure reports not found, and is not cached
        c.handlers["get_touchscreen"] = bad
        assert (await controls.read_controls(c))["touchscreen"] == {"found": False, "enabled": False}
        c.handlers["get_touchscreen"] = good
        assert (await controls.read_controls(c))["touchscreen"] == {"found": True, "enabled": True}
        assert count() == 2
        # cache expired, the read fails: the last status is shown (toggle stays), and the next read asks again
        now[0] += 31
        c.handlers["get_touchscreen"] = bad
        assert (await controls.read_controls(c))["touchscreen"] == {"found": True, "enabled": True}
        assert (await controls.read_controls(c))["touchscreen"] == {"found": True, "enabled": True}
        assert count() == 4
        c.handlers["get_touchscreen"] = good
        await controls.read_controls(c)
        await controls.read_controls(c)                                 # good again: cached
        assert count() == 5
        # settling on "off": a failed read reports the last real status (on), never the requested off
        assert (await controls.set_touchscreen(c, False))["ok"]
        c.handlers["get_touchscreen"] = bad
        now[0] += 1.5
        assert (await controls.read_controls(c))["touchscreen"] == {"found": True, "enabled": True}
        now[0] += 1.5
        await controls.read_controls(c)
        assert count() == 7                                             # still asking: not settled
    run(go())


def test_read_controls_values_and_unsupported(tmp_path):
    async def go():
        c = FakeClient()
        c.handlers["controller_info"] = handler(ok({"found": True, "pid": "0x1B4C", "xbox": False}))
        c.handlers["get_touchscreen"] = handler(ok({"found": True, "enabled": True, "instanceId": "HID\\x"}))
        st = await controls.read_controls(c)
        assert st["controller"] == {"found": True}
        assert st["touchscreen"] == {"found": True, "enabled": True}
        for cmd in ("controller_info", "get_touchscreen"):
            c.handlers[cmd] = handler(fail("not supported"))
        controls.reset_cache()
        st = await controls.read_controls(c)
        assert st["controller"] == {"found": False}
        assert st["touchscreen"] == {"found": False, "enabled": False}
    run(go())


def test_touchscreen_status_is_cached_for_30_seconds(tmp_path, monkeypatch):
    async def go():
        now = [1000.0]
        monkeypatch.setattr(controls, "clock", lambda: now[0])
        c = FakeClient()
        c.handlers["get_touchscreen"] = handler(ok({"found": True, "enabled": True, "instanceId": "x"}))
        count = lambda: len([x for x in c.calls if x[0] == "get_touchscreen"])
        await controls.read_controls(c)
        now[0] += 29
        await controls.read_controls(c)
        assert count() == 1
        now[0] += 2
        await controls.read_controls(c)
        assert count() == 2
        assert c.timeouts["get_touchscreen"] == [15.0, 15.0]           # PowerShell in the helper: a long read
    run(go())


def test_apply_controller_writes_mode_and_vibration(tmp_path):
    async def go():
        c = FakeClient()
        s = settings(tmp_path, controller_mode="desktop", grip_vibration=False)
        r = await controls.apply_controller(c, s)
        assert r["ok"]
        assert c.writes() == [("set_controller_mode", {"mode": "desktop"}), ("set_vibration", {"strength": 0})]
        c.calls.clear()
        s.grip_vibration = True
        await controls.apply_controller(c, s, "gamepad")
        assert c.writes() == [("set_controller_mode", {"mode": "gamepad"}), ("set_vibration", {"strength": 100})]
        c.calls.clear()
        s.controller_mode = "auto"      # auto starts in gamepad
        await controls.apply_controller(c, s)
        assert c.writes()[0] == ("set_controller_mode", {"mode": "gamepad"})
        c.calls.clear()
        assert not (await controls.apply_controller(c, s, "bogus"))["ok"]
        assert c.calls == []
    run(go())


def test_apply_controller_reports_each_failure(tmp_path):
    async def go():
        c = FakeClient()
        c.handlers["set_controller_mode"] = handler(fail("mode refused"))
        r = await controls.apply_controller(c, settings(tmp_path))
        assert not r["ok"] and "mode refused" in r["message"]
        assert ("set_vibration", {"strength": 100}) in c.writes()   # still tried
    run(go())


def test_apply_controller_stops_after_transport_failure(tmp_path):
    async def go():
        c = FakeClient()
        c.handlers["set_controller_mode"] = handler(fail(RESTARTED))
        r = await controls.apply_controller(c, settings(tmp_path))
        assert not r["ok"] and r["message"] == RESTARTED
        assert [x[0] for x in c.calls] == ["set_controller_mode"]
    run(go())
