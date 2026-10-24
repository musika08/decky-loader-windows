import asyncio
import json
import os

import pytest

import main
from fake_helper import FakeClient, fail, ok
from traction import controls
from traction.armoury_record import ArmouryRecord
from traction.reapply import Reapplier
from traction.settings import Settings

UPDATE = "Update Overdrive to 0.2.0 or newer to use Traction's controller settings."
RUNNING = "Armoury Crate is running, so these settings may be overwritten."
BUSY = "Traction is switching Armoury Crate services. Try again when it finishes."


class Clock:
    def time(self):
        return 1_700_000_000.0

    def monotonic(self):
        return 5_000.0


def record_folder(tmp_path) -> str:
    return str(tmp_path / "armoury")


def saved_record(tmp_path) -> ArmouryRecord:
    r = ArmouryRecord(record_folder(tmp_path))
    r.load()
    return r


def settings_file(tmp_path) -> str:
    return str(tmp_path / "settings" / "traction" / "settings.json")


def plugin_with(tmp_path, client=None, old=False, **fields):
    """armoury_replaced/recorded/running go to the shared record (saved), the other fields to the settings."""
    p = main.Plugin()
    s = Settings.load(settings_file(tmp_path))
    rec = ArmouryRecord(record_folder(tmp_path))
    armoury = {k[len("armoury_"):]: v for k, v in fields.items() if k.startswith("armoury_")}
    for k, v in fields.items():
        if not k.startswith("armoury_"):
            setattr(s, k, v)
    if armoury:
        for k, v in armoury.items():
            setattr(rec, k, v)
        rec.save()
    c = client or FakeClient()
    reapplier = Reapplier(c, s, clock=Clock(), power_source=lambda: True,
                          record=ArmouryRecord(record_folder(tmp_path)))
    p._attach(c, s, reapplier, rec, old=old)
    return p, c, s


@pytest.fixture(autouse=True)
def _record_in_tmp(monkeypatch, tmp_path):
    # No test may touch C:\ProgramData or the real Decky folders: every path goes to the test's temp folder.
    monkeypatch.setattr(main, "record_dir", lambda: record_folder(tmp_path))
    monkeypatch.setattr(main, "settings_path", lambda: settings_file(tmp_path))
    monkeypatch.setattr(main, "overdrive_settings_path",
                        lambda: str(tmp_path / "settings" / "overdrive" / "overdrive.json"))
    monkeypatch.setattr(main, "plugins_dir", lambda: str(tmp_path / "plugins"))


@pytest.fixture(autouse=True)
def _no_real_helper(monkeypatch):
    # Belt and braces: no test in this file may ever spawn a process.
    async def refuse(*a, **k):
        raise AssertionError("tests must not start a process")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse)


@pytest.fixture(autouse=True)
def _fresh_controls_cache():
    controls.reset_cache()
    yield
    controls.reset_cache()


def run(coro):
    return asyncio.run(coro)


def wait(coro, secs=2.0):
    """A deadlock on the reapplier's lock fails the test instead of hanging it."""
    return asyncio.wait_for(coro, secs)


def reply(r):
    async def h(_args):
        return r
    return h


def controller_found(c):
    c.handlers["controller_info"] = reply(ok({"found": True, "pid": "0x1B4C", "xbox": False}))


def install_overdrive(tmp_path, version):
    folder = tmp_path / "plugins" / "overdrive"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "package.json").write_text(json.dumps({"name": "overdrive", "version": version}), encoding="utf-8")


def overdrive_settings(tmp_path, data):
    folder = tmp_path / "settings" / "overdrive"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "overdrive.json").write_text(json.dumps(data), encoding="utf-8")


def ally_main(monkeypatch, client=None):
    """_main on an Ally with a FakeClient; returns the list the clients made go to."""
    clients = []

    def make_client(*a, **k):
        c = client or FakeClient()
        clients.append((c, k))
        return c
    monkeypatch.setattr(main.device, "is_ally", lambda read_product=None: True)
    monkeypatch.setattr(main, "TractionHelperClient", make_client)
    return clients


def controller_writes(c):
    return [x for x in c.calls if x[0] in ("set_controller_mode", "set_vibration")]


# ---------- start and stop ----------

