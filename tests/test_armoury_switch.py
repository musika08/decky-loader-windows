"""The Armoury Crate switch shared with Traction (armoury_switch.py), on a temp record folder and a fake helper.

Most tests were ported from test_main.py when the switch moved out of main.py; they now read the shared record
(state.json) where they used to read overdrive.json.
"""
import asyncio
import json
import logging

import pytest

from fake_helper import FakeClient, fail, ok
from overdrive.armoury_record import ArmouryLocked, ArmouryRecord
from overdrive.armoury_switch import LOCKED, ArmourySwitch, migrate_from_settings
from overdrive.settings import Settings

ORIGINAL = {"ArmouryCrateSEService": "AutomaticDelayedStart", "LightingService": "Automatic"}


def make(tmp_path, client=None, **fields):
    """(switch, client, record). fields (replaced, recorded, running) are saved to the record first."""
    rec = ArmouryRecord(str(tmp_path / "armoury"))
    if fields:
        for k, v in fields.items():
            setattr(rec, k, v)
        rec.save()
    c = client or FakeClient()
    sw = ArmourySwitch(c, rec, "Overdrive")
    sw.lock_timeout = 0.2
    sw.applied = []
    sw.on_replaced = lambda: sw.applied.append(True)
    return sw, c, rec


def saved(tmp_path) -> ArmouryRecord:
    r = ArmouryRecord(str(tmp_path / "armoury"))
    r.load()
    return r


def on_disk(tmp_path) -> dict:
    with open(tmp_path / "armoury" / "state.json", encoding="utf-8") as f:
        return json.load(f)


def run(coro):
    return asyncio.run(coro)


def armoury_calls(c):
    return [cmd for cmd, _ in c.calls if cmd.startswith("armoury_")]


# ---------- the cross-process lock ----------

def test_switch_locked_returns_message(tmp_path):
    async def go():
        sw, c, rec = make(tmp_path)
        other = ArmouryRecord(str(tmp_path / "armoury"))     # Traction, say
        with other.lock():
            assert await sw.set(True) == {"ok": False, "message": LOCKED}
            assert await sw.set(False) == {"ok": False, "message": LOCKED}
        assert armoury_calls(c) == []
        assert LOCKED == "Armoury Crate is being switched by another plugin, try again in a few minutes."
        assert (await sw.set(True))["ok"] is True                # free again
    run(go())


def test_switch_holds_the_lock_while_it_runs(tmp_path):
    async def go():
        c = FakeClient()
        held = {}

        async def stop(args):
            try:
                with ArmouryRecord(str(tmp_path / "armoury")).lock(timeout=0):
                    held["free"] = True
            except ArmouryLocked:
                held["free"] = False
            return ok({})
        c.handlers["armoury_stop"] = stop
        sw, c, rec = make(tmp_path, client=c)
        assert (await sw.set(True))["ok"] is True
        assert held["free"] is False
        with ArmouryRecord(str(tmp_path / "armoury")).lock(timeout=0):   # released afterwards
            pass
    run(go())


def test_switch_reads_the_record_first(tmp_path):
    """The other plugin switched meanwhile: this one acts on the record on disk, not on what it read before."""
    async def go():
        sw, c, rec = make(tmp_path)
        await sw.set(True)
        other = ArmouryRecord(str(tmp_path / "armoury"))
        other.load()
        other.replaced, other.recorded, other.running = False, {}, []   # Traction turned Armoury Crate back on
        other.save()
        assert sw.replaced() is False
        c.calls.clear()
        assert (await sw.set(False)) == {"ok": True, "message": ""}     # already off: nothing to restore
        assert armoury_calls(c) == []
    run(go())


def test_replaced_follows_the_record_file(tmp_path):
    sw, c, rec = make(tmp_path)
    assert sw.replaced() is False
    other = ArmouryRecord(str(tmp_path / "armoury"))
    other.replaced = True
    other.save()
    assert sw.replaced() is True


# ---------- switching on ----------

def test_switch_on_writes_record_before_stop(tmp_path):
    async def go():
        c = FakeClient()
        seen = {}

        async def stop(args):
            seen.update(on_disk(tmp_path))
            return ok({})
        c.handlers["armoury_stop"] = stop
        sw, c, rec = make(tmp_path, client=c)
        assert (await sw.set(True)) == {"ok": True, "message": ""}
        assert seen == {"replaced": False, "recorded": ORIGINAL, "running": ["ArmouryCrateSEService"]}
        assert on_disk(tmp_path) == {"replaced": True, "recorded": ORIGINAL, "running": ["ArmouryCrateSEService"]}
        assert sw.applied == [True]
    run(go())


