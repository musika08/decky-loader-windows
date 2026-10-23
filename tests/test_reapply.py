import asyncio

from fake_helper import GPU_CURVE, FakeClient, ok
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
        assert [a for cmd, a in c.calls if cmd == "get_fans"] == \
            [{"mode": m} for m in ("balanced", "turbo", "silent")] + \
            [{"mode": m, "fan": "gpu"} for m in ("balanced", "turbo", "silent")]
        assert set(Settings.load(s.path).default_curve) == {"balanced", "turbo", "silent"}
        assert Settings.load(s.path).default_curve_gpu == {m: GPU_CURVE for m in ("balanced", "turbo", "silent")}
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
        assert "fan" not in c.writes()[1][1]           # the CPU fan (the helper's default); GPU_CURVE passes as is
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


def test_slow_reapply_is_not_a_resume(tmp_path):
    async def go():
        c = FakeClient()
        clock = Clock()

        async def slow_set_mode(args):
            clock.step(60)                 # a busy helper: the write takes a minute of real time
            return ok(dict(args))
        c.handlers["set_mode"] = slow_set_mode
        power = Power(True)
        r = Reapplier(c, make(tmp_path), clock=clock, power_source=power)
        r.prime()
        power.value = False
        assert await r.tick() is True      # AC change
        applied = []
        for _ in range(5):
            clock.step(5)
            applied.append(await r.tick())
        assert applied == [False] * 5      # no repeat reapplies
        assert [cmd for cmd, _ in c.writes()] == ["set_mode"]
    run(go())


def test_slow_start_apply_is_not_a_resume(tmp_path):
    async def go():
        c = FakeClient()
        clock = Clock()

        async def slow_get_fans(args):
            clock.step(10)
            return ok({"mode": args["mode"], "temps": LOW["temps"], "speeds": LOW["speeds"]})
        c.handlers["get_fans"] = slow_get_fans
        r = Reapplier(c, make(tmp_path), clock=clock, power_source=Power(), poll=0.01)
        r.start()
        for _ in range(100):
            if any(cmd == "set_mode" for cmd, _ in c.calls):
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)          # several polls with no clock movement
        await r.stop()
        assert [cmd for cmd, _ in c.writes()].count("set_mode") == 1
    run(go())


def test_no_reapply_while_armoury_call_runs(tmp_path):
    async def go():
        c = FakeClient()
        s = make(tmp_path, default_curve={m: LOW for m in ("balanced", "turbo", "silent")})
        r = Reapplier(c, s, clock=Clock(), power_source=Power())
        c.armoury_inflight = 1
        assert await r.apply_all() is False
        assert c.calls == [] and r.rerun is True
        c.armoury_inflight = 0
        assert await r.tick() is True
        assert c.writes() and r.rerun is False
    run(go())


def test_deferral_is_logged_once(tmp_path, caplog):
    async def go():
        c = FakeClient()
        s = make(tmp_path, default_curve={m: LOW for m in ("balanced", "turbo", "silent")})
        clock = Clock()
        r = Reapplier(c, s, clock=clock, power_source=Power())
        r.prime()
        busy = [True]
        r.busy_check = lambda: busy[0]
        r.rerun = True
        with caplog.at_level("INFO", logger="overdrive"):
            for _ in range(5):
                clock.step(5)
                assert await r.tick() is False
            msgs = [rec.getMessage() for rec in caplog.records]
            assert msgs.count("Reapply deferred while Armoury Crate services are being switched") == 1
            assert not any(m.startswith("Reapplying") for m in msgs)
            assert c.calls == [] and r.rerun is True
            busy[0] = False
            clock.step(5)
            assert await r.tick() is True
            assert c.writes()
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
                reply = await c.call("set_mode", {"mode": "turbo", "spl": 35, "sppt": 35, "fppt": 35})
                s.mode = "turbo"
                return reply
        user_task = asyncio.ensure_future(user())
        await asyncio.sleep(0.05)
        release.set()
        await asyncio.gather(reapply, user_task)

        modes = [a["mode"] for cmd, a in c.calls if cmd == "set_mode"]
        assert modes == ["balanced", "turbo"]                     # the user's value lands last
        assert c.calls[-1] == ("set_mode", {"mode": "turbo", "spl": 35, "sppt": 35, "fppt": 35})     # nothing from the stale reapply after it
        assert r.rerun is True                                    # the dropped reapply is redone with fresh values

        c.calls.clear()
        c.handlers.clear()
        await r.tick()
        assert ("set_mode", {"mode": "turbo", "spl": 35, "sppt": 35, "fppt": 35}) in c.calls
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


async def _ok_async(args):
    return ok(dict(args))