def test_not_ally_starts_nothing(monkeypatch, tmp_path):
    made = []
    monkeypatch.setattr(main.device, "is_ally", lambda read_product=None: False)
    monkeypatch.setattr(main, "TractionHelperClient", lambda *a, **k: made.append("client"))
    monkeypatch.setattr(main, "Reapplier", lambda *a, **k: made.append("reapplier"))

    async def go():
        p = main.Plugin()
        await p._main()
        state = await p.get_state()
        results = [await p.set_controller_mode("desktop"), await p.set_grip_vibration(False),
                   await p.set_touchscreen(False), await p.set_armoury_replaced(True)]
        await p._unload()
        return state, results
    state, results = run(go())
    assert made == []
    assert state["support"]["supported"] is False
    assert state["blocked"] == "" and state["note"] == ""
    for r in results:
        assert r == {"ok": False, "message": "Traction supports the ROG Ally family only"}
    assert not os.path.exists(settings_file(tmp_path))


def test_ally_starts_client_and_reapplier_and_unload_stops_both(monkeypatch, tmp_path):
    clients = ally_main(monkeypatch)

    async def go():
        p = main.Plugin()
        await p._main()
        await p._startup
        assert p.reapplier._task is not None
        await p._unload()
        return p
    p = run(go())
    c, kwargs = clients[0]
    assert c.started and c.stopped
    assert kwargs["name"] == "Traction"
    assert p.reapplier._task is None
    assert not os.path.exists(settings_file(tmp_path))       # an untouched install writes no settings


def test_helper_client_runs_traction_helper(tmp_path):
    client = main.TractionHelperClient(plugin_dir=str(tmp_path), name="Traction")
    assert client.exe_path == os.path.join(str(tmp_path), "bin", "traction-helper.exe")
    assert client.restarted_text == "Traction helper restarted, try again"


def test_paths_follow_decky(monkeypatch):
    monkeypatch.undo()     # the real path functions, with a stand-in decky
    root = os.path.join("C:\\", "homebrew")
    monkeypatch.setattr(main.decky, "DECKY_PLUGIN_SETTINGS_DIR", os.path.join(root, "settings", "traction"),
                        raising=False)
    monkeypatch.setattr(main.decky, "DECKY_PLUGIN_DIR", os.path.join(root, "plugins", "traction"), raising=False)
    assert main.settings_path() == os.path.join(root, "settings", "traction", "settings.json")
    assert main.overdrive_settings_path() == os.path.join(root, "settings", "overdrive", "overdrive.json")
    assert main.plugins_dir() == os.path.join(root, "plugins")
    assert main.record_dir() == r"C:\ProgramData\decky\armoury"


def test_plugin_json_runs_as_root():
    with open(os.path.join(os.path.dirname(main.__file__), "plugin.json"), encoding="utf-8") as f:
        assert "root" in json.load(f)["flags"]


# ---------- state ----------

def test_get_state_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(main.device, "product_name", lambda read_product=None: "ROG Ally RC71L")

    async def go():
        p, c, s = plugin_with(tmp_path, controller_mode="auto", grip_vibration=False)
        controller_found(c)
        c.handlers["get_touchscreen"] = reply(ok({"found": True, "enabled": True, "instanceId": "x"}))
        return await wait(p.get_state())
    st = run(go())
    assert set(st) >= {"support", "armoury", "controller", "vibration", "touchscreen", "blocked", "note", "busy"}
    assert st["support"] == {"supported": True, "product": "ROG Ally RC71L"}
    assert st["armoury"] == {"installed": True, "replaced": False, "warning": ""}
    assert st["controller"] == {"mode": "auto", "found": True}
    assert st["vibration"] is False
    assert st["touchscreen"] == {"found": True, "enabled": True}
    assert st["blocked"] == "" and st["busy"] is False
    assert st["warning"] == "" and st["unavailable"] == ""