def test_armoury_on_saves_recorded_state_before_success_reply(tmp_path):
    async def go():
        c = FakeClient()

        async def restore_spy(args):
            return ok({"skipped": []})
        c.handlers["armoury_restore"] = restore_spy
        sw, c, rec = make(tmp_path, client=c)
        r = await sw.set(True)
        # the reply is only produced after the file holds what armoury_restore needs
        seen_on_disk = on_disk(tmp_path)
        assert r["ok"] is True
        assert seen_on_disk["replaced"] is True
        assert seen_on_disk["recorded"] == ORIGINAL
        assert seen_on_disk["running"] == ["ArmouryCrateSEService"]
        # state first, then a stop sized to the running services: max(5 min, 60 s x running + 60 s)
        assert [cmd for cmd, _ in c.calls][:2] == ["armoury_state", "armoury_stop"]
        assert c.timeouts["armoury_stop"] == [300]
        c.armoury["running"] = ["S%d" % i for i in range(8)]
        assert (await sw.set(False))["ok"] is True
        assert c.timeouts["armoury_restore"] == [540]
    run(go())


def test_armoury_on_save_failure_restores_and_reports(tmp_path, monkeypatch):
    async def go():
        sw, c, rec = make(tmp_path)
        real_save = rec.save
        saves = []

        def save_then_fail():
            saves.append(1)
            if len(saves) > 1:              # the provisional record saves; the final "on" does not
                raise OSError("disk full")
            real_save()
        monkeypatch.setattr(rec, "save", save_then_fail)
        r = await sw.set(True)
        assert r["ok"] is False and "disk full" in r["message"]
        assert r["message"].startswith("Could not save the shared Armoury Crate record (disk full). Armoury Crate was "
                                        "put back.")
        assert ("armoury_restore", {"recorded": ORIGINAL, "running": ["ArmouryCrateSEService"]}) in c.calls
        assert rec.replaced is False and sw.applied == []
    run(go())


def test_failed_record_save_names_the_shared_record(tmp_path, monkeypatch, caplog):
    """Final review O2: a failed save of the shared record never says it was a plugin's own settings."""
    async def go():
        sw, c, rec = make(tmp_path)
        sw.owner = "Traction"

        def broken_save():
            raise OSError("disk full")
        monkeypatch.setattr(rec, "save", broken_save)
        r = await sw.set(True)
        assert r == {"ok": False, "message": "Could not save the shared Armoury Crate record (disk full), so nothing "
                                             "was changed."}
        # the logged form (a merge of new services that could not be saved)
        with caplog.at_level(logging.ERROR):
            assert sw._save() == "Applied, but the setting could not be saved (disk full)."
        assert "Could not save the shared Armoury Crate record: disk full" in caplog.text
        assert "settings" not in caplog.text
    run(go())


def test_armoury_on_refused_when_record_cannot_be_saved(tmp_path, monkeypatch):
    async def go():
        sw, c, rec = make(tmp_path)

        def broken_save():
            raise OSError("disk full")
        monkeypatch.setattr(rec, "save", broken_save)
        r = await sw.set(True)
        assert r["ok"] is False and "nothing was changed" in r["message"]
        assert all(cmd != "armoury_stop" for cmd, _ in c.calls)
        assert rec.recorded == {}
    run(go())


def test_armoury_provisional_record_saved_before_stop(tmp_path):
    async def go():
        c = FakeClient()
        seen = {}

        async def stop_lost(args):
            seen.update(on_disk(tmp_path))
            # services get stopped and disabled, but the reply never arrives (helper killed)
            c.armoury["startTypes"] = {"ArmouryCrateSEService": "Manual", "LightingService": "Manual"}
            c.armoury["running"] = []
            return fail("Overdrive helper restarted, try again")
        c.handlers["armoury_stop"] = stop_lost
        sw, c, rec = make(tmp_path, client=c)
        r = await sw.set(True)
        assert r["ok"] is False
        assert seen["recorded"] == ORIGINAL and seen["replaced"] is False
        s = saved(tmp_path)
        assert s.recorded == ORIGINAL and s.running == ["ArmouryCrateSEService"]

        # the next switch-on keeps the provisional record instead of recording the stopped state
        del c.handlers["armoury_stop"]
        assert (await sw.set(True))["ok"] is True
        s = saved(tmp_path)
        assert s.replaced is True
        assert s.recorded == ORIGINAL and s.running == ["ArmouryCrateSEService"]
    run(go())


