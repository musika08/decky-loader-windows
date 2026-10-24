import asyncio

from fake_helper import FakeClient, fail, ok
from overdrive import controls
from overdrive.settings import Settings


def run(coro):
    return asyncio.run(coro)


def settings(tmp_path, **fields):
    s = Settings.load(str(tmp_path / "overdrive.json"))
    for k, v in fields.items():
        setattr(s, k, v)
    return s


def handler(reply):
    async def h(args):
        return reply
    return h


def test_cpu_boost_round_trip(tmp_path):
    async def go():
        c = FakeClient()
        c.handlers["get_cpu_boost"] = handler(ok({"ac": 3, "dc": 3}))
        s = settings(tmp_path)
        r = await controls.set_cpu_boost(c, s, False)
        assert r["ok"] and s.cpu_boost_on_value == 3
        assert Settings.load(s.path).cpu_boost_on_value == 3   # saved
        assert c.writes() == [("set_cpu_boost", {"value": 0})]
        c.calls.clear()
        c.handlers["get_cpu_boost"] = handler(ok({"ac": 0, "dc": 0}))
        assert (await controls.set_cpu_boost(c, s, True))["ok"]
        assert c.writes() == [("set_cpu_boost", {"value": 3})]
        # Off from an already-zero read keeps the saved value
        c.calls.clear()
        await controls.set_cpu_boost(c, s, False)
        assert s.cpu_boost_on_value == 3
    run(go())


def test_cpu_boost_first_on_writes_default_two(tmp_path):
    async def go():
        c = FakeClient()
        s = settings(tmp_path)
        assert (await controls.set_cpu_boost(c, s, True))["ok"]
        assert c.writes() == [("set_cpu_boost", {"value": 2})]
    run(go())


def test_cpu_boost_failures_are_reported(tmp_path):
    async def go():
        c = FakeClient()
        c.handlers["set_cpu_boost"] = handler(fail("nope"))
        r = await controls.set_cpu_boost(c, settings(tmp_path), True)
        assert r == {"ok": False, "message": "nope"}
        c.calls.clear()
        c.handlers["get_cpu_boost"] = handler(fail("no power api"))
        r = await controls.set_cpu_boost(c, settings(tmp_path), False)
        assert not r["ok"] and r["message"]
        assert c.writes() == []
    run(go())


def test_power_mode_values(tmp_path):
    async def go():
        c = FakeClient()
        for v in ("eco", "balanced", "best"):
            assert (await controls.set_power_mode(c, v))["ok"]
        assert c.writes() == [("set_power_mode", {"mode": m}) for m in ("eco", "balanced", "best")]
        c.calls.clear()
        assert not (await controls.set_power_mode(c, "turbo"))["ok"]
        assert c.calls == []
        c.handlers["set_power_mode"] = handler(fail("refused"))
        assert (await controls.set_power_mode(c, "eco")) == {"ok": False, "message": "refused"}
    run(go())


def test_auto_sr_on_off(tmp_path):
    async def go():
        c = FakeClient()
        assert (await controls.set_auto_sr(c, True))["ok"]
        assert (await controls.set_auto_sr(c, False))["ok"]
        assert c.writes() == [("set_auto_sr", {"on": True}), ("set_auto_sr", {"on": False})]
        c.handlers["set_auto_sr"] = handler(fail("not installed"))
        assert (await controls.set_auto_sr(c, True))["message"] == "not installed"
    run(go())


def test_read_controls_values_and_unsupported(tmp_path):
    async def go():
        c = FakeClient()
        c.handlers["get_cpu_boost"] = handler(ok({"ac": 0, "dc": 2}))
        c.handlers["get_power_mode"] = handler(ok({"overlay": "x", "mode": "best"}))
        c.handlers["get_auto_sr"] = handler(ok({"installed": True, "value": 1028}))
        st = await controls.read_controls(c)
        assert st["cpu_boost"] == {"on": True, "supported": True}
        assert st["power_mode"] == {"value": "best", "supported": True}
        assert st["auto_sr"] == {"installed": True, "on": True}
        c.handlers["get_auto_sr"] = handler(ok({"installed": True, "value": 1024}))
        c.handlers["get_power_mode"] = handler(ok({"overlay": "x", "mode": "other"}))
        st = await controls.read_controls(c)
        assert st["auto_sr"]["on"] is False and st["power_mode"]["value"] == "other"
        c.handlers["get_auto_sr"] = handler(ok({"installed": True, "value": 7}))
        assert (await controls.read_controls(c))["auto_sr"] == {"installed": True, "on": None}
        for cmd in ("get_cpu_boost", "get_power_mode", "get_auto_sr"):
            c.handlers[cmd] = handler(fail("not supported"))
        st = await controls.read_controls(c)
        assert st["cpu_boost"] == {"on": False, "supported": False}
        assert st["power_mode"] == {"value": "other", "supported": False}
        assert st["auto_sr"] == {"installed": False, "on": None}
        assert "controller" not in st and "touchscreen" not in st
    run(go())
