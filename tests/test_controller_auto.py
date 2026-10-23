import asyncio

from fake_helper import FakeClient, fail, ok
from overdrive.controller_auto import ControllerAuto
from overdrive.reapply import Reapplier
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


class FakeTdp:
    def __init__(self):
        self.last_sample = None


def setup(tmp_path, autotdp=None, logger=None, sleep=never):
    c = FakeClient()
    s = Settings.load(str(tmp_path / "overdrive.json"))
    r = Reapplier(c, s, clock=Clock())
    clock = Clock()
    ca = ControllerAuto(c, r, autotdp, clock=clock, sleep=sleep, logger=logger)
    return c, r, ca, clock


def front(c, exes):
    """foreground replies: the next name from the list each call (the last one repeats)."""
    seq = list(exes)

    async def handler(_args):
        exe = seq.pop(0) if len(seq) > 1 else seq[0]
        return ok({"exe": exe})
    c.handlers["foreground"] = handler


def frames(c, fps):
    async def handler(_args):
        return ok({"supported": True, "fps": fps, "gpuPowerW": 5, "errors": []})
    c.handlers["frame_sample"] = handler


def mode_writes(c):
    return [a["mode"] for cmd, a in c.calls if cmd == "set_controller_mode"]


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


def test_decide_rules(tmp_path):
    _, _, ca, _ = setup(tmp_path)
    assert ca.decide("steam.exe", 0.0) == "gamepad"
    assert ca.decide("steamwebhelper.exe", None) == "gamepad"
    assert ca.decide("SteamWebHelper.EXE", 0.0) == "gamepad"
    assert ca.decide("game.exe", 60.0) == "gamepad"
    assert ca.decide("msedge.exe", 0.0) == "desktop"
    assert ca.decide("msedge.exe", None) == "gamepad"          # unknown frame rate: every doubt decides Gamepad
    assert ca.decide("msedge.exe", float("nan")) == "gamepad"
    assert ca.decide("msedge.exe", float("inf")) == "gamepad"
    assert ca.decide("game.exe", None) == "gamepad"
    assert ca.decide("", 0.0) == "gamepad"   # no window (lock screen): safe default


def test_switch_after_three(tmp_path):
    async def go():
        c, r, ca, _ = setup(tmp_path)
        ca.start()
        front(c, ["msedge.exe"])
        frames(c, 0.0)
        await ca.run_once()
        await ca.run_once()
        front(c, ["steam.exe"])
        await ca.run_once()
        assert mode_writes(c) == []          # 2 desktop then gamepad: the streak broke
        await ca.run_once()
        await ca.run_once()
        assert mode_writes(c) == ["gamepad"]   # first streak of 3 writes even if it matches the start
        await ca.run_once()
        assert mode_writes(c) == ["gamepad"]   # only a change is written
        front(c, ["msedge.exe"])
        for _ in range(3):
            await ca.run_once()
        assert mode_writes(c) == ["gamepad", "desktop"]
        for _ in range(5):
            await ca.run_once()
        assert mode_writes(c) == ["gamepad", "desktop"]
        assert r.generation == 0              # loop writes are not user changes
        await ca.stop()
    run(go())


def test_auto_steam_overlay_keeps_gamepad(tmp_path):
    async def go():
        c, _, ca, _ = setup(tmp_path)
        ca.start()
        # A game drawing frames, then Steam's overlay/keyboard in front (steamwebhelper.exe) while the frame reading
        # fails: the Steam rule wins, never Desktop.
        front(c, ["game.exe", "game.exe", "game.exe", "steamwebhelper.exe"])
        frames(c, 60.0)
        for _ in range(3):
            await ca.run_once()

        async def bad(_args):
            return fail("no reading")
        c.handlers["frame_sample"] = bad
        for _ in range(6):
            await ca.run_once()
        assert mode_writes(c) == ["gamepad"]
        await ca.stop()
    run(go())


def test_unknown_frame_rate_decides_gamepad(tmp_path):
    replies = {
        "fps None": ok({"supported": True, "fps": None, "gpuPowerW": None, "errors": ["no reading"]}),
        "failed reply": fail("Overdrive helper did not answer in time."),
        "supported false": ok({"supported": False, "fps": None, "gpuPowerW": None, "errors": []}),
        "supported false with 0": ok({"supported": False, "fps": 0.0, "gpuPowerW": None, "errors": []}),
        "not finite": ok({"supported": True, "fps": float("inf"), "gpuPowerW": 5, "errors": []}),
    }
    for what, rep in replies.items():
        async def go():
            c, _, ca, _ = setup(tmp_path)
            ca.start()
            front(c, ["game.exe"])

            async def handler(_args):
                return rep
            c.handlers["frame_sample"] = handler
            for _ in range(6):
                await ca.run_once()
            assert mode_writes(c) == ["gamepad"], what
            await ca.stop()
        run(go())


