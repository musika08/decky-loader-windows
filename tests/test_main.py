import asyncio
import json

import pytest

import main
from fake_helper import GPU_CURVE, FakeClient, fail, ok
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
    assert set(st) >= {"support", "mode", "spl", "sppt", "fppt", "fps", "fan_preset", "fan_curve", "fan_curve_gpu",
                       "armoury", "unavailable"}
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


def test_each_mode_sends_its_own_watts(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        for mode, w in (("silent", 13), ("balanced", 17), ("turbo", 35)):
            c.calls.clear()
            assert (await p.set_mode(mode))["ok"] is True
            assert c.writes()[0] == ("set_mode", {"mode": mode, "spl": w, "sppt": w, "fppt": w})
        saved = Settings.load(s.path)
        assert (saved.mode, saved.spl, saved.sppt, saved.fppt) == ("turbo", 30, 30, 30)   # Manual's own limits kept
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
        assert (await p.set_fan_curve([30, 25, 30, 30, 30, 30, 30, 30], [0] * 8))["message"] == \
            "Point 2: temperature can't be lower than point 1."
        assert all(cmd != "set_fans" for cmd, _ in c.calls)
    run(go())


def test_fan_curve_and_presets(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, default_curve={m: main_curve() for m in ("balanced", "turbo", "silent")},
                              default_curve_gpu={m: dict(GPU_CURVE) for m in ("balanced", "turbo", "silent")})
        temps = [30, 40, 50, 60, 70, 80, 90, 100]
        speeds = [10, 20, 30, 40, 50, 60, 70, 80]
        assert (await p.set_fan_curve(temps, speeds))["ok"] is True
        assert (s.fan_preset, s.fan_curve) == ("custom", {"temps": temps, "speeds": speeds})
        c.calls.clear()
        assert (await p.set_fan_preset("default"))["ok"] is True
        assert [cmd for cmd, _ in c.writes()] == ["set_mode"]   # both firmware curves come back with the mode write
        c.calls.clear()
        assert (await p.set_fan_preset("quiet"))["ok"] is True
        assert [(cmd, a.get("fan")) for cmd, a in c.writes()] == [("set_fans", None), ("set_fans", "gpu")]
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
        assert c.writes() == [("set_mode", {"mode": "balanced", "spl": 17, "sppt": 17, "fppt": 17}), ("set_fps_limit", {"fps": 0})]
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
        real_save = s.save
        saves = []

        def save_then_fail():
            saves.append(1)
            if len(saves) > 1:              # the provisional record saves; the final "on" does not
                raise OSError("disk full")
            real_save()
        monkeypatch.setattr(s, "save", save_then_fail)
        r = await p.set_armoury_replaced(True)
        assert r["ok"] is False and "disk full" in r["message"]
        assert ("armoury_restore", {"recorded": {"ArmouryCrateSEService": "AutomaticDelayedStart",
                                                 "LightingService": "Automatic"},
                                    "running": ["ArmouryCrateSEService"]}) in c.calls
        assert s.armoury_replaced is False
    run(go())


def test_armoury_on_refused_when_record_cannot_be_saved(tmp_path, monkeypatch):
    async def go():
        p, c, s = plugin_with(tmp_path)

        def broken_save():
            raise OSError("disk full")
        monkeypatch.setattr(s, "save", broken_save)
        r = await p.set_armoury_replaced(True)
        assert r["ok"] is False and "nothing was changed" in r["message"]
        assert all(cmd != "armoury_stop" for cmd, _ in c.calls)
        assert s.armoury_recorded == {}
    run(go())


def test_armoury_provisional_record_saved_before_stop(tmp_path):
    async def go():
        c = FakeClient()
        on_disk = {}

        async def stop_lost(args):
            with open(s.path, encoding="utf-8") as f:
                on_disk.update(json.load(f))
            # services get stopped and disabled, but the reply never arrives (helper killed)
            c.armoury["startTypes"] = {"ArmouryCrateSEService": "Manual", "LightingService": "Manual"}
            c.armoury["running"] = []
            return fail("Overdrive helper restarted, try again")
        c.handlers["armoury_stop"] = stop_lost
        p, c, s = plugin_with(tmp_path, client=c)
        r = await p.set_armoury_replaced(True)
        assert r["ok"] is False
        original = {"ArmouryCrateSEService": "AutomaticDelayedStart", "LightingService": "Automatic"}
        assert on_disk["armoury_recorded"] == original and on_disk["armoury_replaced"] is False
        saved = Settings.load(s.path)
        assert saved.armoury_recorded == original and saved.armoury_running == ["ArmouryCrateSEService"]

        # the next switch-on keeps the provisional record instead of recording the stopped state
        del c.handlers["armoury_stop"]
        assert (await p.set_armoury_replaced(True))["ok"] is True
        saved = Settings.load(s.path)
        assert saved.armoury_replaced is True
        assert saved.armoury_recorded == original and saved.armoury_running == ["ArmouryCrateSEService"]
    run(go())


def test_armoury_off_with_provisional_record_restores(tmp_path):
    async def go():
        recorded = {"ArmouryCrateService": "Automatic"}
        p, c, s = plugin_with(tmp_path, armoury_replaced=False, armoury_recorded=dict(recorded),
                              armoury_running=["ArmouryCrateService"])
        r = await p.set_armoury_replaced(False)
        assert r["ok"] is True
        assert c.calls[-1] == ("armoury_restore", {"recorded": recorded, "running": ["ArmouryCrateService"]})
        assert Settings.load(s.path).armoury_recorded == {}
    run(go())


def test_armoury_stop_rolled_back_clears_provisional_record(tmp_path):
    async def go():
        c = FakeClient()

        async def stop_fails(args):
            return fail("Could not stop AsHidService (stop): access denied. Every service changed so far was put back.",
                        {"failed": "AsHidService", "rolledBack": True})
        c.handlers["armoury_stop"] = stop_fails
        p, c, s = plugin_with(tmp_path, client=c)
        assert (await p.set_armoury_replaced(True))["ok"] is False
        assert Settings.load(s.path).armoury_recorded == {}
    run(go())


def test_armoury_off_with_empty_record_leaves_services(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, armoury_replaced=True)
        r = await p.set_armoury_replaced(False)
        assert r["ok"] is True and "No recorded Armoury Crate state" in r["message"]
        assert all(cmd != "armoury_restore" for cmd, _ in c.calls)
        assert s.armoury_replaced is False
    run(go())


def test_busy_while_armoury_call_runs(tmp_path):
    async def go():
        c = FakeClient()
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_stop(args):
            entered.set()
            await release.wait()
            return ok({"startTypes": dict(c.armoury["startTypes"]), "running": list(c.armoury["running"])})
        c.handlers["armoury_stop"] = slow_stop
        p, c, s = plugin_with(tmp_path, client=c,
                              default_curve={m: main_curve() for m in ("balanced", "turbo", "silent")})
        switch = asyncio.ensure_future(p.set_armoury_replaced(True))
        await entered.wait()
        busy = {"ok": False, "message": "Overdrive is switching Armoury Crate services. Try again when it finishes."}
        assert await p.set_mode("turbo") == busy
        assert await p.set_fps(60) == busy
        assert await p.set_fan_preset("quiet") == busy
        assert await p.set_fan_curve([30, 40, 50, 60, 70, 80, 90, 100], [10, 20, 30, 40, 50, 60, 70, 80]) == busy
        assert await p.reset_defaults() == busy
        assert await p.set_armoury_replaced(False) == busy
        assert c.writes() == [("armoury_stop", None)]          # nothing queued behind the service switch
        st = await asyncio.wait_for(p.get_state(), 1)          # reads still work, and do not queue either
        assert st["busy"] is True
        assert (await p.get_sensors())["ok"] is True
        release.set()
        assert (await switch)["ok"] is True
        assert (await p.get_state())["busy"] is False
        assert (await p.set_mode("turbo"))["ok"] is True
    run(go())


def test_hung_helper_with_get_state_polling_ends_unavailable_not_busy(tmp_path):
    from overdrive.helper_client import HelperClient

    class HungProc:
        """A helper that reads requests and never answers."""
        def __init__(self):
            self.returncode = None
            self.stdout = asyncio.StreamReader()
            proc = self

            class In:
                def write(self, b):
                    pass

                async def drain(self):
                    pass

                def close(self):
                    proc.kill()
            self.stdin = In()

        def kill(self):
            if self.returncode is None:
                self.returncode = -9
                self.stdout.feed_eof()

        async def wait(self):
            return self.returncode

    async def go():
        async def start():
            return HungProc()

        async def no_sleep(d):
            pass
        client = HelperClient(start_process=start, sleep=no_sleep, default_timeout=0.1)
        p, c, s = plugin_with(tmp_path, client=client)
        try:
            for _ in range(100):
                if client.unavailable_reason:
                    break
                states = await asyncio.gather(p.get_state(), p.get_state())
                assert all(st["busy"] is False for st in states)
            st = await p.get_state()
            assert st["busy"] is False
            assert "5 times in 10 minutes" in st["unavailable"]
        finally:
            await client.stop()
    run(go())


def test_setter_waiting_for_lock_is_refused_when_switch_begins(tmp_path):
    async def go():
        c = FakeClient()
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_stop(args):
            entered.set()
            await release.wait()
            return ok({})
        c.handlers["armoury_stop"] = slow_stop
        p, c, s = plugin_with(tmp_path, client=c)
        gate = asyncio.Event()

        async def hold():
            async with p.reapplier.user_change():
                await gate.wait()
        holder = asyncio.ensure_future(hold())
        await asyncio.sleep(0)
        setter = asyncio.ensure_future(p.set_mode("turbo"))     # passes the first check, waits for the lock
        await asyncio.sleep(0.05)
        switch = asyncio.ensure_future(p.set_armoury_replaced(True))
        await entered.wait()
        gate.set()
        r = await setter
        assert r["message"] == "Overdrive is switching Armoury Crate services. Try again when it finishes."
        assert all(cmd != "set_mode" for cmd, _ in c.calls)
        release.set()
        await asyncio.gather(holder, switch)
    run(go())


def test_load_skips_restop_when_state_unreadable(tmp_path):
    async def go():
        c = FakeClient()

        async def state_fails(args):
            return fail("Overdrive helper did not answer in 10 s.")
        c.handlers["armoury_state"] = state_fails
        p, c, s = plugin_with(tmp_path, client=c, armoury_replaced=True,
                              armoury_recorded={"ArmouryCrateService": "Automatic"})
        await p._startup_sequence()
        assert all(cmd != "armoury_stop" for cmd, _ in c.calls)
        st = await p.get_state()
        assert "were not stopped again" in st["warning"] and "did not answer" in st["warning"]
        assert p.reapplier._task is not None
        await p.reapplier.stop()
    run(go())


def test_armoury_on_shows_real_reason_when_state_unreadable(tmp_path):
    async def go():
        c = FakeClient()

        async def state_fails(args):
            return fail("Overdrive helper stopped 5 times in 10 minutes. Last error: x")
        c.handlers["armoury_state"] = state_fails
        p, c, s = plugin_with(tmp_path, client=c)
        r = await p.set_armoury_replaced(True)
        assert r["ok"] is False and "stopped 5 times in 10 minutes" in r["message"]
        assert all(cmd != "armoury_stop" for cmd, _ in c.calls)
    run(go())


def test_unload_waits_for_armoury_switch(tmp_path):
    async def go():
        c = FakeClient()
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_stop(args):
            entered.set()
            await release.wait()
            return ok({"startTypes": dict(c.armoury["startTypes"]), "running": list(c.armoury["running"])})
        c.handlers["armoury_stop"] = slow_stop
        p, c, s = plugin_with(tmp_path, client=c)
        switch = asyncio.ensure_future(p.set_armoury_replaced(True))
        await entered.wait()
        unload = asyncio.ensure_future(p._unload())
        await asyncio.sleep(0.2)
        assert not c.stopped and not unload.done()
        release.set()
        await unload
        assert c.stopped
        assert (await switch)["ok"] is True
        assert Settings.load(s.path).armoury_replaced is True
    run(go())


def test_load_restop_is_busy_and_adds_new_services_only(tmp_path):
    async def go():
        c = FakeClient()
        c.armoury["startTypes"] = {"ArmouryCrateService": "Disabled", "LightingService": "Automatic"}
        c.armoury["running"] = ["LightingService"]
        seen = {}

        async def stop(args):
            seen["busy"] = (await p.get_state())["busy"]
            return ok({"startTypes": {}, "running": []})
        c.handlers["armoury_stop"] = stop
        recorded = {"ArmouryCrateService": "Automatic"}
        p, c, s = plugin_with(tmp_path, client=c, armoury_replaced=True, armoury_recorded=dict(recorded),
                              armoury_running=["ArmouryCrateService"])
        await p._startup_sequence()
        assert seen["busy"] is True
        saved = Settings.load(s.path)
        assert saved.armoury_recorded == {"ArmouryCrateService": "Automatic", "LightingService": "Automatic"}
        assert saved.armoury_running == ["ArmouryCrateService", "LightingService"]
        await p.reapplier.stop()
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
        # existing entries untouched (services the fake reports that were not recorded are added, see below)
        assert s.armoury_recorded["ArmouryCrateService"] == "Automatic"
        assert s.armoury_running[0] == "ArmouryCrateService"
        await p.reapplier.stop()
    run(go())


def test_get_sensors(tmp_path):
    times = iter([(100, 300, 100), (150, 400, 200)])   # idle, kernel (with idle), user

    async def go():
        p, c, s = plugin_with(tmp_path)
        p.cpu_usage = main.CpuUsage(lambda: next(times))
        return await p.get_sensors(), await p.get_sensors()
    first, second = run(go())
    assert first == {"ok": True, "message": "", "cpuTempC": 55, "fanRpm": 3100, "gpuFanRpm": 2900, "gpuTempC": 49,
                     "cpuUse": None, "gpuUse": 62, "cpuPowerW": 8, "gpuPowerW": 6, "errors": [],
                     "autoTdp": {"on": False, "watts": None, "fps": None, "best": None, "paused": "",
                                 "supported": True}}
    assert list(first) == ["ok", "message", "cpuTempC", "fanRpm", "gpuFanRpm", "gpuTempC", "cpuUse", "gpuUse",
                           "cpuPowerW", "gpuPowerW", "errors", "autoTdp"]
    assert second["cpuUse"] == 75          # 200 of 200 total busy minus 50 idle -> 150/200


def test_get_sensors_passes_missing_gpu_readings_as_none(tmp_path):
    class Client(FakeClient):
        async def call(self, cmd, args=None, timeout=10.0):
            if cmd == "sensors":
                return ok({"cpuTempC": 50, "fanRpm": 3000, "gpuFanRpm": 2800, "gpuTempC": None, "errors": ["x"]})
            return await super().call(cmd, args, timeout)

    async def go():
        p, c, s = plugin_with(tmp_path, client=Client())
        return await p.get_sensors()
    r = run(go())
    assert (r["gpuTempC"], r["gpuUse"], r["cpuPowerW"], r["gpuPowerW"]) == (None, None, None, None)
    assert r["errors"] == ["x"]


def test_plugin_json_runs_as_root():
    with open(main.os.path.join(main.os.path.dirname(main.__file__), "plugin.json"), encoding="utf-8") as f:
        assert "root" in json.load(f)["flags"]


@pytest.fixture(autouse=True)
def _no_real_helper(monkeypatch):
    # Belt and braces: no test in this file may ever spawn a process.
    async def refuse(*a, **k):
        raise AssertionError("tests must not start a process")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse)



