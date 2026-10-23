import asyncio

from fake_helper import FakeClient, fail, ok
from overdrive.auto_tdp_loop import AutoTdpLoop, ceiling
from overdrive.reapply import Reapplier, mode_write
from overdrive.settings import Settings


class Clock:
    def __init__(self):
        self.mono = 1_000.0

    def time(self):
        return 1_700_000_000.0 + self.mono

    def monotonic(self):
        return self.mono


async def never(_secs):
    await asyncio.Event().wait()


def make(tmp_path, **fields):
    s = Settings.load(str(tmp_path / "overdrive.json"))
    s.user_set = True
    s.auto_tdp = True
    for k, v in fields.items():
        setattr(s, k, v)
    return s


def frames(fps, gpu=5):
    async def handler(_args):
        return ok({"supported": True, "fps": fps, "gpuPowerW": gpu, "errors": []})
    return handler


def setup(tmp_path, **fields):
    c = FakeClient()
    s = make(tmp_path, **fields)
    r = Reapplier(c, s, clock=Clock())
    clock = Clock()
    loop = AutoTdpLoop(c, s, r, clock=clock, sleep=never)
    return c, s, r, loop, clock


def power_writes(c):
    return [a for cmd, a in c.calls if cmd == "set_power"]


def run(coro):
    return asyncio.run(coro)


