import asyncio
import json

import pytest

import main
from fake_helper import FakeClient, fail, ok
from overdrive.reapply import Reapplier
from overdrive.settings import Settings


class Clock:
    def time(self):
        return 1_700_000_000.0

    def monotonic(self):
        return 5_000.0


def plugin_with(tmp_path, client=None, **fields):
    p = main.Plugin()
    s = Settings.load(str(tmp_path / "overdrive.json"))
    for k, v in fields.items():
        setattr(s, k, v)
    c = client or FakeClient()
    p._attach(c, s, Reapplier(c, s, clock=Clock(), power_source=lambda: True))
    return p, c, s


def run(coro):
    return asyncio.run(coro)


def test_not_ally_starts_nothing(monkeypatch, tmp_path):
    made = []
    monkeypatch.setattr(main.device, "is_ally", lambda read_product=None: False)
    monkeypatch.setattr(main, "HelperClient", lambda *a, **k: made.append("client"))
    monkeypatch.setattr(main, "Reapplier", lambda *a, **k: made.append("reapplier"))
    monkeypatch.setattr(main, "settings_path", lambda: str(tmp_path / "overdrive.json"))

    async def go():
        p = main.Plugin()
        await p._main()
        state = await p.get_state()
        r = await p.set_mode("turbo")
        await p._unload()
        return state, r
    state, r = run(go())
    assert made == []
    assert state["support"]["supported"] is False
    assert r["ok"] is False and r["message"] == "Overdrive supports the ROG Ally family only"
    assert not (tmp_path / "overdrive.json").exists()


def test_ally_starts_client_and_reapplier_and_unload_stops_both(monkeypatch, tmp_path):
    clients = []

    def make_client(*a, **k):
        c = FakeClient()
        clients.append(c)
        return c

    monkeypatch.setattr(main.device, "is_ally", lambda read_product=None: True)
    monkeypatch.setattr(main, "HelperClient", make_client)
    monkeypatch.setattr(main, "settings_path", lambda: str(tmp_path / "overdrive.json"))

    async def go():
        p = main.Plugin()
        await p._main()
        await p._startup
        assert p.reapplier._task is not None
        await p._unload()
        return p
    p = run(go())
    assert clients and clients[0].started and clients[0].stopped
    assert p.reapplier._task is None


def test_get_state_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(main.device, "product_name", lambda read_product=None: "ROG Ally RC71L")

    async def go():
        p, c, s = plugin_with(tmp_path, mode="manual", spl=10, sppt=20, fppt=30, fps=60)
        return await p.get_state()
    st = run(go())
    assert set(st) >= {"support", "mode", "spl", "sppt", "fppt", "fps", "fan_preset", "fan_curve", "armoury",
                       "unavailable"}
    assert st["support"] == {"supported": True, "product": "ROG Ally RC71L"}
    assert (st["mode"], st["spl"], st["sppt"], st["fppt"], st["fps"]) == ("manual", 10, 20, 30, 60)
    assert st["armoury"] == {"installed": True, "replaced": False}
    assert st["unavailable"] == ""


def test_set_mode_saves_applied_values_and_rewrites_fans(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, fan_preset="cool",
                              default_curve={m: dict(main_curve()) for m in ("balanced", "turbo", "silent")})
        gen = p.reapplier.generation
        r = await p.set_mode("manual", 40, 20, 45)
        assert r["ok"] is True
        assert p.reapplier.generation == gen + 1
        assert c.writes()[0] == ("set_mode", {"mode": "manual", "spl": 40, "sppt": 40, "fppt": 45})
        assert c.writes()[1][0] == "set_fans"      # the mode write reset the curve; the preset goes back on
        saved = Settings.load(s.path)
        assert (saved.mode, saved.spl, saved.sppt, saved.fppt, saved.user_set) == ("manual", 40, 40, 45, True)
    run(go())


def main_curve():
    return {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [0, 5, 10, 20, 30, 40, 50, 60]}


def test_refused_change_keeps_stored_value(tmp_path):
    async def go():
        c = FakeClient()

        async def refuse(args):
            return fail("The device refused the performance mode (result 0).")
        c.handlers["set_mode"] = refuse
        p, c, s = plugin_with(tmp_path, client=c)
        r = await p.set_mode("turbo")
        assert r == {"ok": False, "message": "The device refused the performance mode (result 0)."}
        assert s.mode == "balanced"
        assert (await p.set_mode("silent", 10, 10, 10))["ok"] is False      # limits only with manual
        assert (await p.set_fps(33))["ok"] is False
        assert (await p.set_fan_curve([30] * 8, [0] * 8))["message"] == \
            "Point 2: temperature must be higher than point 1."
        assert all(cmd != "set_fans" for cmd, _ in c.calls)
    run(go())


def test_fan_curve_and_presets(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, default_curve={m: main_curve() for m in ("balanced", "turbo", "silent")})
        temps = [30, 40, 50, 60, 70, 80, 90, 100]
        speeds = [10, 20, 30, 40, 50, 60, 70, 80]
        assert (await p.set_fan_curve(temps, speeds))["ok"] is True
        assert (s.fan_preset, s.fan_curve) == ("custom", {"temps": temps, "speeds": speeds})
        c.calls.clear()
        assert (await p.set_fan_preset("default"))["ok"] is True
        assert [cmd for cmd, _ in c.writes()] == ["set_mode"]   # the firmware curve comes back with the mode write
        c.calls.clear()
        assert (await p.set_fan_preset("quiet"))["ok"] is True
        assert [cmd for cmd, _ in c.writes()] == ["set_fans"]
        assert (await p.set_fan_preset("custom"))["ok"] is True  # stored custom curve still there
        assert (await p.set_fan_preset("loud"))["ok"] is False
        st = await p.get_state()
        assert st["fan_curve"] == {"temps": temps, "speeds": speeds}
    run(go())