def test_armoury_stop_rolled_back_clears_provisional_record(tmp_path):
    async def go():
        c = FakeClient()

        async def stop_fails(args):
            return fail("Could not stop AsHidService (stop): access denied. Every service changed so far was put back.",
                        {"failed": "AsHidService", "rolledBack": True})
        c.handlers["armoury_stop"] = stop_fails
        sw, c, rec = make(tmp_path, client=c)
        assert (await sw.set(True))["ok"] is False
        assert saved(tmp_path).recorded == {}
    run(go())


def test_armoury_stop_failure_keeps_switch_off(tmp_path):
    async def go():
        c = FakeClient()

        async def stop_fails(args):
            return fail("Could not stop AsHidService (stop): access denied. Every service changed so far was put back.",
                        {"failed": "AsHidService", "rolledBack": True})
        c.handlers["armoury_stop"] = stop_fails
        sw, c, rec = make(tmp_path, client=c)
        r = await sw.set(True)
        assert r["ok"] is False and "AsHidService" in r["message"]
        assert rec.replaced is False and rec.recorded == {}
    run(go())


def test_armoury_on_shows_real_reason_when_state_unreadable(tmp_path):
    async def go():
        c = FakeClient()

        async def state_fails(args):
            return fail("Overdrive helper stopped 5 times in 10 minutes. Last error: x")
        c.handlers["armoury_state"] = state_fails
        sw, c, rec = make(tmp_path, client=c)
        r = await sw.set(True)
        assert r["ok"] is False and "stopped 5 times in 10 minutes" in r["message"]
        assert all(cmd != "armoury_stop" for cmd, _ in c.calls)
    run(go())


def test_busy_only_while_the_service_call_runs(tmp_path):
    async def go():
        c = FakeClient()
        seen = {}

        async def stop(args):
            seen["busy"] = sw.busy
            return ok({})
        c.handlers["armoury_stop"] = stop
        sw, c, rec = make(tmp_path, client=c)
        assert sw.busy is False
        await sw.set(True)
        assert seen["busy"] is True and sw.busy is False
    run(go())


# ---------- switching off ----------

def test_switch_off_restores_and_clears(tmp_path):
    async def go():
        recorded = {"ArmouryCrateService": "Automatic"}
        sw, c, rec = make(tmp_path, replaced=True, recorded=dict(recorded), running=["ArmouryCrateService"])
        r = await sw.set(False)
        assert r == {"ok": True, "message": ""}
        assert c.calls[-1] == ("armoury_restore", {"recorded": recorded, "running": ["ArmouryCrateService"]})
        assert on_disk(tmp_path) == {"replaced": False, "recorded": {}, "running": []}
    run(go())


def test_armoury_off_with_provisional_record_restores(tmp_path):
    async def go():
        recorded = {"ArmouryCrateService": "Automatic"}
        sw, c, rec = make(tmp_path, replaced=False, recorded=dict(recorded), running=["ArmouryCrateService"])
        r = await sw.set(False)
        assert r["ok"] is True
        assert c.calls[-1] == ("armoury_restore", {"recorded": recorded, "running": ["ArmouryCrateService"]})
        assert saved(tmp_path).recorded == {}
    run(go())


def test_armoury_off_with_empty_record_leaves_services(tmp_path):
    async def go():
        sw, c, rec = make(tmp_path, replaced=True)
        r = await sw.set(False)
        assert r["ok"] is True and "No recorded Armoury Crate state" in r["message"]
        assert all(cmd != "armoury_restore" for cmd, _ in c.calls)
        assert rec.replaced is False and saved(tmp_path).replaced is False
    run(go())


def test_armoury_off_reports_skipped_and_save_failure(tmp_path, monkeypatch):
    async def go():
        c = FakeClient()

        async def restore(args):
            return ok({"skipped": ["LightingService"]})
        c.handlers["armoury_restore"] = restore
        sw, c, rec = make(tmp_path, client=c, replaced=True, recorded=dict(ORIGINAL),
                          running=["ArmouryCrateSEService"])
        assert await sw.set(False) == {"ok": True, "message": "Not installed any more, skipped: LightingService."}
        sw2, c2, rec2 = make(tmp_path / "b", replaced=True, recorded=dict(ORIGINAL), running=[])

        def broken_save():
            raise OSError("disk full")
        monkeypatch.setattr(rec2, "save", broken_save)
        assert await sw2.set(False) == {"ok": False, "message": "Armoury Crate was restored, but the setting could "
                                                                 "not be saved (disk full)."}
    run(go())


# ---------- at load ----------