def test_valid_zero_fps_with_another_app_decides_desktop(tmp_path):
    async def go():
        tdp = FakeTdp()
        c, _, ca, clock = setup(tmp_path, autotdp=tdp)
        ca.start()
        front(c, ["msedge.exe"])
        frames(c, 0.0)
        for _ in range(3):
            await ca.run_once()
        assert mode_writes(c) == ["desktop"]
        # Auto TDP's fresh sample: a non-finite value is unknown (Gamepad), a valid 0 is Desktop.
        tdp.last_sample = (float("nan"), clock.mono)
        for _ in range(3):
            await ca.run_once()
        assert mode_writes(c) == ["desktop", "gamepad"]
        tdp.last_sample = (0.0, clock.mono)
        for _ in range(3):
            await ca.run_once()
        assert mode_writes(c) == ["desktop", "gamepad", "desktop"]
        await ca.stop()
    run(go())


def test_uses_autotdp_sample_when_fresh(tmp_path):
    async def go():
        tdp = FakeTdp()
        c, _, ca, clock = setup(tmp_path, autotdp=tdp)
        ca.start()
        front(c, ["game.exe"])
        tdp.last_sample = (55.0, clock.mono - 0.2)
        for _ in range(3):
            await ca.run_once()
        assert [cmd for cmd, _ in c.calls if cmd == "frame_sample"] == []
        assert mode_writes(c) == ["gamepad"]
        # A stale sample (0.6 s or older) is not used: the loop asks itself, with the short timeout.
        tdp.last_sample = (55.0, clock.mono - 0.6)
        await ca.run_once()
        assert [cmd for cmd, _ in c.calls if cmd == "frame_sample"] == ["frame_sample"]
        assert c.timeouts["frame_sample"] == [2.0]
        await ca.stop()
    run(go())


def test_auto_loop_exception_writes_gamepad(tmp_path):
    class Log:
        def __init__(self):
            self.traces = 0

        def exception(self, _msg):
            self.traces += 1

        def info(self, _msg):
            pass

        warning = info

    async def go():
        log = Log()
        ticks = []
        done = asyncio.Event()

        async def sleep(_secs):
            clock.mono += 0.3
            ticks.append(1)
            if len(ticks) > 12:
                done.set()
                await asyncio.Event().wait()
            await asyncio.sleep(0)
        c, _, ca, clock = setup(tmp_path, logger=log, sleep=sleep)
        front(c, ["msedge.exe"])
        frames(c, 0.0)
        calls = {"n": 0}

        async def flaky(_args):
            calls["n"] += 1
            if calls["n"] >= 5:
                raise RuntimeError("bad reply")
            return ok({"exe": "msedge.exe"})
        c.handlers["foreground"] = flaky
        ca.start()
        await asyncio.wait_for(done.wait(), 5)
        writes = mode_writes(c)
        assert writes[0] == "desktop"           # ticks 1-3 decide Desktop
        assert writes[1] == "gamepad"           # tick 5 raised: Gamepad written, loop kept running
        assert calls["n"] >= 10                 # still sampling after the error
        assert log.traces == 1                  # rate-limited
        assert set(writes[1:]) == {"gamepad"}
        await ca.stop()
    run(go())


def test_skips_while_busy(tmp_path):
    async def go():
        c, _, ca, _ = setup(tmp_path)
        ca.start()
        front(c, ["msedge.exe"])
        frames(c, 0.0)
        c.armoury_inflight = 1
        for _ in range(4):
            await ca.run_once()
        assert c.calls == []                    # nothing asked or written while Armoury Crate is switched
        c.armoury_inflight = 0
        await ca.run_once()
        await ca.run_once()
        orig = c.handlers["frame_sample"]

        async def sample_then_busy(args):
            c.armoury_inflight = 1              # a switch starts between the decision and the write
            return await orig(args)
        c.handlers["frame_sample"] = sample_then_busy
        await ca.run_once()                     # third Desktop decision, but busy at write time: dropped
        assert mode_writes(c) == []
        c.armoury_inflight = 0
        c.handlers["frame_sample"] = orig
        await ca.run_once()                     # the switch made the state unknown: a fresh streak of 3
        await ca.run_once()
        assert mode_writes(c) == []
        await ca.run_once()
        assert mode_writes(c) == ["desktop"]
        await ca.stop()
    run(go())


def test_stop_writes_gamepad_and_ends_task(tmp_path):
    async def go():
        c, _, ca, _ = setup(tmp_path, sleep=lambda _s: asyncio.sleep(0))
        front(c, ["msedge.exe"])
        frames(c, 0.0)
        ca.start()
        for _ in range(50):
            await asyncio.sleep(0)
        await ca.stop()
        assert mode_writes(c)[-1] == "gamepad"
        n = len(c.calls)
        for _ in range(20):
            await asyncio.sleep(0)
        assert len(c.calls) == n                # the loop is gone
    run(go())


