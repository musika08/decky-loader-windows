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
        r.on_applied = loop.reset
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
        loop.reset()
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
        r.on_applied = loop.reset
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