def test_load_restops_armoury_without_overwriting_record(tmp_path):
    async def go():
        recorded = {"ArmouryCrateService": "Automatic"}
        sw, c, rec = make(tmp_path, replaced=True, recorded=dict(recorded), running=["ArmouryCrateService"])
        await sw.on_load()
        assert [cmd for cmd, _ in c.calls][:2] == ["armoury_state", "armoury_stop"]
        # existing entries untouched (services the fake reports that were not recorded are added, see below)
        s = saved(tmp_path)
        assert s.recorded["ArmouryCrateService"] == "Automatic"
        assert s.running[0] == "ArmouryCrateService"
        assert s.replaced is True and sw.warning == ""
    run(go())


def test_load_restop_is_busy_and_adds_new_services_only(tmp_path):
    async def go():
        c = FakeClient()
        c.armoury["startTypes"] = {"ArmouryCrateService": "Disabled", "LightingService": "Automatic"}
        c.armoury["running"] = ["LightingService"]
        seen = {}

        async def stop(args):
            seen["busy"] = sw.busy
            return ok({"startTypes": {}, "running": []})
        c.handlers["armoury_stop"] = stop
        recorded = {"ArmouryCrateService": "Automatic"}
        sw, c, rec = make(tmp_path, client=c, replaced=True, recorded=dict(recorded),
                          running=["ArmouryCrateService"])
        await sw.on_load()
        assert seen["busy"] is True
        s = saved(tmp_path)
        assert s.recorded == {"ArmouryCrateService": "Automatic", "LightingService": "Automatic"}
        assert s.running == ["ArmouryCrateService", "LightingService"]
    run(go())


def test_load_skips_restop_when_state_unreadable(tmp_path):
    async def go():
        c = FakeClient()

        async def state_fails(args):
            return fail("Overdrive helper did not answer in 10 s.")
        c.handlers["armoury_state"] = state_fails
        sw, c, rec = make(tmp_path, client=c, replaced=True, recorded={"ArmouryCrateService": "Automatic"})
        await sw.on_load()
        assert all(cmd != "armoury_stop" for cmd, _ in c.calls)
        assert "were not stopped again" in sw.warning and "did not answer" in sw.warning
    run(go())


def test_load_restop_failure_warns(tmp_path):
    async def go():
        c = FakeClient()

        async def stop_fails(args):
            return fail("access denied")
        c.handlers["armoury_stop"] = stop_fails
        sw, c, rec = make(tmp_path, client=c, replaced=True, recorded={"ArmouryCrateService": "Automatic"})
        await sw.on_load()
        assert sw.warning == "Armoury Crate services could not be stopped again: access denied"
    run(go())


def test_on_load_recovers_cut_switch(tmp_path):
    async def go():
        c = FakeClient()
        seen = {}

        async def restore(args):
            seen["busy"] = sw.busy
            seen["args"] = args
            return ok({"skipped": []})
        c.handlers["armoury_restore"] = restore
        sw, c, rec = make(tmp_path, client=c, replaced=False, recorded=dict(ORIGINAL),
                          running=["ArmouryCrateSEService"])
        await sw.on_load()
        assert [cmd for cmd, _ in c.calls][:2] == ["armoury_state", "armoury_restore"]
        assert all(cmd != "armoury_stop" for cmd, _ in c.calls)
        assert seen["busy"] is True
        assert seen["args"] == {"recorded": ORIGINAL, "running": ["ArmouryCrateSEService"]}
        assert on_disk(tmp_path) == {"replaced": False, "recorded": {}, "running": []}
        assert sw.busy is False
        assert sw.warning == "A previous Armoury Crate switch did not finish, so Armoury Crate was put back."
    run(go())


def test_load_restore_failure_keeps_record_and_warns(tmp_path):
    async def go():
        c = FakeClient()

        async def restore_fails(args):
            return fail("Could not start AsusAppService (restore): x. Every service changed so far was put back.")
        c.handlers["armoury_restore"] = restore_fails
        recorded = {"ArmouryCrateService": "Automatic"}
        sw, c, rec = make(tmp_path, client=c, recorded=dict(recorded), running=["ArmouryCrateService"])
        await sw.on_load()
        assert saved(tmp_path).recorded == recorded
        assert "putting the services back failed" in sw.warning and "AsusAppService" in sw.warning
        assert "Turn the switch on and off again" in sw.warning
    run(go())