# ---------- final review I1: a switch cut short by Decky's 5 s kill ----------

def test_load_restores_interrupted_switch_and_warns(tmp_path):
    async def go():
        c = FakeClient()
        seen = {}

        async def restore(args):
            seen["busy"] = (await p.get_state())["busy"]
            seen["args"] = args
            return ok({"skipped": []})
        c.handlers["armoury_restore"] = restore
        recorded = {"ArmouryCrateSEService": "AutomaticDelayedStart", "LightingService": "Automatic"}
        p, c, s = plugin_with(tmp_path, client=c, armoury_replaced=False, armoury_recorded=dict(recorded),
                              armoury_running=["ArmouryCrateSEService"])
        s.save()
        await p._startup_sequence()
        assert [cmd for cmd, _ in c.calls][:2] == ["armoury_state", "armoury_restore"]
        assert all(cmd != "armoury_stop" for cmd, _ in c.calls)
        assert seen["busy"] is True
        assert seen["args"] == {"recorded": recorded, "running": ["ArmouryCrateSEService"]}
        saved = Settings.load(s.path)
        assert saved.armoury_recorded == {} and saved.armoury_running == [] and saved.armoury_replaced is False
        st = await p.get_state()
        assert st["busy"] is False
        assert "did not finish, so Armoury Crate was put back" in st["warning"]
        assert p.reapplier._task is not None
        await p.reapplier.stop()
    run(go())


