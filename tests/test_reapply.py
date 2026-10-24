"""Traction's reapply: the controller steps of Overdrive ba0307a's reapply (tests ported from its test_reapply.py)."""
import asyncio

from fake_helper import FakeClient, ok
from traction.armoury_record import ArmouryRecord
from traction.reapply import Reapplier
from traction.settings import Settings

RESTARTED = "Traction helper restarted, try again"


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
    s = Settings.load(str(tmp_path / "settings.json"))
    s.user_set = user_set
    for k, v in fields.items():
        setattr(s, k, v)
    return s


def run(coro):
    return asyncio.run(coro)


def _controller_client(found=True):
    c = FakeClient()

    async def info(args):
        return ok({"found": found, "pid": "0x1B4C", "xbox": False})
    c.handlers["controller_info"] = info
    return c


def test_reapply_includes_controller(tmp_path):
    async def go():
        c = _controller_client()
        s = make(tmp_path, controller_mode="desktop", grip_vibration=False)
        r = Reapplier(c, s, clock=Clock(), power_source=Power())
        assert await r.apply_all() is True
        assert c.calls == [("controller_info", {}), ("set_controller_mode", {"mode": "desktop"}),
                           ("set_vibration", {"strength": 0})]
        assert r.warning == ""
        c.calls.clear()
        s.controller_mode, s.grip_vibration = "auto", True     # Auto starts in Gamepad
        await r.apply_all()
        assert c.writes() == [("set_controller_mode", {"mode": "gamepad"}), ("set_vibration", {"strength": 100})]
    run(go())


def test_untouched_install_only_reads(tmp_path):
    async def go():
        c = _controller_client()
        await Reapplier(c, make(tmp_path, user_set=False), clock=Clock(), power_source=Power()).apply_all()
        assert c.writes() == [] and c.calls == []
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
        r = Reapplier(c, make(tmp_path), clock=Clock(), power_source=Power())
        await r.apply_all()
        assert "controller refused the mode" in r.warning
        assert ("set_vibration", {"strength": 100}) in c.writes()
        assert r.rerun is False                       # a device refusal is not retried
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


def test_controller_restart_schedules_retry(tmp_path):
    async def go():
        c = _controller_client()

        async def restarted(args):
            return {"id": 1, "ok": False, "message": RESTARTED, "data": None}
        c.handlers["set_controller_mode"] = restarted
        c.handlers["set_vibration"] = restarted
        r = Reapplier(c, make(tmp_path), clock=Clock(), power_source=Power())
        await r.apply_all()
        assert r.rerun is True
        assert r.warning.count(RESTARTED) == 1          # stopped after the first transport failure
        assert not any(cmd == "set_vibration" for cmd, _ in c.calls)
    run(go())


def test_controller_info_timeout_is_retried(tmp_path):
    async def go():
        c = FakeClient()

        async def timeout(args):
            return {"id": None, "ok": False, "message": "Traction helper did not answer in 10 s.", "data": None}
        c.handlers["controller_info"] = timeout
        r = Reapplier(c, make(tmp_path), clock=Clock(), power_source=Power())
        await r.apply_all()
        assert r.rerun is True and c.writes() == []
    run(go())


def test_reapply_on_ac_change_and_resume(tmp_path):
    async def go():
        c = _controller_client()
        power, clock = Power(True), Clock()
        r = Reapplier(c, make(tmp_path), clock=clock, power_source=power)
        r.prime()
        assert await r.tick() is False
        power.value = False
        assert await r.tick() is True
        assert [cmd for cmd, _ in c.writes()] == ["set_controller_mode", "set_vibration"]
        clock.step(5)
        assert await r.tick() is False
        clock.step(3600, mono=5)       # the PC slept: the controller forgot its mode and vibration
        assert await r.tick() is True
        assert len(c.writes()) == 4
    run(go())


def test_no_reapply_while_armoury_call_runs(tmp_path, caplog):
    async def go():
        c = _controller_client()
        r = Reapplier(c, make(tmp_path), clock=Clock(), power_source=Power())
        c.armoury_inflight = 1
        with caplog.at_level("INFO", logger="traction"):
            assert await r.apply_all() is False
        assert c.calls == [] and r.rerun is True
        assert "Reapply deferred while Armoury Crate services are being switched" in caplog.text
        c.armoury_inflight = 0
        assert await r.tick() is True
        assert c.writes() and r.rerun is False
    run(go())


def test_reapply_on_record_flip(tmp_path, caplog):
    async def go():
        folder = str(tmp_path / "armoury")
        c = _controller_client()
        r = Reapplier(c, make(tmp_path), clock=Clock(), power_source=Power(), record=ArmouryRecord(folder))
        r.prime()
        assert await r.tick() is False
        other = ArmouryRecord(folder)              # Overdrive (or Traction's own switch) stops Armoury Crate
        other.replaced, other.recorded = True, {"ArmouryCrateService": "Automatic"}
        other.save()
        with caplog.at_level("INFO", logger="traction"):
            assert await r.tick() is True
        assert "Reapplying (Armoury switched" in caplog.text
        assert [cmd for cmd, _ in c.writes()] == ["set_controller_mode", "set_vibration"]
        assert await r.tick() is False
    run(go())


def test_skip_message_names_traction(tmp_path, caplog):
    async def go():
        r = Reapplier(FakeClient(), make(tmp_path, user_set=False), clock=Clock(), power_source=Power())
        with caplog.at_level("INFO", logger="traction"):
            await r.apply_all()
        assert "Reapply skipped: no Traction settings chosen yet" in caplog.text
    run(go())