def test_load_restore_skipped_when_state_unreadable(tmp_path):
    async def go():
        c = FakeClient()

        async def state_fails(args):
            return fail("Overdrive helper did not answer in 10 s.")
        c.handlers["armoury_state"] = state_fails
        sw, c, rec = make(tmp_path, client=c, recorded={"ArmouryCrateService": "Automatic"})
        await sw.on_load()
        assert all(cmd != "armoury_restore" for cmd, _ in c.calls)
        assert "did not finish" in sw.warning and "did not answer" in sw.warning
    run(go())


def test_load_without_record_touches_no_service(tmp_path):
    async def go():
        sw, c, rec = make(tmp_path)
        await sw.on_load()
        assert armoury_calls(c) == []
        assert sw.warning == ""
    run(go())


def test_on_load_skips_when_locked(tmp_path, caplog):
    async def go():
        sw, c, rec = make(tmp_path, replaced=False, recorded=dict(ORIGINAL), running=["ArmouryCrateSEService"])
        other = ArmouryRecord(str(tmp_path / "armoury"))
        with caplog.at_level(logging.INFO, logger="overdrive"):
            with other.lock():
                await sw.on_load()
        assert c.calls == []
        assert sw.warning == ""
        lines = [r for r in caplog.records if "being switched by another plugin" in r.getMessage()]
        assert len(lines) == 1 and lines[0].levelno == logging.INFO
        assert saved(tmp_path).recorded == ORIGINAL       # left for whoever holds the lock
    run(go())


def test_load_warns_about_a_recovered_record(tmp_path):
    async def go():
        sw, c, rec = make(tmp_path, replaced=True, recorded={"ArmouryCrateService": "Automatic"},
                          running=["ArmouryCrateService"])
        (tmp_path / "armoury" / "state.json").write_bytes(b"{ broken")
        await sw.on_load()
        assert "armoury_stop" in armoury_calls(c)        # the recovered record says on: stopped again (P8)
        assert sw.warning == "The Armoury Crate record was recovered from state.json.bak."
    run(go())


# ---------- moving Overdrive's old record ----------

def test_migrate_once(tmp_path):
    s = Settings.load(str(tmp_path / "overdrive.json"))
    s.armoury_replaced = True
    s.armoury_recorded = {"ArmouryCrateService": "Automatic", "Bogus": "Manual"}
    s.armoury_running = ["ArmouryCrateService"]
    rec = ArmouryRecord(str(tmp_path / "armoury"))
    assert migrate_from_settings(rec, s) is True
    assert on_disk(tmp_path) == {"replaced": True, "recorded": {"ArmouryCrateService": "Automatic"},
                                 "running": ["ArmouryCrateService"]}
    # the record is switched off afterwards (say by Traction); a second move must not undo that
    other = saved(tmp_path)
    other.replaced, other.recorded, other.running = False, {}, []
    other.save()
    assert migrate_from_settings(ArmouryRecord(str(tmp_path / "armoury")), s) is False
    assert on_disk(tmp_path) == {"replaced": False, "recorded": {}, "running": []}


def test_migrate_never_overwrites_an_existing_shared_record(tmp_path):
    """Coordinator ruling on final review O1: an existing record, empty or not, is never overwritten."""
    for fields in ({}, {"replaced": True}, {"recorded": {"LightingService": "Manual"}}):
        folder = tmp_path / ("a" if "replaced" in fields else "b" if fields else "empty")
        rec = ArmouryRecord(str(folder))
        for k, v in fields.items():
            setattr(rec, k, v)
        rec.save()
        before = (folder / "state.json").read_bytes()
        s = Settings.load(str(tmp_path / "overdrive.json"))
        s.armoury_recorded = {"ArmouryCrateService": "Automatic"}
        assert migrate_from_settings(ArmouryRecord(str(folder)), s) is False
        assert (folder / "state.json").read_bytes() == before


def test_migrate_nothing_to_move(tmp_path):
    s = Settings.load(str(tmp_path / "overdrive.json"))
    rec = ArmouryRecord(str(tmp_path / "armoury"))
    assert migrate_from_settings(rec, s) is False
    assert not rec.exists()


def test_migrate_refused_while_locked(tmp_path, monkeypatch):
    s = Settings.load(str(tmp_path / "overdrive.json"))
    s.armoury_replaced = True
    rec = ArmouryRecord(str(tmp_path / "armoury"))
    real_lock = rec.lock
    monkeypatch.setattr(rec, "lock", lambda timeout=2.0: real_lock(timeout=0.1))
    with ArmouryRecord(str(tmp_path / "armoury")).lock():
        with pytest.raises(ArmouryLocked):
            migrate_from_settings(rec, s)
    assert not rec.exists()