def test_load_restore_failure_keeps_record_and_warns(tmp_path):
    async def go():
        c = FakeClient()

        async def restore_fails(args):
            return fail("Could not start AsusAppService (restore): x. Every service changed so far was put back.")
        c.handlers["armoury_restore"] = restore_fails
        recorded = {"ArmouryCrateService": "Automatic"}
        p, c, s = plugin_with(tmp_path, client=c, armoury_recorded=dict(recorded),
                              armoury_running=["ArmouryCrateService"])
        s.save()
        await p._startup_sequence()
        assert Settings.load(s.path).armoury_recorded == recorded
        st = await p.get_state()
        assert "putting the services back failed" in st["warning"] and "AsusAppService" in st["warning"]
        assert "Turn the switch on and off again" in st["warning"]
        await p.reapplier.stop()
    run(go())


def test_load_restore_skipped_when_state_unreadable(tmp_path):
    async def go():
        c = FakeClient()

        async def state_fails(args):
            return fail("Overdrive helper did not answer in 10 s.")
        c.handlers["armoury_state"] = state_fails
        p, c, s = plugin_with(tmp_path, client=c, armoury_recorded={"ArmouryCrateService": "Automatic"})
        await p._startup_sequence()
        assert all(cmd != "armoury_restore" for cmd, _ in c.calls)
        st = await p.get_state()
        assert "did not finish" in st["warning"] and "did not answer" in st["warning"]
        await p.reapplier.stop()
    run(go())