def test_transport_failure_is_retried_next_poll_a_few_times(tmp_path):
    async def go():
        c = FakeClient()

        async def restarted(args):
            return {"id": None, "ok": False, "message": "Overdrive helper restarted, try again", "data": None}
        c.handlers["set_mode"] = restarted
        s = make(tmp_path, default_curve={m: LOW for m in ("balanced", "turbo", "silent")})
        clock = Clock()
        r = Reapplier(c, s, clock=clock, power_source=Power())
        r.prime()
        await r.apply_all()
        assert r.rerun is True
        ran = []
        for _ in range(5):
            clock.step(5)
            ran.append(await r.tick())
        assert ran == [True, True, True, False, False]    # 3 retries, then it waits for the next trigger
        assert r.rerun is False

        c.handlers["set_mode"] = _ok_async
        clock.step(3600, mono=5)                          # resume
        assert await r.tick() is True and r.rerun is False and r.warning == ""
    run(go())


def test_timeout_is_retried_but_refusal_is_not(tmp_path):
    async def go():
        c = FakeClient()

        async def timeout(args):
            return {"id": None, "ok": False, "message": "Overdrive helper did not answer in 10 s.", "data": None}
        c.handlers["set_fps_limit"] = timeout
        s = make(tmp_path, fps=60, default_curve={m: LOW for m in ("balanced", "turbo", "silent")})
        r = Reapplier(c, s, clock=Clock(), power_source=Power())
        await r.apply_all()
        assert r.rerun is True

        async def refused(args):
            return {"id": 1, "ok": False, "message": "The device refused the performance mode (result 0).",
                    "data": None}
        c.handlers.clear()
        c.handlers["set_mode"] = refused
        r2 = Reapplier(c, s, clock=Clock(), power_source=Power())
        await r2.apply_all()
        assert r2.rerun is False and "refused" in r2.warning
    run(go())


def test_partial_default_curve_read_is_completed_later(tmp_path):
    async def go():
        c = FakeClient()

        async def turbo_fails(args):
            if args["mode"] == "turbo":
                return {"id": None, "ok": False, "message": "Overdrive helper did not answer in 10 s.", "data": None}
            return ok({"mode": args["mode"], **(GPU_CURVE if args.get("fan") == "gpu" else LOW)})
        c.handlers["get_fans"] = turbo_fails
        s = make(tmp_path, user_set=False)
        r = Reapplier(c, s, clock=Clock(), power_source=Power())
        await r.apply_all()
        assert set(Settings.load(s.path).default_curve) == {"balanced", "silent"}
        c.calls.clear()
        del c.handlers["get_fans"]
        await r.apply_all()
        assert [a for cmd, a in c.calls if cmd == "get_fans"] == [{"mode": "turbo"}, {"mode": "turbo", "fan": "gpu"}]
        saved = Settings.load(s.path).default_curve
        assert set(saved) == {"balanced", "turbo", "silent"} and saved["balanced"] == LOW
        saved_gpu = Settings.load(s.path).default_curve_gpu
        assert set(saved_gpu) == {"balanced", "turbo", "silent"} and saved_gpu["balanced"] == GPU_CURVE
        c.calls.clear()
        await r.apply_all()
        assert all(cmd != "get_fans" for cmd, _ in c.calls)
    run(go())


# ---------- GPU fan curve ----------

GPU_LOW = {"temps": [35, 45, 55, 65, 75, 85, 95, 105], "speeds": [0, 0, 5, 8, 10, 11, 12, 20]}


def test_reapply_writes_both_fans_each_from_its_own_default(tmp_path):
    async def go():
        c = FakeClient(curves={m: LOW for m in ("balanced", "turbo", "silent")},
                       curves_gpu={m: GPU_LOW for m in ("balanced", "turbo", "silent")})
        s = make(tmp_path, mode="silent", fan_preset="quiet")
        r = Reapplier(c, s, clock=Clock(), power_source=Power())
        await r.apply_all()
        w = c.writes()
        assert [cmd for cmd, _ in w] == ["set_mode", "set_fans", "set_fans"]   # mode, then CPU, then GPU
        assert w[1][1]["temps"] == LOW["temps"] and "fan" not in w[1][1]
        assert w[2][1]["temps"] == GPU_LOW["temps"] and w[2][1]["fan"] == "gpu"
        assert w[2][1]["speeds"][-1] == 42            # 105 °C: G-Helper's floor ceil((105 - 70) * 1.2)
        assert r.fan_note == "Both fans adjusted to the safe minimum"
        assert r.warning == ""
    run(go())


def test_reapply_custom_uses_each_fans_custom_curve(tmp_path):
    async def go():
        c = FakeClient()
        cpu = {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [10, 20, 30, 40, 50, 60, 70, 80]}
        gpu = {"temps": [35, 45, 55, 65, 75, 85, 95, 105], "speeds": [5, 15, 25, 35, 45, 55, 65, 75]}
        s = make(tmp_path, fan_preset="custom", fan_curve=cpu, fan_curve_gpu=gpu)
        await Reapplier(c, s, clock=Clock(), power_source=Power()).apply_all()
        assert c.writes()[1:] == [("set_fans", {"temps": cpu["temps"], "speeds": cpu["speeds"]}),
                                  ("set_fans", {"temps": gpu["temps"], "speeds": gpu["speeds"], "fan": "gpu"})]
    run(go())