def test_note_while_armoury_runs(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        assert (await wait(p.get_state()))["note"] == RUNNING        # no record yet: Armoury Crate runs
        p2, c2, s2 = plugin_with(tmp_path / "b", armoury_replaced=True,
                                 armoury_recorded={"ArmouryCrateService": "Automatic"})
        assert (await wait(p2.get_state()))["note"] == ""
        other = ArmouryRecord(record_folder(tmp_path))                # Overdrive stops Armoury Crate
        other.replaced, other.recorded = True, {"ArmouryCrateService": "Automatic"}
        other.save()
        st = await wait(p.get_state())
        assert st["note"] == "" and st["armoury"]["replaced"] is True
    run(go())


def test_state_reads_shared_record(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        assert (await p.get_state())["armoury"]["replaced"] is False
        other = ArmouryRecord(record_folder(tmp_path))      # Overdrive switches Armoury Crate off
        other.replaced, other.recorded = True, {"ArmouryCrateService": "Automatic"}
        other.save()
        assert (await p.get_state())["armoury"] == {"installed": True, "replaced": True, "warning": ""}
        other.replaced, other.recorded = False, {}
        other.save()
        assert (await p.get_state())["armoury"]["replaced"] is False
    run(go())


def test_get_state_skips_touchscreen_when_the_helper_does_not_answer(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        controller_found(c)
        st = await wait(p.get_state())
        assert st["controller"]["found"] is True
        c.handlers["controller_info"] = reply(fail("Traction helper did not answer in 10 s."))
        controls.reset_cache()
        n = len(c.calls)
        st = await wait(p.get_state())
        assert [cmd for cmd, _ in c.calls[n:]] == ["controller_info"]   # no touchscreen read behind a hung helper
        assert st["controller"]["found"] is True                         # the last reading is shown
    run(go())


# ---------- controller mode and vibration (ported from Overdrive ba0307a) ----------

def test_set_controller_mode_auto_starts_loop(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        controller_found(c)
        ca = p.controller_auto
        assert (await p.set_controller_mode("mouse"))["ok"] is False
        r = await wait(p.set_controller_mode("auto"))
        assert r["ok"] is True, r
        assert ("set_controller_mode", {"mode": "gamepad"}) in c.writes()   # Auto starts in Gamepad
        assert ca._on is True
        saved = Settings.load(s.path)
        assert (saved.controller_mode, saved.user_set) == ("auto", True)   # reapplied after sleep
        c.calls.clear()
        r = await wait(p.set_controller_mode("desktop"))
        assert r["ok"] is True, r
        assert ca._on is False and ca._task is None
        assert c.writes()[-2:] == [("set_controller_mode", {"mode": "desktop"}), ("set_vibration", {"strength": 100})]
        assert Settings.load(s.path).controller_mode == "desktop"
        # a refused write saves nothing
        c.handlers["set_controller_mode"] = reply(fail("The controller refused the mode."))
        r = await wait(p.set_controller_mode("gamepad"))
        assert r["ok"] is False and "refused" in r["message"]
        assert Settings.load(s.path).controller_mode == "desktop" and s.controller_mode == "desktop"
    run(go())


def test_set_controller_mode_away_from_auto_stops_loop_outside_the_lock(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, controller_mode="auto", user_set=True)
        controller_found(c)
        ca = p.controller_auto
        ca.start()
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(args):
            entered.set()
            await release.wait()
            return ok(dict(args))
        c.handlers["set_controller_mode"] = slow
        writing = asyncio.ensure_future(ca._write("desktop"))         # a loop write holds the reapplier's lock
        await wait(entered.wait())
        assert p.reapplier.lock.locked()
        release.set()
        r = await wait(p.set_controller_mode("gamepad"))             # stop() before user_change: no deadlock
        assert r["ok"] is True, r
        await wait(writing)
        assert ca._on is False
        assert c.writes()[-2] == ("set_controller_mode", {"mode": "gamepad"})
    run(go())


def test_concurrent_auto_and_desktop_leave_no_loop_running(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        controller_found(c)
        ca = p.controller_auto
        # Both calls pass the "stop a running loop" check, then wait for the lock: Auto first, Desktop second.
        async with p.reapplier.lock:
            a = asyncio.ensure_future(p.set_controller_mode("auto"))
            b = asyncio.ensure_future(p.set_controller_mode("desktop"))
            for _ in range(5):
                await asyncio.sleep(0)
        ra, rb = await wait(a), await wait(b)
        assert ra["ok"] and rb["ok"], (ra, rb)
        assert s.controller_mode == "desktop" and Settings.load(s.path).controller_mode == "desktop"
        assert ca.running is False and ca._task is None          # the loop Auto started was stopped
        modes = [a["mode"] for cmd, a in c.writes() if cmd == "set_controller_mode"]
        assert modes[-1] == "desktop"                             # the stored choice is what the device has
    run(go())


def test_concurrent_auto_desktop_gamepad_ends_in_the_last_choice(tmp_path):
    async def go():
        # C9: the stray-loop stop decides its write under the lock, so the later Gamepad choice is what stays.
        p, c, s = plugin_with(tmp_path)
        controller_found(c)
        ca = p.controller_auto
        async with p.reapplier.lock:
            calls = [asyncio.ensure_future(p.set_controller_mode(m)) for m in ("auto", "desktop", "gamepad")]
            for _ in range(5):
                await asyncio.sleep(0)
        for t in calls:
            r = await wait(t)
            assert r["ok"], r
        assert s.controller_mode == "gamepad" and Settings.load(s.path).controller_mode == "gamepad"
        assert ca.running is False
        modes = [a["mode"] for cmd, a in c.writes() if cmd == "set_controller_mode"]
        assert modes[-1] == "gamepad", modes
        assert modes.count("desktop") == 1, modes                # Desktop only from the Desktop choice itself
    run(go())


def test_controller_choices_saved_when_controller_missing(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)                               # controller_info: found False
        r = await wait(p.set_controller_mode("desktop"))
        assert r["ok"] is True and "not found" in r["message"]
        r = await wait(p.set_grip_vibration(False))
        assert r["ok"] is True and "not found" in r["message"]
        assert c.writes() == []
        saved = Settings.load(s.path)
        assert (saved.controller_mode, saved.grip_vibration) == ("desktop", False)
    run(go())


def test_set_grip_vibration_saves_and_applies(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        controller_found(c)
        gen = p.reapplier.generation
        r = await wait(p.set_grip_vibration(False))
        assert r == {"ok": True, "message": ""}
        assert p.reapplier.generation == gen + 1                       # a user change, under the shared lock
        assert c.writes() == [("set_vibration", {"strength": 0})]
        saved = Settings.load(s.path)
        assert (saved.grip_vibration, saved.user_set) == (False, True)
        assert (await wait(p.get_state()))["vibration"] is False
        c.handlers["set_vibration"] = reply(fail("The controller refused the vibration."))
        r = await wait(p.set_grip_vibration(True))
        assert r["ok"] is False
        assert s.grip_vibration is False and Settings.load(s.path).grip_vibration is False
        assert (await p.set_grip_vibration("yes"))["ok"] is False
    run(go())


def test_set_touchscreen_writes_and_reports(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        gen = p.reapplier.generation
        assert (await wait(p.set_touchscreen(False))) == {"ok": True, "message": ""}
        assert p.reapplier.generation == gen + 1
        assert c.writes() == [("set_touchscreen", {"enabled": False})]
        assert (await p.set_touchscreen("off"))["ok"] is False
        c.handlers["set_touchscreen"] = reply(fail("The touch screen was not found."))
        assert (await wait(p.set_touchscreen(True))) == {"ok": False, "message": "The touch screen was not found."}
        assert not os.path.exists(s.path)                              # Touch Screen is never stored
    run(go())


def test_setters_refused_while_busy(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        controller_found(c)
        p.armoury._busy = True
        for name, arg in (("set_controller_mode", "desktop"), ("set_grip_vibration", False),
                          ("set_touchscreen", False), ("set_armoury_replaced", True)):
            assert (await wait(getattr(p, name)(arg))) == {"ok": False, "message": BUSY}, name
        p.armoury._busy = False
        assert c.writes() == []
        assert (s.controller_mode, s.grip_vibration) == ("gamepad", True)
        assert not os.path.exists(s.path)
    run(go())


def test_startup_starts_controller_auto_when_mode_is_auto(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, controller_mode="auto", user_set=True)
        p._startup = asyncio.ensure_future(p._startup_sequence())
        await wait(p._startup)
        assert p.controller_auto._on is True
        assert p.controller_auto.autotdp is None                     # Traction samples the frame rate itself
        await wait(p._unload())
        assert p.controller_auto._on is False

        p2, c2, s2 = plugin_with(tmp_path / "b", controller_mode="desktop")
        p2._startup = asyncio.ensure_future(p2._startup_sequence())
        await wait(p2._startup)
        assert p2.controller_auto._on is False
        await wait(p2._unload())
    run(go())


def test_load_turns_stored_desktop_into_gamepad(tmp_path, caplog):
    async def go():
        # C7: Desktop does not survive a plugin load (Decky restart, boot).
        p, c, s = plugin_with(tmp_path, controller_mode="desktop", user_set=True)
        controller_found(c)
        with caplog.at_level("INFO"):
            p._startup = asyncio.ensure_future(p._startup_sequence())
            await wait(p._startup)
        assert "Controller mode Desktop is not kept across a plugin load: Gamepad" in caplog.text
        assert s.controller_mode == "gamepad"                 # changed before the first reapply
        assert Settings.load(s.path).controller_mode == "gamepad"   # and saved: the panel shows Gamepad
        await wait(p.reapplier.apply_all())
        modes = [a["mode"] for cmd, a in c.writes() if cmd == "set_controller_mode"]
        assert modes and set(modes) == {"gamepad"}
        assert p.controller_auto.running is False
        # Within the session a chosen Desktop is reapplied (resume, AC change) as before.
        assert (await wait(p.set_controller_mode("desktop")))["ok"] is True
        n = len(c.writes())
        assert await wait(p.reapplier.apply_all()) is True
        modes = [a["mode"] for cmd, a in c.writes()[n:] if cmd == "set_controller_mode"]
        assert modes == ["desktop"]
        await wait(p._unload())
        # "auto" and "gamepad" are left as stored
        for i, mode in enumerate(("auto", "gamepad")):
            p2, c2, s2 = plugin_with(tmp_path / str(i), controller_mode=mode, user_set=True)
            p2._startup = asyncio.ensure_future(p2._startup_sequence())
            await wait(p2._startup)
            assert s2.controller_mode == mode
            await wait(p2._unload())
    run(go())


def test_unload_writes_gamepad_first(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, user_set=True, controller_mode="auto")
        controller_found(c)
        p._startup = asyncio.ensure_future(p._startup_sequence())
        await wait(p._startup)
        await wait(p.reapplier.apply_all())
        before = len(c.writes())
        await wait(p._unload())
        after = c.writes()[before:]
        assert after == [("set_controller_mode", {"mode": "gamepad"})]
        assert c.stopped and p.reapplier._task is None
        # Gamepad is written at every unload, whatever the stored mode (Big Picture needs its gamepad).
        p2, c2, s2 = plugin_with(tmp_path / "b")
        await wait(p2._unload())
        assert c2.writes() == [("set_controller_mode", {"mode": "gamepad"})]
    run(go())


def test_unload_goes_on_when_a_step_fails(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path, user_set=True)
        p._startup = asyncio.ensure_future(p._startup_sequence())
        await wait(p._startup)

        async def boom(_args):
            raise RuntimeError("controller gone")
        c.handlers["set_controller_mode"] = boom
        await wait(p._unload())
        assert c.stopped and p.reapplier._task is None
    run(go())


# ---------- the Armoury Crate switch (shared record) ----------

def test_switch_on_applies_settings_next_poll(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        assert (await wait(p.set_armoury_replaced(True)))["ok"] is True
        assert p.reapplier.rerun is True and s.user_set is True
        assert Settings.load(s.path).user_set is True
        assert saved_record(tmp_path).replaced is True
        st = await wait(p.get_state())
        assert st["armoury"]["replaced"] is True and st["note"] == ""
        assert (await p.set_armoury_replaced("on"))["ok"] is False
        assert (await wait(p.set_armoury_replaced(False)))["ok"] is True
        assert saved_record(tmp_path).replaced is False
    run(go())


def test_switch_refused_while_the_other_plugin_holds_the_lock(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        p.armoury.lock_timeout = 0.1
        with ArmouryRecord(record_folder(tmp_path)).lock():
            r = await wait(p.set_armoury_replaced(True))
        assert r == {"ok": False,
                     "message": "Armoury Crate is being switched by another plugin, try again in a few minutes."}
        assert all(not cmd.startswith("armoury_") for cmd, _ in c.calls)
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
        p, c, s = plugin_with(tmp_path, client=c)
        controller_found(c)
        switch = asyncio.ensure_future(p.set_armoury_replaced(True))
        await entered.wait()
        assert await p.set_controller_mode("desktop") == {"ok": False, "message": BUSY}
        assert await p.set_armoury_replaced(False) == {"ok": False, "message": BUSY}
        assert c.writes() == [("armoury_stop", None)]          # nothing queued behind the service switch
        n = len(c.calls)
        st = await asyncio.wait_for(p.get_state(), 1)          # reads still work, and do not queue either
        assert st["busy"] is True and len(c.calls) == n
        release.set()
        assert (await switch)["ok"] is True
        assert (await p.get_state())["busy"] is False
        assert (await wait(p.set_controller_mode("desktop")))["ok"] is True
    run(go())


def test_load_restops_armoury_without_overwriting_record(tmp_path):
    async def go():
        recorded = {"ArmouryCrateService": "Automatic"}
        p, c, s = plugin_with(tmp_path, armoury_replaced=True, armoury_recorded=dict(recorded),
                              armoury_running=["ArmouryCrateService"])
        await wait(p._startup_sequence())
        assert [cmd for cmd, _ in c.calls][:2] == ["armoury_state", "armoury_stop"]
        rec = saved_record(tmp_path)
        assert rec.recorded["ArmouryCrateService"] == "Automatic"
        assert rec.running[0] == "ArmouryCrateService"
        assert p.reapplier._task is not None
        await p.reapplier.stop()
    run(go())


def test_load_restores_interrupted_switch_and_warns(tmp_path):
    async def go():
        recorded = {"ArmouryCrateSEService": "AutomaticDelayedStart", "LightingService": "Automatic"}
        p, c, s = plugin_with(tmp_path, armoury_replaced=False, armoury_recorded=dict(recorded),
                              armoury_running=["ArmouryCrateSEService"])
        await wait(p._startup_sequence())
        assert [cmd for cmd, _ in c.calls][:2] == ["armoury_state", "armoury_restore"]
        rec = saved_record(tmp_path)
        assert rec.recorded == {} and rec.replaced is False
        st = await wait(p.get_state())
        assert "did not finish, so Armoury Crate was put back" in st["armoury"]["warning"]
        await p.reapplier.stop()
    run(go())


def test_load_without_record_touches_no_service(tmp_path):
    async def go():
        p, c, s = plugin_with(tmp_path)
        await wait(p._startup_sequence())
        assert all(not cmd.startswith("armoury_") or cmd == "armoury_state" for cmd, _ in c.calls)
        assert (await wait(p.get_state()))["armoury"]["warning"] == ""
        await p.reapplier.stop()
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
        await wait(unload)
        assert c.stopped
        assert (await switch)["ok"] is True
        assert saved_record(tmp_path).replaced is True
    run(go())


# ---------- settings carried over from Overdrive (spec 3.3) ----------

def test_startup_takes_overdrive_values_once(monkeypatch, tmp_path):
    ally_main(monkeypatch)
    overdrive_settings(tmp_path, {"controller_mode": "auto", "grip_vibration": False, "mode": "turbo"})

    async def go():
        p = main.Plugin()
        await p._main()
        await wait(p._startup)
        assert (p.settings.controller_mode, p.settings.grip_vibration, p.settings.user_set) == ("auto", False, True)
        assert p.controller_auto.running is True
        await wait(p._unload())
    run(go())
    saved = Settings.load(settings_file(tmp_path))
    assert (saved.controller_mode, saved.grip_vibration, saved.user_set) == ("auto", False, True)
    # Traction's own file exists now: a later change in Overdrive's file is not taken again.
    overdrive_settings(tmp_path, {"controller_mode": "gamepad", "grip_vibration": True})

    async def again():
        p = main.Plugin()
        await p._main()
        await wait(p._startup)
        mode = p.settings.controller_mode
        await wait(p._unload())
        return mode
    assert run(again()) == "auto"


def test_migrate_desktop_becomes_gamepad_at_load(monkeypatch, tmp_path):
    ally_main(monkeypatch)
    overdrive_settings(tmp_path, {"controller_mode": "desktop", "grip_vibration": True})

    async def go():
        p = main.Plugin()
        await p._main()
        await wait(p._startup)
        mode = p.settings.controller_mode
        await wait(p._unload())
        return mode
    assert run(go()) == "gamepad"
    assert Settings.load(settings_file(tmp_path)).controller_mode == "gamepad"


def test_unreadable_overdrive_settings_still_load(monkeypatch, tmp_path):
    ally_main(monkeypatch)
    folder = tmp_path / "settings" / "overdrive"
    folder.mkdir(parents=True)
    (folder / "overdrive.json").write_text("{", encoding="utf-8")

    async def go():
        p = main.Plugin()
        await p._main()
        await wait(p._startup)
        st = await wait(p.get_state())
        await wait(p._unload())
        return p, st
    p, st = run(go())
    assert (p.settings.controller_mode, p.settings.grip_vibration, p.settings.user_set) == ("gamepad", True, False)
    assert st["warning"] == ""


# ---------- an Overdrive older than 0.2.0 next to Traction (spec 3.4) ----------

def test_old_overdrive_blocks_controller_writes(monkeypatch, tmp_path):
    install_overdrive(tmp_path, "0.1.0")
    overdrive_settings(tmp_path, {"controller_mode": "auto", "grip_vibration": False})
    c = FakeClient()
    controller_found(c)
    ally_main(monkeypatch, client=c)

    async def go():
        p = main.Plugin()
        await p._main()
        await wait(p._startup)
        st = await wait(p.get_state())
        results = [await wait(p.set_controller_mode("desktop")), await wait(p.set_grip_vibration(True))]
        p.reapplier.rerun = True
        await wait(p.reapplier.tick())          # even a reapply poll (not started anyway) writes nothing
        await wait(p._unload())
        return p, st, results
    p, st, results = run(go())
    assert controller_writes(c) == []
    assert st["blocked"] == UPDATE
    assert p.reapplier._task is None and p.controller_auto.running is False
    for r in results:
        assert r == {"ok": False, "message": UPDATE}
    assert c.stopped


def test_old_overdrive_keeps_armoury_read_only_and_touchscreen_working(monkeypatch, tmp_path):
    install_overdrive(tmp_path, "0.1.0")
    c = FakeClient()
    ally_main(monkeypatch, client=c)
    rec = ArmouryRecord(record_folder(tmp_path))     # a record says replaced: on_load would re-stop the services
    rec.replaced, rec.recorded = True, {"ArmouryCrateService": "Automatic"}
    rec.save()

    async def go():
        p = main.Plugin()
        await p._main()
        await wait(p._startup)
        st = await wait(p.get_state())
        sw = await wait(p.set_armoury_replaced(False))
        touch = await wait(p.set_touchscreen(False))
        await wait(p._unload())
        return st, sw, touch
    st, sw, touch = run(go())
    assert sw == {"ok": False, "message": UPDATE}
    assert all(cmd not in ("armoury_stop", "armoury_restore") for cmd, _ in c.calls)   # on_load did nothing either
    assert st["armoury"]["replaced"] is True                                          # still read
    assert touch == {"ok": True, "message": ""}
    assert c.writes() == [("set_touchscreen", {"enabled": False})]
    assert saved_record(tmp_path).replaced is True


def test_new_overdrive_does_not_block(monkeypatch, tmp_path):
    for version in ("0.2.0", "0.10.0"):
        folder = tmp_path / version
        install_overdrive(folder, version)
        monkeypatch.setattr(main, "plugins_dir", lambda f=folder: str(f / "plugins"))
        c = FakeClient()
        controller_found(c)
        ally_main(monkeypatch, client=c)

        async def go():
            p = main.Plugin()
            await p._main()
            await wait(p._startup)
            st = await wait(p.get_state())
            r = await wait(p.set_controller_mode("desktop"))
            await wait(p._unload())
            return st, r
        st, r = run(go())
        assert st["blocked"] == "" and r["ok"] is True, version
        assert ("set_controller_mode", {"mode": "desktop"}) in c.writes()


def test_updating_overdrive_unblocks_at_the_next_state_read(monkeypatch, tmp_path):
    install_overdrive(tmp_path, "0.1.0")
    c = FakeClient()
    controller_found(c)
    ally_main(monkeypatch, client=c)

    async def go():
        p = main.Plugin()
        await p._main()
        await wait(p._startup)
        assert (await wait(p.get_state()))["blocked"] == UPDATE
        install_overdrive(tmp_path, "0.2.0")                     # Overdrive updated while Traction runs
        st = await wait(p.get_state())
        assert st["blocked"] == ""
        await wait(p._late_start)
        assert p.reapplier._task is not None
        r = await wait(p.set_grip_vibration(False))
        await wait(p._unload())
        return r
    assert run(go()) == {"ok": True, "message": ""}
    assert ("set_vibration", {"strength": 0}) in c.writes()


# ---------- Overdrive's old Armoury record not moved yet (fix round 1) ----------

NOT_MOVED = ("Overdrive's Armoury Crate record has not moved to the shared record yet. Open Overdrive once, then "
             "try again.")
OLD_RECORD = {"armoury_replaced": True, "armoury_recorded": {"ArmouryCrateService": "Automatic"},
              "armoury_running": ["ArmouryCrateService"]}


def test_switch_refused_while_overdrives_old_record_is_not_moved(tmp_path, monkeypatch):
    for i, old in enumerate(({"armoury_recorded": {"ArmouryCrateService": "Automatic"}},
                             {"armoury_replaced": True}, {"armoury_running": ["ArmouryCrateService"]}, OLD_RECORD)):
        base = tmp_path / str(i)
        overdrive_settings(base, old)

        async def go():
            # this test's paths live under `base`: point the patched path functions there
            main_paths = {"record_dir": lambda: record_folder(base),
                          "overdrive_settings_path": lambda: str(base / "settings" / "overdrive" / "overdrive.json")}
            for k, v in main_paths.items():
                monkeypatch.setattr(main, k, v)
            p, c, s = plugin_with(base)
            st = await wait(p.get_state())
            assert st["armoury"]["warning"] == NOT_MOVED
            for on in (True, False):
                assert (await wait(p.set_armoury_replaced(on))) == {"ok": False, "message": NOT_MOVED}
            assert all(not cmd.startswith("armoury_") or cmd == "armoury_state" for cmd, _ in c.calls)
            assert not os.path.exists(os.path.join(record_folder(base), "state.json"))   # no record created
            assert s.user_set is False and not os.path.exists(s.path)
        run(go())


def test_switch_works_once_the_shared_record_exists(tmp_path):
    overdrive_settings(tmp_path, OLD_RECORD)

    async def go():
        p, c, s = plugin_with(tmp_path)
        assert (await wait(p.set_armoury_replaced(True)))["message"] == NOT_MOVED
        moved = ArmouryRecord(record_folder(tmp_path))          # Overdrive 0.2.0 moved its record
        moved.replaced, moved.recorded = True, {"ArmouryCrateService": "Automatic"}
        moved.save()
        st = await wait(p.get_state())
        assert st["armoury"]["warning"] == "" and st["armoury"]["replaced"] is True
        r = await wait(p.set_armoury_replaced(False))
        assert r["ok"] is True, r
        assert ("armoury_restore", {"recorded": {"ArmouryCrateService": "Automatic"}, "running": []}) in c.calls
        assert saved_record(tmp_path).replaced is False
    run(go())


def test_unreadable_or_clean_overdrive_settings_do_not_block_the_switch(tmp_path, monkeypatch):
    cases = {"unreadable": "{", "not an object": "[]", "empty record": json.dumps(
        {"armoury_replaced": False, "armoury_recorded": {}, "armoury_running": []}),
        "wrong types": json.dumps({"armoury_replaced": "yes", "armoury_recorded": ["x"], "armoury_running": {}})}
    for i, (what, text) in enumerate(cases.items()):
        base = tmp_path / str(i)
        folder = base / "settings" / "overdrive"
        folder.mkdir(parents=True)
        (folder / "overdrive.json").write_text(text, encoding="utf-8")

        async def go():
            monkeypatch.setattr(main, "record_dir", lambda: record_folder(base))
            monkeypatch.setattr(main, "overdrive_settings_path", lambda: str(folder / "overdrive.json"))
            p, c, s = plugin_with(base)
            assert (await wait(p.get_state()))["armoury"]["warning"] == "", what
            assert (await wait(p.set_armoury_replaced(True)))["ok"] is True, what
            assert saved_record(base).replaced is True, what
        run(go())
    # no overdrive.json at all: not blocked either (test_switch_on_applies_settings_next_poll)