def test_load_without_record_touches_no_service(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        await p._startup_sequence()
        assert all(not cmd.startswith("armoury_") for cmd, _ in c.calls)
        assert (await p.get_state())["warning"] == ""
        await p.reapplier.stop()
    run(go())


# ---------- probe follow-ups: read-back mode "other" and power 0 ----------

def test_get_state_reports_device_readback(tmp_path):
    async def go():
        c = FakeClient()

        async def status(args):
            return ok({"modeRaw": 7, "mode": "other", "spl": 0, "sppt": None, "fppt": 0, "errors": []})
        c.handlers["status"] = status
        p, c, s = plugin_with(tmp_path, client=c)
        st = await p.get_state()
        assert st["device"] == {"mode": "other", "spl": None, "sppt": None, "fppt": None}
        assert st["mode"] == "balanced" and st["user_set"] is False and st["power_set"] is False
        assert c.writes() == []                                   # reading back never writes

        async def status2(args):
            return ok({"modeRaw": 1, "mode": "turbo", "spl": 25, "sppt": 30, "fppt": 35, "errors": []})
        c.handlers["status"] = status2
        assert (await p.get_state())["device"] == {"mode": "turbo", "spl": 25, "sppt": 30, "fppt": 35}

        assert (await p.set_mode("other"))["ok"] is False             # never written back
        assert all(cmd != "set_mode" for cmd, _ in c.calls)
        assert (await p.set_mode("manual", 15, 15, 15))["ok"] is True
        assert (await p.get_state())["power_set"] is True
    run(go())


def test_get_state_skips_readback_while_busy(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        c.armoury_inflight = 1
        st = await p.get_state()
        assert st["busy"] is True and st["device"]["mode"] is None
        assert all(cmd != "status" for cmd, _ in c.calls)
    run(go())


def test_stored_mode_other_is_not_loaded(tmp_path):
    path = tmp_path / "overdrive.json"
    path.write_text(json.dumps({"mode": "other"}), encoding="utf-8")
    assert Settings.load(str(path)).mode == "balanced"


# ---------- live finding: an unreadable settings file must not lose the Armoury record ----------

def test_unreadable_settings_recover_record_from_bak_and_restore_on_load(monkeypatch, tmp_path):
    path = tmp_path / "overdrive.json"
    recorded = {"ArmouryCrateSEService": "AutomaticDelayedStart", "LightingService": "Automatic"}
    s = Settings.load(str(path))
    s.armoury_recorded, s.armoury_running = dict(recorded), ["ArmouryCrateSEService"]   # a switch cut short
    s.save()
    path.write_bytes(b"\xef\xbb\xbf{ broken")
    c = FakeClient()
    monkeypatch.setattr(main.device, "is_ally", lambda read_product=None: True)
    monkeypatch.setattr(main.device, "product_name", lambda read_product=None: "ROG Ally RC71L")
    monkeypatch.setattr(main, "HelperClient", lambda *a, **k: c)
    monkeypatch.setattr(main, "settings_path", lambda: str(path))

    async def go():
        p = main.Plugin()
        await p._main()
        await p._startup
        st = await p.get_state()
        await p._unload()
        return st
    st = run(go())
    copies = [x.name for x in tmp_path.iterdir() if x.name.startswith("overdrive.json.unreadable-")]
    assert len(copies) == 1 and (tmp_path / copies[0]).read_bytes() == b"\xef\xbb\xbf{ broken"
    # the load-time restore (I1) ran with the recovered record, then the cleared record was saved
    restores = [args for cmd, args in c.calls if cmd == "armoury_restore"]
    assert restores == [{"recorded": recorded, "running": ["ArmouryCrateSEService"]}]
    assert Settings.load(str(path)).armoury_recorded == {}
    assert (f"Overdrive settings could not be read and were reset; a copy was kept as {copies[0]}."
            in st["warning"])
    assert "recovered from overdrive.json.bak" in st["warning"]
    assert "did not finish, so Armoury Crate was put back" in st["warning"]


# ---------- GPU fan curve ----------

GPU_A = {"temps": [35, 45, 55, 65, 75, 85, 95, 105], "speeds": [5, 15, 25, 35, 45, 55, 65, 75]}


def both_defaults():
    return dict(default_curve={m: main_curve() for m in ("balanced", "turbo", "silent")},
                default_curve_gpu={m: dict(GPU_CURVE) for m in ("balanced", "turbo", "silent")})


def test_set_fan_curve_gpu_writes_gpu_and_keeps_cpu_as_it_runs(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, fan_preset="cool", **both_defaults())
        cool_cpu = main.preset_curve("cool", main_curve())[0]
        r = await p.set_fan_curve(GPU_A["temps"], GPU_A["speeds"], "gpu")
        assert r["ok"] is True
        assert c.writes() == [("set_fans", {"temps": GPU_A["temps"], "speeds": GPU_A["speeds"], "fan": "gpu"})]
        assert (s.fan_preset, s.fan_curve_gpu, s.fan_curve) == ("custom", GPU_A, cool_cpu)   # CPU keeps "cool"
        saved = Settings.load(s.path)
        assert (saved.fan_preset, saved.fan_curve_gpu, saved.fan_curve) == ("custom", GPU_A, cool_cpu)
        st = await p.get_state()
        assert (st["fan_curve"], st["fan_curve_gpu"]) == (cool_cpu, GPU_A)

        # A later CPU edit (already custom) leaves the GPU custom curve alone.
        c.calls.clear()
        cpu = {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [10, 20, 30, 40, 50, 60, 70, 80]}
        assert (await p.set_fan_curve(cpu["temps"], cpu["speeds"]))["ok"] is True
        assert c.writes() == [("set_fans", cpu)]
        assert (s.fan_curve, s.fan_curve_gpu) == (cpu, GPU_A)
    run(go())


def test_set_fan_curve_from_default_preset_leaves_other_fan_on_firmware(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, fan_curve_gpu=dict(GPU_A), **both_defaults())   # stale GPU custom curve
        cpu = {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [10, 20, 30, 40, 50, 60, 70, 80]}
        assert (await p.set_fan_curve(cpu["temps"], cpu["speeds"], "cpu"))["ok"] is True
        assert s.fan_curve_gpu is None      # the GPU fan ran its firmware curve, and still does
        c.calls.clear()
        await p.reapplier.apply_all()
        assert [a.get("fan") for cmd, a in c.writes() if cmd == "set_fans"] == [None]
    run(go())


def test_set_fan_curve_gpu_validation_and_bad_fan(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, **both_defaults())
        r = await p.set_fan_curve([30, 40, 50, 60, 70, 75, 80, 100], [0, 0, 0, 0, 0, 6, 12, 30], "gpu")
        assert r == {"ok": False, "message": "Point 8: at 100 °C the fan must be at least 36 %."}
        assert (await p.set_fan_curve(GPU_A["temps"], GPU_A["speeds"], "mid")) == \
            {"ok": False, "message": "Fan must be cpu or gpu."}
        assert c.writes() == [] and s.fan_curve_gpu is None and s.fan_preset == "default"
    run(go())


def test_set_fan_curve_gpu_refused_keeps_settings(tmp_path):
    async def go():
        c = FakeClient()

        async def refuse(args):
            return fail("The device refused the GPU fan curve (result 0).")
        c.handlers["set_fans"] = refuse
        p, c, s = plugin_with(tmp_path, client=c, **both_defaults())
        r = await p.set_fan_curve(GPU_A["temps"], GPU_A["speeds"], "gpu")
        assert r == {"ok": False, "message": "The device refused the GPU fan curve (result 0)."}
        assert (s.fan_preset, s.fan_curve, s.fan_curve_gpu) == ("default", None, None)
    run(go())


def test_preset_with_unknown_gpu_default_applies_cpu_and_says_so(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, default_curve={m: main_curve() for m in ("balanced", "turbo", "silent")})
        r = await p.set_fan_preset("quiet")
        assert r["ok"] is True
        assert r["message"] == ("The device's default GPU fan curve is not known yet, so the fan preset was not "
                                "applied to the GPU fan.")
        # the GPU fan is put back on its firmware curve by the mode write, then the CPU's quiet curve goes on
        assert [(cmd, a.get("fan")) for cmd, a in c.writes()] == [("set_mode", None), ("set_fans", None)]
        assert s.fan_preset == "quiet"
    run(go())


def test_preset_with_unknown_cpu_default_changes_nothing(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, default_curve_gpu={m: dict(GPU_CURVE) for m in ("balanced", "turbo", "silent")})
        r = await p.set_fan_preset("cool")
        assert r == {"ok": False,
                     "message": "The device's default fan curve is not known yet, so the fan preset was not applied."}
        assert c.writes() == [] and s.fan_preset == "default"
    run(go())


def test_custom_preset_needs_a_custom_curve_on_either_fan(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, **both_defaults())
        assert (await p.set_fan_preset("custom"))["ok"] is False
        s.fan_curve_gpu = dict(GPU_A)
        assert (await p.set_fan_preset("custom"))["ok"] is True
        # GPU custom, CPU without one: the CPU's firmware curve comes back with the mode write
        assert [(cmd, a.get("fan")) for cmd, a in c.writes()] == [("set_mode", None), ("set_fans", "gpu")]
    run(go())


def test_mode_change_and_reset_rewrite_both_fans(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, fan_preset="cool", **both_defaults())
        assert (await p.set_mode("turbo"))["ok"] is True
        assert [(cmd, a.get("fan")) for cmd, a in c.writes()] == \
            [("set_mode", None), ("set_fans", None), ("set_fans", "gpu")]
        c.calls.clear()
        assert (await p.reset_defaults())["ok"] is True
        assert c.writes() == [("set_mode", {"mode": "balanced", "spl": 17, "sppt": 17, "fppt": 17}), ("set_fps_limit", {"fps": 0})]
    run(go())


def test_get_state_gpu_curve_follows_preset(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, fan_preset="quiet", **both_defaults())
        st = await p.get_state()
        assert st["fan_curve_gpu"] == main.preset_curve("quiet", GPU_CURVE)[0]
        assert st["fan_curve"] == main.preset_curve("quiet", main_curve())[0]
        s.fan_preset = "default"
        st = await p.get_state()
        assert st["fan_curve_gpu"] == GPU_CURVE and st["fan_note"] == ""
    run(go())


def test_get_sensors_without_helper_has_gpu_field():
    r = asyncio.run(main.Plugin().get_sensors())
    assert r["gpuFanRpm"] is None and r["ok"] is False


RECOMMENDED = {"temps": [40, 50, 60, 65, 70, 75, 80, 90], "speeds": [10, 15, 25, 35, 45, 55, 70, 90]}


def test_recommended_preset_writes_one_curve_to_both_fans(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)                    # no device curves read yet: not needed
        r = await p.set_fan_preset("recommended")
        assert r["ok"] is True, r
        assert [(cmd, a.get("fan")) for cmd, a in c.writes()] == [("set_fans", None), ("set_fans", "gpu")]
        for _, a in c.writes():
            assert (a["temps"], a["speeds"]) == (RECOMMENDED["temps"], RECOMMENDED["speeds"])
        assert s.fan_preset == "recommended"
        st = await p.get_state()
        assert st["fan_preset"] == "recommended"
        assert st["fan_curve"] == RECOMMENDED and st["fan_curve_gpu"] == RECOMMENDED
        assert st["fan_note"] == ""
        saved = Settings.load(s.path)
        assert saved.fan_preset == "recommended"
        # Editing one fan from Recommended keeps the other fan on the Recommended curve
        c.calls.clear()
        temps, speeds = [30, 40, 50, 60, 70, 80, 90, 100], [10, 20, 30, 40, 50, 60, 70, 80]
        assert (await p.set_fan_curve(temps, speeds, "gpu"))["ok"] is True
        assert (s.fan_preset, s.fan_curve) == ("custom", RECOMMENDED)
        assert (await p.set_fan_preset("loud"))["message"] == \
            "Fan preset must be default, recommended, quiet, cool or custom."
    run(go())


# ---------- Auto TDP (task 5) ----------

UNSUPPORTED = "Auto TDP needs the AMD driver's frame-rate reading, which this driver does not offer."
STOPPED_STATUS = {"on": False, "watts": None, "fps": None, "best": None, "paused": "", "supported": False}


def wait(coro, secs=2.0):
    """A deadlock on the reapplier's lock fails the test instead of hanging it."""
    return asyncio.wait_for(coro, secs)


def loop_task_writing(loop, rp, watts):
    """The loop's own task is in the middle of a set_power write (as when it decided on new watts)."""
    loop._task.cancel()
    loop._task = asyncio.ensure_future(loop._write(watts, loop._epoch, (rp.generation, rp.applies)))
    return loop._task


def blocked_power(c):
    """set_power never answers: a loop write holds the reapplier's lock until it is cancelled."""
    entered = asyncio.Event()

    async def handler(_args):
        entered.set()
        await asyncio.Event().wait()
    c.handlers["set_power"] = handler
    return entered


async def no_frame_metrics(_args):
    return ok({"supported": False, "fps": None, "gpuPowerW": None, "errors": []})


def test_set_auto_tdp_saves_and_starts(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, mode="balanced", fps=60)
        assert s.user_set is False
        r = await wait(p.set_auto_tdp(True))
        assert r["ok"] is True, r
        saved = Settings.load(s.path)
        assert (saved.auto_tdp, saved.user_set) == (True, True)       # R9: a user choice, so reapplies run
        st = p.auto_tdp_loop.status()
        assert st["on"] is True and st["watts"] == 17                  # starts at the mode's watts
        assert c.writes() == []                                       # starting writes nothing
        assert (await wait(p.set_auto_tdp("yes")))["ok"] is False
        await wait(p.auto_tdp_loop.stop(restore=False))
    run(go())


def test_set_auto_tdp_refuses_like_other_writes(tmp_path):
    async def go():
        assert (await main.Plugin().set_auto_tdp(True)) == {"ok": False, "message": main.NOT_ALLY}
        p, c, s = plugin_with(tmp_path)
        p._armoury_busy = True
        assert (await p.set_auto_tdp(True)) == {"ok": False, "message": main.BUSY}
        p._armoury_busy = False
        assert s.auto_tdp is False
        c.handlers["frame_sample"] = no_frame_metrics
        p.auto_tdp_loop.start()
        await wait(p.auto_tdp_loop.run_once())                        # the driver has no frame metrics
        assert p.auto_tdp_loop.status()["supported"] is False
        r = await wait(p.set_auto_tdp(True))
        assert r == {"ok": False, "message": UNSUPPORTED}
        assert Settings.load(s.path).auto_tdp is False
    run(go())


def test_set_auto_tdp_off_restores_mode(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, mode="turbo", auto_tdp=True, user_set=True)
        loop = p.auto_tdp_loop
        loop.start()
        loop.ctl.watts = 20                                           # trimmed by Auto TDP
        r = await wait(p.set_auto_tdp(False))
        assert r["ok"] is True, r
        assert c.writes() == [("set_mode", {"mode": "turbo", "spl": 35, "sppt": 35, "fppt": 35})]
        assert loop.status()["on"] is False
        assert Settings.load(s.path).auto_tdp is False
    run(go())


def test_set_auto_tdp_off_while_a_loop_write_holds_the_lock(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, mode="silent", auto_tdp=True, user_set=True)
        loop, rp = p.auto_tdp_loop, p.reapplier
        loop.start()
        entered = blocked_power(c)
        write = loop_task_writing(loop, rp, 10)
        await wait(entered.wait())
        assert rp.lock.locked()
        r = await wait(p.set_auto_tdp(False))                        # cancels the write, then restores: no deadlock
        assert r["ok"] is True, r
        assert c.writes()[-1] == ("set_mode", {"mode": "silent", "spl": 13, "sppt": 13, "fppt": 13})
        assert write.done()                                           # stop() cancelled it
    run(go())


def test_mode_change_resets_loop(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, mode="balanced", fps=0, auto_tdp=True, user_set=True)
        loop = p.auto_tdp_loop
        loop.start()
        loop.ctl.watts = 9
        assert (await wait(p.set_mode("turbo")))["ok"] is True
        assert (loop.ctl.ceiling, loop.ctl.watts) == (35, 35)
        loop.ctl.watts = 9
        assert (await wait(p.set_mode("manual", 22, 25, 30)))["ok"] is True   # the Manual sliders
        assert (loop.ctl.ceiling, loop.ctl.watts) == (22, 22)
        loop.ctl.watts = 9
        assert (await wait(p.set_fps(60)))["ok"] is True
        assert (loop.ctl.fps_limit, loop.ctl.watts) == (60, 22)
        loop.ctl.watts = 9
        assert (await wait(p.set_fan_preset("default")))["ok"] is True   # no device curve known: rewrites the mode
        assert c.writes()[-1][0] == "set_mode" and loop.ctl.watts == 22
        # the reapplier's own reapply resets it through on_applied
        assert p.reapplier.on_applied == loop.reset
        loop.ctl.watts = 9
        assert await wait(p.reapplier.apply_all()) is True
        assert loop.ctl.watts == 22
        # a refused write leaves it alone
        loop.ctl.watts = 9

        async def refuse(_args):
            return fail("refused")
        c.handlers["set_mode"] = refuse
        assert (await wait(p.set_mode("turbo")))["ok"] is False
        assert loop.ctl.watts == 9
        await wait(loop.stop(restore=False))
    run(go())


def test_reset_defaults_turns_auto_tdp_off(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, mode="turbo", fps=60, auto_tdp=True, user_set=True)
        loop, rp = p.auto_tdp_loop, p.reapplier
        loop.start()
        entered = blocked_power(c)                                    # a loop write is in flight, holding the lock
        write = loop_task_writing(loop, rp, 20)
        await wait(entered.wait())
        r = await wait(p.reset_defaults())
        assert r["ok"] is True, r
        # no second restore: only Balanced and FPS off after the interrupted loop write
        assert [w for w in c.writes() if w[0] != "set_power"] == [
            ("set_mode", {"mode": "balanced", "spl": 17, "sppt": 17, "fppt": 17}), ("set_fps_limit", {"fps": 0})]
        assert loop.status()["on"] is False
        saved = Settings.load(s.path)
        assert (saved.auto_tdp, saved.mode, saved.fps) == (False, "balanced", 0)
        assert write.done()                                           # stop() cancelled it
    run(go())


def test_unload_restores_mode_watts(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, mode="silent", auto_tdp=True, user_set=True)
        at_stop = []

        async def stop():
            at_stop.append(list(c.writes()))
            c.stopped = True
        c.stop = stop
        p._startup = asyncio.ensure_future(p._startup_sequence())
        await wait(p._startup)
        loop = p.auto_tdp_loop
        assert loop.status()["on"] is True                            # started at load
        await wait(p.reapplier.apply_all())                          # a reapply ran
        await c.call("set_power", {"spl": 9, "sppt": 9, "fppt": 9})   # Auto TDP trimmed the watts
        await wait(p._unload())
        assert c.stopped and at_stop
        assert at_stop[0][-1] == ("set_mode", {"mode": "silent", "spl": 13, "sppt": 13, "fppt": 13})
        assert loop.status()["on"] is False and p.reapplier._task is None
    run(go())


def test_unload_without_auto_tdp_writes_nothing_more(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, mode="silent")
        p._startup = asyncio.ensure_future(p._startup_sequence())
        await wait(p._startup)
        assert p.auto_tdp_loop.status()["on"] is False
        await wait(p.reapplier.apply_all())
        before = list(c.writes())
        await wait(p._unload())
        assert c.writes() == before and c.stopped
    run(go())


def test_state_and_sensors_report_auto_tdp(tmp_path, monkeypatch):
    monkeypatch.setattr(main.device, "product_name", lambda read_product=None: "ROG Ally RC71L")

    async def go():
        bare = main.Plugin()
        assert (await bare.get_state())["auto_tdp"] == {"on": False, "supported": False}
        assert (await bare.get_sensors())["autoTdp"] == STOPPED_STATUS

        p, c, s = plugin_with(tmp_path, auto_tdp=True, user_set=True)
        assert (await p.get_state())["auto_tdp"] == {"on": True, "supported": True}
        p.auto_tdp_loop.start()
        sensors = await p.get_sensors()
        assert sensors["autoTdp"] == p.auto_tdp_loop.status()
        assert sensors["autoTdp"]["on"] is True and sensors["autoTdp"]["watts"] == 17
        c.handlers["frame_sample"] = no_frame_metrics
        await wait(p.auto_tdp_loop.run_once())
        # R8: the stored switch stays on; the panel shows why from the sensors
        assert (await p.get_state())["auto_tdp"] == {"on": True, "supported": False}
        assert (await p.get_sensors())["autoTdp"]["supported"] is False
    run(go())