def test_reapply_custom_without_gpu_curve_keeps_gpu_firmware_curve(tmp_path):
    async def go():
        c = FakeClient()
        cpu = {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [10, 20, 30, 40, 50, 60, 70, 80]}
        s = make(tmp_path, fan_preset="custom", fan_curve=cpu)
        await Reapplier(c, s, clock=Clock(), power_source=Power()).apply_all()
        assert c.writes()[1:] == [("set_fans", {"temps": cpu["temps"], "speeds": cpu["speeds"]})]
    run(go())


def test_reapply_gpu_default_unknown_warns_but_applies_cpu(tmp_path):
    async def go():
        c = FakeClient()

        async def no_gpu(args):
            if args.get("fan") == "gpu":
                return {"id": 1, "ok": False, "message": "The device did not return a GPU fan curve.", "data": None}
            return ok({"mode": args["mode"], **LOW})
        c.handlers["get_fans"] = no_gpu
        s = make(tmp_path, fan_preset="cool")
        r = Reapplier(c, s, clock=Clock(), power_source=Power())
        await r.apply_all()
        assert [cmd for cmd, _ in c.writes()] == ["set_mode", "set_fans"]
        assert "fan" not in c.writes()[1][1]
        assert r.warning == ("The device's default GPU fan curve is not known yet, so the fan preset was not "
                             "applied to the GPU fan.")
        assert s.default_curve_gpu is None and set(s.default_curve) == {"balanced", "turbo", "silent"}
    run(go())


def test_reapply_gpu_curve_refusal_is_reported(tmp_path):
    async def go():
        c = FakeClient(curves_gpu={m: GPU_LOW for m in ("balanced", "turbo", "silent")})

        async def refuse_gpu(args):
            if args.get("fan") == "gpu":
                return {"id": 1, "ok": False, "message": "", "data": None}
            return ok(dict(args))
        c.handlers["set_fans"] = refuse_gpu
        s = make(tmp_path, fan_preset="quiet")
        r = Reapplier(c, s, clock=Clock(), power_source=Power())
        await r.apply_all()
        assert r.warning == "The GPU fan curve was not applied."
    run(go())


def test_recommended_preset_is_written_to_both_fans_after_mode(tmp_path):
    rec = {"temps": [40, 50, 60, 65, 70, 75, 80, 90], "speeds": [10, 15, 25, 35, 45, 55, 70, 90]}

    async def go():
        c = FakeClient()
        s = make(tmp_path, mode="silent", fan_preset="recommended")
        await Reapplier(c, s, clock=Clock(), power_source=Power()).apply_all()
        w = c.writes()
        assert [(cmd, a.get("fan")) for cmd, a in w] == [("set_mode", None), ("set_fans", None), ("set_fans", "gpu")]
        for _, a in w[1:]:
            assert (a["temps"], a["speeds"]) == (rec["temps"], rec["speeds"])
    run(go())


def _controller_client(found=True):
    c = FakeClient()

    async def info(args):
        return ok({"found": found, "pid": "0x1B4C", "xbox": False})
    c.handlers["controller_info"] = info
    return c


def test_reapply_includes_controller(tmp_path):
    async def go():
        c = _controller_client()
        s = make(tmp_path, fps=60, controller_mode="desktop", grip_vibration=False)
        r = Reapplier(c, s, clock=Clock(), power_source=Power())
        await r.apply_all()
        assert c.writes() == [("set_mode", {"mode": "balanced", "spl": 17, "sppt": 17, "fppt": 17}),
                              ("set_fps_limit", {"fps": 60}),
                              ("set_controller_mode", {"mode": "desktop"}),
                              ("set_vibration", {"strength": 0})]
        assert r.warning == ""
    run(go())


def test_reapply_skips_controller_when_none_found(tmp_path):
    async def go():
        c = _controller_client(found=False)
        r = Reapplier(c, make(tmp_path), clock=Clock(), power_source=Power())
        await r.apply_all()
        assert not any(cmd in ("set_controller_mode", "set_vibration") for cmd, _ in c.calls)
        assert r.warning == ""
    run(go())


def test_reapply_controller_failure_joins_warnings(tmp_path):
    async def go():
        c = _controller_client()

        async def refuse(args):
            return {"id": 1, "ok": False, "message": "The controller refused the mode.", "data": None}
        c.handlers["set_controller_mode"] = refuse
        r = Reapplier(c, make(tmp_path, fps=60), clock=Clock(), power_source=Power())
        await r.apply_all()
        assert "controller refused the mode" in r.warning
        assert ("set_fps_limit", {"fps": 60}) in c.writes()
    run(go())


def test_user_change_drops_controller_step(tmp_path):
    async def go():
        c = _controller_client()
        r = Reapplier(c, make(tmp_path), clock=Clock(), power_source=Power())
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_info(args):
            entered.set()
            await release.wait()
            return ok({"found": True, "pid": "0x1B4C", "xbox": False})
        c.handlers["controller_info"] = slow_info
        task = asyncio.ensure_future(r.apply_all())
        await entered.wait()
        r.generation += 1                      # a user change arrived meanwhile
        release.set()
        assert await task is False
        assert not any(cmd in ("set_controller_mode", "set_vibration") for cmd, _ in c.calls)
        assert r.rerun is True
    run(go())