def test_reset_defaults(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, mode="manual", spl=10, sppt=10, fppt=10, fps=60, fan_preset="cool",
                              default_curve={m: main_curve() for m in ("balanced", "turbo", "silent")})
        r = await p.reset_defaults()
        assert r["ok"] is True
        assert c.writes() == [("set_mode", {"mode": "balanced"}), ("set_fps_limit", {"fps": 0})]
        saved = Settings.load(s.path)
        assert (saved.mode, saved.spl, saved.sppt, saved.fppt, saved.fps, saved.fan_preset) == \
            ("balanced", 30, 30, 30, 0, "default")
    run(go())


def test_armoury_on_saves_recorded_state_before_success_reply(tmp_path):
    async def go():
        c = FakeClient()
        seen_on_disk = {}

        async def restore_spy(args):
            return ok({"skipped": []})
        c.handlers["armoury_restore"] = restore_spy
        p, c, s = plugin_with(tmp_path, client=c)
        r = await p.set_armoury_replaced(True)
        # the reply is only produced after the file holds what armoury_restore needs
        with open(s.path, encoding="utf-8") as f:
            seen_on_disk.update(json.load(f))
        assert r["ok"] is True
        assert seen_on_disk["armoury_replaced"] is True
        assert seen_on_disk["armoury_recorded"] == {"ArmouryCrateSEService": "AutomaticDelayedStart",
                                                    "LightingService": "Automatic"}
        assert seen_on_disk["armoury_running"] == ["ArmouryCrateSEService"]
        # state first, then a stop sized to the running services: max(5 min, 60 s x running + 60 s)
        assert [cmd for cmd, _ in c.calls][:2] == ["armoury_state", "armoury_stop"]
        assert c.timeouts["armoury_stop"] == [300]
        c.armoury["running"] = ["S%d" % i for i in range(8)]
        assert (await p.set_armoury_replaced(False))["ok"] is True
        assert c.timeouts["armoury_restore"] == [540]
    run(go())


def test_armoury_on_save_failure_restores_and_reports(tmp_path, monkeypatch):
    async def go():
        p, c, s = plugin_with(tmp_path)

        def broken_save():
            raise OSError("disk full")
        monkeypatch.setattr(s, "save", broken_save)
        r = await p.set_armoury_replaced(True)
        assert r["ok"] is False and "disk full" in r["message"]
        assert ("armoury_restore", {"recorded": {"ArmouryCrateSEService": "AutomaticDelayedStart",
                                                 "LightingService": "Automatic"},
                                    "running": ["ArmouryCrateSEService"]}) in c.calls
        assert s.armoury_replaced is False
    run(go())


def test_armoury_stop_failure_keeps_switch_off(tmp_path):
    async def go():
        c = FakeClient()

        async def stop_fails(args):
            return fail("Could not stop AsHidService (stop): access denied. Every service changed so far was put back.",
                        {"failed": "AsHidService", "rolledBack": True})
        c.handlers["armoury_stop"] = stop_fails
        p, c, s = plugin_with(tmp_path, client=c)
        r = await p.set_armoury_replaced(True)
        assert r["ok"] is False and "AsHidService" in r["message"]
        assert s.armoury_replaced is False and s.armoury_recorded == {}
    run(go())


def test_armoury_off_restores_recorded_state(tmp_path):
    async def go():
        recorded = {"ArmouryCrateService": "Automatic"}
        p, c, s = plugin_with(tmp_path, armoury_replaced=True, armoury_recorded=dict(recorded),
                              armoury_running=["ArmouryCrateService"])
        r = await p.set_armoury_replaced(False)
        assert r["ok"] is True
        assert c.calls[-1] == ("armoury_restore", {"recorded": recorded, "running": ["ArmouryCrateService"]})
        saved = Settings.load(s.path)
        assert saved.armoury_replaced is False and saved.armoury_recorded == {}
    run(go())


def test_load_restops_armoury_without_overwriting_record(tmp_path):
    async def go():
        recorded = {"ArmouryCrateService": "Automatic"}
        p, c, s = plugin_with(tmp_path, armoury_replaced=True, armoury_recorded=dict(recorded),
                              armoury_running=["ArmouryCrateService"])
        await p._startup_sequence()
        assert [cmd for cmd, _ in c.calls][:2] == ["armoury_state", "armoury_stop"]
        assert s.armoury_recorded == recorded and s.armoury_running == ["ArmouryCrateService"]
        await p.reapplier.stop()
    run(go())


def test_get_sensors(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        return await p.get_sensors()
    r = run(go())
    assert r == {"ok": True, "message": "", "cpuTempC": 55, "fanRpm": 3100, "errors": []}


def test_plugin_json_runs_as_root():
    with open(main.os.path.join(main.os.path.dirname(main.__file__), "plugin.json"), encoding="utf-8") as f:
        assert "root" in json.load(f)["flags"]


@pytest.fixture(autouse=True)
def _no_real_helper(monkeypatch):
    # Belt and braces: no test in this file may ever spawn a process.
    async def refuse(*a, **k):
        raise AssertionError("tests must not start a process")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse)