def test_writes_three_equal_limits(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        loop.start()
        loop.ctl.watts = 13   # trimmed earlier
        c.handlers["frame_sample"] = frames(40.0)   # far below 60: climb +2 W from 13
        await loop.run_once()
        assert c.writes() == [("set_power", {"spl": 15, "sppt": 15, "fppt": 15})]
        assert loop.status()["watts"] == 15
        assert loop.status()["fps"] == 40.0
        assert r.generation == 0   # a loop write is not a user change
        await loop.stop(restore=False)
    run(go())


def test_ceiling_per_mode(tmp_path):
    s = make(tmp_path)
    for mode, w in (("silent", 13), ("balanced", 17), ("turbo", 35)):
        s.mode = mode
        assert ceiling(s) == w
    s.mode, s.spl = "manual", 22
    assert ceiling(s) == 22


def test_start_writes_nothing_and_reports_status(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=0)
        assert loop.status() == {"on": False, "watts": None, "fps": None, "best": None, "paused": "",
                                 "supported": True}
        loop.start()
        assert c.calls == []
        assert loop.status() == {"on": True, "watts": 17, "fps": None, "best": None, "paused": "",
                                 "supported": True}
        await loop.stop(restore=False)
        assert c.calls == []
        assert loop.status()["on"] is False
    run(go())


def test_stop_restores_mode(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="manual", spl=20, sppt=25, fppt=30, fps=60)
        loop.start()
        await loop.stop()
        assert c.writes() == [("set_mode", mode_write("manual", (20, 25, 30)))]
        assert r.generation == 0
        assert loop.status()["on"] is False and loop.status()["watts"] is None
    run(go())


def test_skips_while_busy(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="silent", fps=60)
        loop.start()
        c.armoury_inflight = 1
        await loop.run_once()
        assert c.calls == []
        await loop.stop(restore=False)
    run(go())


def test_pause_after_three_failures_restores_mode(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        loop.start()
        c.handlers["frame_sample"] = frames(None)
        for _ in range(2):
            await loop.run_once()
        assert c.writes() == [] and loop.status()["paused"] == ""
        await loop.run_once()
        assert c.writes() == [("set_mode", mode_write("balanced"))]
        assert loop.status()["paused"] == "Auto TDP paused: no frame-rate reading"
        assert loop.status()["on"] is True
        assert r.generation == 0
        c.calls.clear()
        await loop.run_once()
        await loop.run_once()
        assert c.calls == []   # no more samples while paused
        loop.reset()
        assert loop.status()["paused"] == ""
        c.handlers["frame_sample"] = frames(40.0)
        await loop.run_once()
        assert [cmd for cmd, _ in c.calls] == ["frame_sample"]   # sampling again, already at the 17 W ceiling
        await loop.stop(restore=False)
    run(go())


def test_failure_kinds_count_and_reason_is_last_message(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="silent", fps=60)
        loop.start()
        replies = iter([fail("Overdrive helper did not answer in time."), fail("ADL call failed"),
                        ok({"supported": True, "fps": float("nan"), "gpuPowerW": 5, "errors": ["Frame metrics off"]})])

        async def handler(_args):
            return next(replies)
        c.handlers["frame_sample"] = handler
        for _ in range(3):
            await loop.run_once()
        assert loop.status()["paused"] == "Auto TDP paused: Frame metrics off"
        assert c.writes() == [("set_mode", mode_write("silent"))]
        await loop.stop(restore=False)
    run(go())


def test_good_sample_clears_failure_count(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="silent", fps=60)
        loop.start()
        loop.after_mode_write()   # the reapply's mode write landed: the device is at the 13 W ceiling
        for fps in (None, None, 60.0, None, None):
            c.handlers["frame_sample"] = frames(fps)
            await loop.run_once()
        assert loop.status()["paused"] == ""
        assert c.writes() == []
        await loop.stop(restore=False)
    run(go())


def test_reapply_hook_resets_and_resumes(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        r.on_applied = loop.after_mode_write
        loop.start()
        loop.ctl.watts = 13
        c.handlers["frame_sample"] = frames(40.0)
        await loop.run_once()
        assert loop.status()["watts"] == 15
        c.handlers["frame_sample"] = frames(None)
        for _ in range(3):
            await loop.run_once()
        assert loop.status()["paused"].startswith("Auto TDP paused:")
        await r.apply_all()
        assert loop.status()["paused"] == ""
        assert loop.status()["watts"] == 17   # restarted at the ceiling
        c.calls.clear()
        c.handlers["frame_sample"] = frames(60.0, gpu=3)
        for _ in range(8):
            clock.mono += 0.3
            await loop.run_once()
        assert power_writes(c) == [{"spl": 16, "sppt": 16, "fppt": 16}]   # sampling and writing again
        await loop.stop(restore=False)
    run(go())


def test_on_applied_not_called_when_dropped(tmp_path):
    async def go():
        c = FakeClient()
        s = make(tmp_path)
        r = Reapplier(c, s, clock=Clock())
        hits = []
        r.on_applied = lambda: hits.append(1)
        await r.apply_all()
        assert hits == [1]

        async def bump(args):
            r.generation += 1
            return ok(dict(args))
        c.handlers["set_mode"] = bump
        s.fps = 60
        assert await r.apply_all() is False
        assert hits == [1]
    run(go())


def test_loop_write_dropped_after_user_change(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="silent", fps=60)
        loop.start()

        async def sample_then_user_change(_args):
            r.generation += 1
            return ok({"supported": True, "fps": 40.0, "gpuPowerW": 5, "errors": []})
        c.handlers["frame_sample"] = sample_then_user_change
        await loop.run_once()
        assert power_writes(c) == []
        await loop.stop(restore=False)
    run(go())


def test_reset_lowers_to_new_ceiling(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="manual", spl=15, sppt=15, fppt=15, fps=60)
        loop.start()
        assert loop.status()["watts"] == 15
        s.spl, s.sppt, s.fppt = 10, 10, 10
        loop.reset(mode_written=True)   # the Sustained slider wrote the mode
        assert loop.status()["watts"] == 10
        c.handlers["frame_sample"] = frames(60.0, gpu=3)   # at the limit, GPU under the cap: trims after 8
        for _ in range(8):
            clock.mono += 0.3
            await loop.run_once()
        assert power_writes(c) == [{"spl": 9, "sppt": 9, "fppt": 9}]
        await loop.stop(restore=False)
    run(go())


def test_unsupported_stops_without_writes(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="silent", fps=60)
        loop.start()

        async def unsupported(_args):
            return fail("The driver has no frame metrics", {"supported": False, "fps": None, "gpuPowerW": None,
                                                             "errors": ["no entry points"]})
        c.handlers["frame_sample"] = unsupported
        await loop.run_once()
        assert c.writes() == []
        st = loop.status()
        assert st["supported"] is False and st["on"] is False
        c.calls.clear()
        await loop.run_once()
        assert c.calls == []
    run(go())


def test_power_refused_stops_and_restores(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="silent", fps=60)
        loop.start()
        loop.ctl.watts = 11
        c.handlers["frame_sample"] = frames(40.0)

        async def refuse(_args):
            return fail("The power limit was refused by the firmware.")
        c.handlers["set_power"] = refuse
        await loop.run_once()
        assert c.writes() == [("set_power", {"spl": 13, "sppt": 13, "fppt": 13}),
                              ("set_mode", mode_write("silent"))]
        st = loop.status()
        assert st["on"] is False
        assert st["paused"] == "The power limit was refused by the firmware."
        c.calls.clear()
        await loop.run_once()
        assert c.calls == []
        loop.reset()   # a reapply while stopped does not restart the loop or clear the message
        assert loop.status()["paused"] == "The power limit was refused by the firmware."
    run(go())


def test_best_is_target_while_trimming_most_frames(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=0)
        loop.start()
        c.handlers["frame_sample"] = frames(72.0)
        for _ in range(9):
            clock.mono += 0.3
            await loop.run_once()
        assert loop.status()["best"] is None   # still measuring
        clock.mono += 0.3
        await loop.run_once()
        assert loop.status()["best"] == 72.0
        await loop.stop(restore=False)
    run(go())


def test_background_task_samples_every_period(tmp_path):
    async def go():
        c = FakeClient()
        s = make(tmp_path, mode="silent", fps=60)
        r = Reapplier(c, s, clock=Clock())
        slept = []
        gate = asyncio.Event()

        async def sleep(secs):
            slept.append(secs)
            if len(slept) > 3:
                gate.set()
                await asyncio.Event().wait()
            await asyncio.sleep(0)
        loop = AutoTdpLoop(c, s, r, clock=Clock(), sleep=sleep)
        loop.start()
        await asyncio.wait_for(gate.wait(), 2.0)
        assert slept[:3] == [0.3, 0.3, 0.3]
        assert len([1 for cmd, _ in c.calls if cmd == "frame_sample"]) == 3
        await loop.stop(restore=False)
    run(go())


def test_loop_write_never_lands_inside_a_reapply(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        r.on_applied = loop.after_mode_write
        loop.start()
        loop.ctl.watts = 13
        c.handlers["frame_sample"] = frames(40.0)
        spawned = []

        async def set_mode(args):
            # While the mode write holds the lock, a loop sample is taken and its write queues for the lock; the
            # lock is free again between the reapply's steps.
            spawned.append(asyncio.ensure_future(loop.run_once()))
            for _ in range(5):
                await asyncio.sleep(0)
            assert r.applying == 1
            return ok(dict(args))
        c.handlers["set_mode"] = set_mode
        assert await r.apply_all() is True
        await spawned[0]
        assert [cmd for cmd, _ in c.writes()] == ["set_mode", "set_fps_limit"]
        assert r.applying == 0 and r.applies == 1
        assert loop.status()["watts"] == 17   # the reapply reset it to the ceiling the mode write set
        await loop.stop(restore=False)
    run(go())


def test_write_sampled_before_a_reapply_is_dropped(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        loop.start()
        loop.ctl.watts = 13

        async def sample_then_reapply(_args):
            r.applies += 1   # a reapply started and finished after this sample
            return ok({"supported": True, "fps": 40.0, "gpuPowerW": 5, "errors": []})
        c.handlers["frame_sample"] = sample_then_reapply
        await loop.run_once()
        assert power_writes(c) == []
        await loop.stop(restore=False)
    run(go())


def test_write_dropped_while_busy_rolls_back(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        loop.start()
        loop.ctl.watts = 13
        loop._written = 13   # the device is at 13 W

        async def sample_then_busy(_args):
            c.armoury_inflight = 1
            return ok({"supported": True, "fps": 40.0, "gpuPowerW": 5, "errors": []})
        c.handlers["frame_sample"] = sample_then_busy
        await loop.run_once()
        assert power_writes(c) == []
        assert loop.status()["watts"] == 13   # rolled back to what the device has
        c.armoury_inflight = 0
        c.handlers["frame_sample"] = frames(40.0)
        await loop.run_once()
        assert power_writes(c) == [{"spl": 15, "sppt": 15, "fppt": 15}]   # the climb is written again
        await loop.stop(restore=False)
    run(go())


def test_write_dropped_after_reset_keeps_new_ceiling(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="manual", spl=20, sppt=20, fppt=20, fps=60)
        loop.start()
        loop.ctl.watts = loop._written = 13
        c.handlers["frame_sample"] = frames(40.0)
        await r.lock.acquire()
        task = asyncio.ensure_future(loop.run_once())
        for _ in range(5):
            await asyncio.sleep(0)
        s.spl = s.sppt = s.fppt = 18
        loop.reset()   # epoch changes while the write waits for the lock
        r.lock.release()
        await task
        assert power_writes(c) == []
        assert loop.status()["watts"] == 18
        await loop.stop(restore=False)
    run(go())


def test_power_transport_failure_rolls_back_and_counts(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        loop.start()
        loop.ctl.watts = loop._written = 13
        c.handlers["frame_sample"] = frames(40.0)

        async def timeout(_args):
            return fail("Overdrive helper did not answer in time.")
        c.handlers["set_power"] = timeout
        await loop.run_once()
        st = loop.status()
        assert st["on"] is True and st["paused"] == "" and st["watts"] == 13
        del c.handlers["set_power"]
        c.calls.clear()
        await loop.run_once()
        assert power_writes(c) == [{"spl": 15, "sppt": 15, "fppt": 15}]
        await loop.stop(restore=False)
    run(go())


def test_failed_restore_asks_for_a_reapply(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        loop.start()
        c.handlers["frame_sample"] = frames(None)

        async def refuse(_args):
            return fail("Overdrive helper did not answer in time.")
        c.handlers["set_mode"] = refuse
        for _ in range(3):
            await loop.run_once()
        assert loop.status()["paused"].startswith("Auto TDP paused:")
        assert r.rerun is True
        r.rerun = False
        await loop.stop()
        assert r.rerun is True
    run(go())


def test_successful_restore_leaves_rerun_alone(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        loop.start()
        await loop.stop()
        assert r.rerun is False
    run(go())


# ---------- final review fixes ----------

def with_quiet_fans(s):
    from fake_helper import CURVE, GPU_CURVE
    s.fan_preset = "quiet"
    s.set_default_curves("cpu", {m: dict(CURVE) for m in ("balanced", "turbo", "silent")})
    s.set_default_curves("gpu", {m: dict(GPU_CURVE) for m in ("balanced", "turbo", "silent")})


def test_restore_writes_fan_curves_after_the_mode(tmp_path):
    from overdrive.reapply import fan_args, fan_plan

    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        with_quiet_fans(s)
        cpu, gpu = fan_plan(s, "cpu")[0], fan_plan(s, "gpu")[0]
        expected = [("set_mode", mode_write("balanced")), ("set_fans", fan_args(cpu, "cpu")),
                    ("set_fans", fan_args(gpu, "gpu"))]
        loop.start()
        await asyncio.wait_for(loop.stop(), 2)          # switched off
        assert c.writes() == expected
        assert r.rerun is False
        c.calls.clear()
        loop.start()
        c.handlers["frame_sample"] = frames(None)
        for _ in range(3):                                # paused
            await asyncio.wait_for(loop.run_once(), 2)
        assert c.writes() == expected
        await asyncio.wait_for(loop.stop(restore=False), 2)
    run(go())


def test_restore_fan_write_failure_asks_for_a_reapply(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        with_quiet_fans(s)

        async def refuse(_args):
            return fail("fan refused")
        c.handlers["set_fans"] = refuse
        loop.start()
        await asyncio.wait_for(loop.stop(), 2)
        assert [cmd for cmd, _ in c.writes()] == ["set_mode", "set_fans", "set_fans"]
        assert r.rerun is True
    run(go())


def test_failed_reapply_mode_write_does_not_reset_or_resume(tmp_path):
    """(a) A reapply whose mode write failed did not put the ceiling on the device."""
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        r.on_applied = loop.after_mode_write
        loop.start()
        loop.ctl.watts = loop._written = 11              # trimmed and confirmed

        async def refuse(_args):
            return fail("mode refused")
        c.handlers["set_mode"] = refuse
        assert await asyncio.wait_for(r.apply_all(), 2) is True
        assert (loop.ctl.watts, loop._written) == (11, 11)   # not reset to 17
        c.handlers["frame_sample"] = frames(None)
        for _ in range(3):
            await asyncio.wait_for(loop.run_once(), 2)
        assert loop.status()["paused"]
        await asyncio.wait_for(r.apply_all(), 2)
        assert loop.status()["paused"]                     # still paused: no successful reapply yet
        del c.handlers["set_mode"]
        await asyncio.wait_for(r.apply_all(), 2)
        assert loop.status()["paused"] == "" and loop._written == 17
        await asyncio.wait_for(loop.stop(restore=False), 2)
    run(go())


def test_switching_on_writes_the_ceiling_when_the_device_watts_are_unknown(tmp_path):
    """(b) Armoury Crate or an earlier session may have left lower watts: the first sample writes the ceiling even
    though the controller wants no change (low fps at the ceiling)."""
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        loop.start()
        c.handlers["frame_sample"] = frames(40.0)
        await asyncio.wait_for(loop.run_once(), 2)
        assert power_writes(c) == [{"spl": 17, "sppt": 17, "fppt": 17}]
        await asyncio.wait_for(loop.run_once(), 2)
        assert len(power_writes(c)) == 1                   # confirmed now: no repeat
        await asyncio.wait_for(loop.stop(restore=False), 2)
    run(go())


def test_failed_pause_restore_leaves_watts_unknown(tmp_path):
    """(c) The pause's restore failed: the device may still be trimmed, so the ceiling is written once sampling
    resumes (here after an FPS limit change, which writes no mode)."""
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        loop.start()
        loop.ctl.watts = loop._written = 9
        c.handlers["frame_sample"] = frames(None)

        async def refuse(_args):
            return fail("mode refused")
        c.handlers["set_mode"] = refuse
        for _ in range(3):
            await asyncio.wait_for(loop.run_once(), 2)
        assert loop.status()["paused"] and loop._written is None and r.rerun is True
        s.fps = 45
        loop.reset()                                       # FPS limit change: no mode write
        c.handlers["frame_sample"] = frames(30.0)          # far below 45: wants a climb, already at the ceiling
        await asyncio.wait_for(loop.run_once(), 2)
        assert power_writes(c) == [{"spl": 17, "sppt": 17, "fppt": 17}]
        await asyncio.wait_for(loop.stop(restore=False), 2)
    run(go())


def test_fps_change_reset_keeps_confirmed_watts(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        loop.start()
        loop.ctl.watts = loop._written = 11
        s.fps = 45
        loop.reset()                                       # no mode write: the device is still at 11 W
        assert loop._written == 11 and loop.ctl.watts == 17
        c.handlers["frame_sample"] = frames(45.0, gpu=10)
        await asyncio.wait_for(loop.run_once(), 2)
        assert power_writes(c) == [{"spl": 17, "sppt": 17, "fppt": 17}]
        await asyncio.wait_for(loop.stop(restore=False), 2)
    run(go())


def test_halted_task_ends_when_switched_on_again_during_its_restore(tmp_path):
    async def go():
        c = FakeClient()
        s = make(tmp_path, mode="silent", fps=60)
        r = Reapplier(c, s, clock=Clock())

        async def tick(_secs):
            await asyncio.sleep(0.005)
        loop = AutoTdpLoop(c, s, r, clock=Clock(), sleep=tick)
        loop.start()
        old = loop._task
        loop._written = 11
        loop.ctl.watts = 11
        c.handlers["frame_sample"] = frames(40.0)
        restoring, release = asyncio.Event(), asyncio.Event()

        async def refuse(_args):
            return fail("The power limit was refused by the firmware.")

        async def slow_mode(args):
            restoring.set()
            await release.wait()
            return ok(dict(args))
        c.handlers["set_power"] = refuse
        c.handlers["set_mode"] = slow_mode
        await asyncio.wait_for(restoring.wait(), 2)        # the old task halted and is restoring the mode
        assert loop.status()["on"] is False
        del c.handlers["set_power"]
        loop.start()                                       # switched on again meanwhile
        new = loop._task
        assert new is not None and new is not old
        release.set()
        await asyncio.wait_for(old, 2)                     # the old task ends instead of sampling on
        assert not new.done()
        await asyncio.wait_for(loop.stop(restore=False), 2)
    run(go())


def test_persistent_sample_error_is_logged_once_a_minute(tmp_path):
    class Log:
        def __init__(self):
            self.traces = 0

        def exception(self, _msg):
            self.traces += 1

        def info(self, _msg):
            pass

        warning = info

    async def go():
        c = FakeClient()
        s = make(tmp_path, mode="silent", fps=60)
        r = Reapplier(c, s, clock=Clock())
        clock = Clock()
        log = Log()
        samples = []
        done = asyncio.Event()

        async def tick(_secs):
            clock.mono += 0.3
            if len(samples) >= 400:                        # 400 samples x 0.3 s = 120 s
                done.set()
                await asyncio.Event().wait()
            await asyncio.sleep(0)
        loop = AutoTdpLoop(c, s, r, clock=clock, sleep=tick, logger=log)

        async def boom(_args):
            samples.append(1)
            raise RuntimeError("bad reply")
        c.handlers["frame_sample"] = boom
        loop.start()
        await asyncio.wait_for(done.wait(), 10)
        assert log.traces == 2                             # at 0 s and at 60 s, not 400 times
        await asyncio.wait_for(loop.stop(restore=False), 2)
    run(go())


def test_last_sample_is_kept_for_controller_auto(tmp_path):
    async def go():
        c, s, r, loop, clock = setup(tmp_path, mode="balanced", fps=60)
        assert loop.last_sample is None
        loop.start()
        c.handlers["frame_sample"] = frames(48.0)
        await loop.run_once()
        assert loop.last_sample == (48.0, clock.mono)
        clock.mono += 0.3
        c.handlers["frame_sample"] = frames(float("nan"))   # a bad reading leaves the last good one
        await loop.run_once()
        assert loop.last_sample == (48.0, clock.mono - 0.3)
        await loop.stop(restore=False)
    run(go())