def test_write_takes_the_reapplier_lock(tmp_path):
    async def go():
        c, r, ca, _ = setup(tmp_path)
        ca.start()
        front(c, ["msedge.exe"])
        frames(c, 0.0)
        await ca.run_once()
        await ca.run_once()
        await r.lock.acquire()
        t = asyncio.ensure_future(ca.run_once())
        for _ in range(10):
            await asyncio.sleep(0)
        assert mode_writes(c) == []             # waits for the lock
        r.lock.release()
        await asyncio.wait_for(t, 2)
        assert mode_writes(c) == ["desktop"]
        await ca.stop()
    run(go())


async def desktop_written(c, ca):
    front(c, ["msedge.exe"])
    frames(c, 0.0)
    for _ in range(3):
        await ca.run_once()
    assert mode_writes(c) == ["desktop"]


def test_rewrites_after_a_reapply(tmp_path):
    async def go():
        c, r, ca, _ = setup(tmp_path)
        ca.start()
        await desktop_written(c, ca)
        r.applies += 1                          # a reapply (resume, AC, helper restart) may have reset the controller
        await ca.run_once()
        await ca.run_once()
        assert mode_writes(c) == ["desktop"]
        await ca.run_once()
        assert mode_writes(c) == ["desktop", "desktop"]
        for _ in range(3):
            await ca.run_once()
        assert mode_writes(c) == ["desktop", "desktop"]
        await ca.stop()
    run(go())


def test_rewrites_after_an_armoury_switch(tmp_path):
    async def go():
        c, _, ca, _ = setup(tmp_path)
        ca.start()
        await desktop_written(c, ca)
        c.armoury_inflight = 1
        await ca.run_once()
        c.armoury_inflight = 0
        for _ in range(3):
            await ca.run_once()
        assert mode_writes(c) == ["desktop", "desktop"]
        await ca.stop()
    run(go())


def test_no_write_while_a_reapply_runs(tmp_path):
    async def go():
        c, r, ca, _ = setup(tmp_path)
        ca.start()
        front(c, ["msedge.exe"])
        frames(c, 0.0)
        r.applying = 1
        for _ in range(4):
            await ca.run_once()
        assert mode_writes(c) == []             # dropped, not counted as written
        r.applying = 0
        await ca.run_once()
        assert mode_writes(c) == ["desktop"]
        await ca.stop()
    run(go())


def test_failed_gamepad_write_retried_next_tick(tmp_path):
    async def go():
        c, _, ca, _ = setup(tmp_path)
        ca.start()
        refuse = {"n": 1}

        async def set_mode(args):
            if refuse["n"]:
                refuse["n"] -= 1
                return fail("controller busy")
            return ok(dict(args))
        c.handlers["set_controller_mode"] = set_mode
        front(c, ["steam.exe"])
        for _ in range(3):
            await ca.run_once()
        assert mode_writes(c) == ["gamepad"]    # refused
        await ca.run_once()
        assert mode_writes(c) == ["gamepad", "gamepad"]   # retried at once
        # A refused Desktop write waits for another 3 equal decisions.
        refuse["n"] = 1
        front(c, ["msedge.exe"])
        frames(c, 0.0)
        for _ in range(3):
            await ca.run_once()
        assert mode_writes(c) == ["gamepad", "gamepad", "desktop"]
        await ca.run_once()
        await ca.run_once()
        assert len(mode_writes(c)) == 3
        await ca.run_once()
        assert mode_writes(c) == ["gamepad", "gamepad", "desktop", "desktop"]
        await ca.stop()
    run(go())


def test_no_controller_retried_every_five_seconds(tmp_path):
    async def go():
        c, r, ca, clock = setup(tmp_path)
        ca.start()
        found = {"on": False}

        async def set_mode(args):
            return ok(dict(args)) if found["on"] else fail("Controller not found")
        c.handlers["set_controller_mode"] = set_mode
        front(c, ["steam.exe"])
        for _ in range(3):
            await ca.run_once()
        assert mode_writes(c) == ["gamepad"]          # refused: no controller
        for _ in range(10):                           # 3 s of ticks: not tried again
            clock.mono += 0.3
            await ca.run_once()
        assert mode_writes(c) == ["gamepad"]
        clock.mono += 2.1                             # 5 s after the refusal: one more try
        await ca.run_once()
        assert mode_writes(c) == ["gamepad", "gamepad"]
        await ca.run_once()
        assert len(mode_writes(c)) == 2
        found["on"] = True                            # the controller is back: the next try after 5 s writes
        clock.mono += 5.0
        await ca.run_once()
        assert mode_writes(c) == ["gamepad"] * 3
        await ca.run_once()
        assert len(mode_writes(c)) == 3               # written: only a change is written
        # Another refusal (not "not found") keeps the old rule: Gamepad is retried at the next tick.
        found["on"] = False
        c.handlers["set_controller_mode"] = lambda args: _refuse()
        r.applies += 1                                # a reapply: the state is unknown again
        for _ in range(4):
            await ca.run_once()
        assert len(mode_writes(c)) == 5
        await ca.stop()

    async def _refuse():
        return fail("controller busy")
    run(go())
