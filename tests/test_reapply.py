import asyncio

from fake_helper import FakeClient, ok
from overdrive.reapply import Reapplier
from overdrive.settings import Settings

LOW = {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [0, 0, 5, 8, 10, 11, 12, 20]}


class Clock:
    def __init__(self):
        self.wall = 1_700_000_000.0
        self.mono = 5_000.0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def step(self, wall, mono=None):
        self.wall += wall
        self.mono += wall if mono is None else mono


class Power:
    def __init__(self, value=True):
        self.value = value

    def __call__(self):
        return self.value


def make(tmp_path, user_set=True, **fields):
    s = Settings.load(str(tmp_path / "overdrive.json"))
    s.user_set = user_set
    for k, v in fields.items():
        setattr(s, k, v)
    return s


def run(coro):
    return asyncio.run(coro)


def test_start_reads_default_curves_once_and_applies(tmp_path):
    async def go():
        c = FakeClient()
        s = make(tmp_path, mode="manual", spl=10, sppt=20, fppt=30, fps=60)
        r = Reapplier(c, s, clock=Clock(), power_source=Power())
        await r.apply_all()
        assert [a for cmd, a in c.calls if cmd == "get_fans"] == [{"mode": m} for m in ("balanced", "turbo", "silent")]
        assert set(Settings.load(s.path).default_curve) == {"balanced", "turbo", "silent"}
        assert c.writes() == [("set_mode", {"mode": "manual", "spl": 10, "sppt": 20, "fppt": 30}),
                              ("set_fps_limit", {"fps": 60})]
        c.calls.clear()
        await r.apply_all()
        assert all(cmd != "get_fans" for cmd, _ in c.calls)   # read on first run only
    run(go())


def test_presets_are_written_after_mode(tmp_path):
    async def go():
        c = FakeClient(curves={m: LOW for m in ("balanced", "turbo", "silent")})
        s = make(tmp_path, mode="turbo")
        await Reapplier(c, s, clock=Clock(), power_source=Power()).apply_all()
        cmds = [cmd for cmd, _ in c.writes()]
        assert cmds == ["set_mode", "set_fans"]       # LOW is below the minimum, so "default" is written raised
        assert c.writes()[1][1]["speeds"][-1] == 36
    run(go())


def test_untouched_install_only_reads(tmp_path):
    async def go():
        c = FakeClient()
        s = make(tmp_path, user_set=False)
        await Reapplier(c, s, clock=Clock(), power_source=Power()).apply_all()
        assert c.writes() == []
        assert s.default_curve is not None
    run(go())


def test_reapply_on_ac_change(tmp_path):
    async def go():
        c = FakeClient()
        power = Power(True)
        r = Reapplier(c, make(tmp_path), clock=Clock(), power_source=power)
        r.prime()
        assert await r.tick() is False
        power.value = False
        assert await r.tick() is True
        assert [cmd for cmd, _ in c.writes()] == ["set_mode"]
        assert await r.tick() is False
        power.value = None            # unknown reading is not a change
        assert await r.tick() is False
        power.value = True
        assert await r.tick() is True
    run(go())


def test_reapply_on_resume_gap(tmp_path):
    async def go():
        c = FakeClient()
        clock = Clock()
        r = Reapplier(c, make(tmp_path), clock=clock, power_source=Power())
        r.prime()
        clock.step(5)
        assert await r.tick() is False
        clock.step(3600, mono=5)       # wall clock jumped, monotonic did not: the PC slept
        assert await r.tick() is True
        clock.step(5)
        assert await r.tick() is False
        clock.step(600, mono=600)      # both jumped: the loop itself was suspended (monotonic counts sleep)
        assert await r.tick() is True
    run(go())


def test_reapply_does_not_overwrite_newer_user_change(tmp_path):
    async def go():
        c = FakeClient()
        s = make(tmp_path, mode="balanced", fps=60)
        r = Reapplier(c, s, clock=Clock(), power_source=Power())
        await r.apply_all()                    # first run: curves read
        c.calls.clear()

        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_set_mode(args):
            if args["mode"] == "balanced":     # the reapply's stale value
                entered.set()
                await release.wait()
            return ok(dict(args))
        c.handlers["set_mode"] = slow_set_mode

        reapply = asyncio.ensure_future(r.apply_all())
        await entered.wait()

        async def user():
            async with r.user_change():
                reply = await c.call("set_mode", {"mode": "turbo"})
                s.mode = "turbo"
                return reply
        user_task = asyncio.ensure_future(user())
        await asyncio.sleep(0.05)
        release.set()
        await asyncio.gather(reapply, user_task)

        modes = [a["mode"] for cmd, a in c.calls if cmd == "set_mode"]
        assert modes == ["balanced", "turbo"]                     # the user's value lands last
        assert c.calls[-1] == ("set_mode", {"mode": "turbo"})     # nothing from the stale reapply after it
        assert r.rerun is True                                    # the dropped reapply is redone with fresh values

        c.calls.clear()
        c.handlers.clear()
        await r.tick()
        assert ("set_mode", {"mode": "turbo"}) in c.calls
        assert r.rerun is False
    run(go())


def test_user_change_before_step_drops_reapply(tmp_path):
    async def go():
        c = FakeClient()
        s = make(tmp_path, default_curve={m: LOW for m in ("balanced", "turbo", "silent")})
        r = Reapplier(c, s, clock=Clock(), power_source=Power())
        gen = r.generation
        async with r.user_change():
            assert r.generation == gen + 1
            task = asyncio.ensure_future(r.apply_all())
            await asyncio.sleep(0.05)
            assert c.writes() == []            # blocked while the user change holds the lock
        await task
        assert c.writes() != []                # started after the bump, so it runs with current values
    run(go())


def test_failed_reapply_sets_warning(tmp_path):
    async def go():
        c = FakeClient()

        async def refuse(args):
            return {"id": 1, "ok": False, "message": "The device refused the performance mode (result 0).", "data": None}
        c.handlers["set_mode"] = refuse
        s = make(tmp_path, mode="silent")
        r = Reapplier(c, s, clock=Clock(), power_source=Power())
        await r.apply_all()
        assert "refused the performance mode" in r.warning
        assert s.mode == "silent"               # stored value kept
        del c.handlers["set_mode"]
        await r.apply_all()
        assert r.warning == ""
    run(go())


def test_start_and_stop_poll_loop(tmp_path):
    async def go():
        c = FakeClient()
        r = Reapplier(c, make(tmp_path), clock=Clock(), power_source=Power(), poll=0.01)
        r.start()
        for _ in range(100):
            if any(cmd == "set_mode" for cmd, _ in c.calls):
                break
            await asyncio.sleep(0.01)
        assert any(cmd == "set_mode" for cmd, _ in c.calls)      # applied on start
        await r.stop()
        assert r._task is None
    run(go())
