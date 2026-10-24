"""The Armoury Crate switch shared by Overdrive and Traction: stop Armoury Crate's services, put them back, and the
load-time checks (ruling P8 re-stop, recovery of a switch cut short).

Shared with the Traction plugin: standard library only, plus armoury_record (and through it settings_file).

The record (start types + running services before anything was stopped) is what armoury_restore puts back, so it must
exist on disk before the first service is touched and must never be replaced by types read after a stop. A record with
the switch off is provisional (a switch-on that never got its reply); switching off with one still restores it, and
the next switch-on keeps it.

Every public operation takes, in this order: this object's asyncio lock, the record's cross-process lock
(ArmouryRecord.lock, so Overdrive and Traction never switch at the same time), then reloads the record before acting.
"""
import asyncio
import logging
from typing import Callable, Optional

from .armoury_record import ARMOURY_SERVICES, ArmouryLocked, ArmouryRecord, clean_running, clean_types

ARMOURY_MIN_TIMEOUT = 300.0      # ruling P8: armoury_* calls get at least 5 minutes
ARMOURY_PER_SERVICE = 60.0       # each service may wait 30 s, doubled when a rollback runs
LOCK_TIMEOUT = 2.0
LOCK_POLL = 0.05
LOCKED = "Armoury Crate is being switched by another plugin, try again in a few minutes."


def _fail(message: str) -> dict:
    return {"ok": False, "message": message}


def clean_record(data: dict):
    """Start types and running list from an armoury_state/armoury_stop reply, limited to listed services."""
    types = clean_types(data.get("startTypes"))
    return types, clean_running(data.get("running"), types)


def migrate_from_settings(record: ArmouryRecord, s) -> bool:
    """Moves a plugin's own armoury_replaced/recorded/running into the shared record, once: only when the record does
    not exist yet and the settings hold any of them. True when they were moved. Raises ArmouryLocked (another plugin
    is switching) or OSError (the record could not be saved); the settings are left as they are then."""
    replaced = bool(getattr(s, "armoury_replaced", False))
    recorded = dict(getattr(s, "armoury_recorded", None) or {})
    running = list(getattr(s, "armoury_running", None) or [])
    if not (replaced or recorded or running):
        return False
    with record.lock():
        if record.exists():
            return False
        record.replaced = replaced
        record.recorded = clean_types(recorded)
        record.running = clean_running(running, record.recorded)
        try:
            record.save()
        except OSError:
            record.replaced, record.recorded, record.running = False, {}, []
            raise
    return True


class ArmourySwitch:
    def __init__(self, client, record: ArmouryRecord, owner: str, logger: Optional[logging.Logger] = None,
                 on_replaced: Optional[Callable[[], None]] = None):
        self.client = client
        self.record = record
        self.owner = owner                  # "Overdrive" / "Traction": fills the messages only
        self.log = logger or logging.getLogger(owner.lower())
        self.on_replaced = on_replaced      # after a switch-on landed (the plugin applies its settings then)
        self.lock_timeout = LOCK_TIMEOUT
        self._lock = asyncio.Lock()
        self._installed = None              # armoury_state's "installed", None until read
        self._warning = ""
        self._busy = False                  # an armoury_stop / armoury_restore call is running
        self._until = 0.0                   # loop time by which that call gives up

    # ---------- what the plugin reads ----------

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def until(self) -> float:
        return self._until

    @property
    def warning(self) -> str:
        return " ".join(w for w in (self.record.note, self._warning) if w)

    @property
    def installed(self) -> bool:
        return bool(self._installed)

    def replaced(self) -> bool:
        """The switch as the record on disk says now (another plugin may have switched). While an operation of this
        object runs, the record it is working on."""
        if not self._lock.locked():
            try:
                self.record.refresh()
            except Exception as e:
                self.log.warning(f"Could not read the Armoury Crate record: {e}")
        return bool(self.record.replaced)

    async def check_installed(self) -> None:
        """Reads armoury_state once (never while a service call runs: it would queue behind it)."""
        if self._installed is not None or self._busy:
            return
        r = await self.client.call("armoury_state")
        if r.get("ok") and isinstance(r.get("data"), dict):
            self._installed = bool(r["data"].get("installed"))

    # ---------- locks ----------

    async def _take_record_lock(self):
        """The record's lock, tried without blocking the event loop for up to lock_timeout seconds."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.lock_timeout
        while True:
            held = self.record.lock(timeout=0)
            try:
                held.__enter__()
                return held
            except ArmouryLocked:
                if loop.time() >= deadline:
                    raise
            await asyncio.sleep(LOCK_POLL)

    # ---------- helper calls ----------

    async def _timeout(self, extra_running: int):
        """(timeout, armoury_state data or None, failure message). armoury_state is an ordinary read (normal timeout,
        not busy). Timeout = max(5 min, 60 s per listed service running now + 60 s); all listed services count when
        the state is unknown."""
        r = await self.client.call("armoury_state")
        d = r.get("data") if r.get("ok") and isinstance(r.get("data"), dict) else None
        if d is not None:
            self._installed = bool(d.get("installed"))
            n = len(d.get("running") or [])
        else:
            n = len(ARMOURY_SERVICES)
        n = max(n, extra_running)
        message = "" if d is not None else str(r.get("message") or "no answer")
        return max(ARMOURY_MIN_TIMEOUT, ARMOURY_PER_SERVICE * n + 60.0), d, message

    async def _call(self, cmd: str, args, timeout: float) -> dict:
        """armoury_stop / armoury_restore: busy (P12) for exactly as long as the call runs."""
        self._until = asyncio.get_running_loop().time() + timeout + 5.0
        self._busy = True
        try:
            return await self.client.call(cmd, args, timeout)
        finally:
            self._busy = False

    def _save(self) -> str:
        try:
            self.record.save()
            return ""
        except OSError as e:
            self.log.error(f"Could not save {self.owner}'s settings: {e}")
            return f"Applied, but the setting could not be saved ({e})."

    def _restore_args(self) -> dict:
        return {"recorded": dict(self.record.recorded), "running": list(self.record.running)}

    def _merge_new_services(self, data) -> bool:
        """Adds services that are missing from a non-empty record (installed later), never changing an entry."""
        rec = self.record
        if not rec.recorded or not isinstance(data, dict):
            return False
        types, running = clean_record(data)
        added = False
        for name, typ in types.items():
            if name not in rec.recorded:
                rec.recorded[name] = typ
                if name in running and name not in rec.running:
                    rec.running.append(name)
                added = True
        return added

    # ---------- load ----------

    async def on_load(self) -> None:
        """At plugin load: with the switch on, stop the services again (P8); with a provisional record, put them back
        (a switch cut short). Nothing at all when another plugin is switching right now."""
        async with self._lock:
            try:
                held = await self._take_record_lock()
            except ArmouryLocked:
                self.log.info(f"{self.owner}: Armoury Crate is being switched by another plugin; the load-time "
                              "check was skipped")
                return
            except OSError as e:
                self._warning = f"The Armoury Crate record could not be opened at load: {e}"
                self.log.warning(self._warning)
                return
            try:
                self.record.load()
                if self.record.replaced:
                    await self._restop_on_load()
                elif self.record.recorded:
                    await self._recover_on_load()
            finally:
                held.__exit__(None, None, None)

    async def _restop_on_load(self) -> None:
        # Ruling P8: stopped-but-Automatic services may have come back after a reboot, so stop them again while the
        # switch is on. Only the first switch-on records the start types; this run never overwrites them.
        timeout, state, why = await self._timeout(0)
        if state is None:
            # Never stop blind (an unknown state means the all-services timeout on a possibly hung helper).
            self._warning = ("Armoury Crate services could not be checked at load, so they were not "
                             f"stopped again: {why}")
            self.log.warning(self._warning)
            return
        if self._merge_new_services(state):
            self._save()
        r = await self._call("armoury_stop", None, timeout)
        if r.get("ok"):
            self._warning = ""
        else:
            self._warning = "Armoury Crate services could not be stopped again: " + str(r.get("message"))
            self.log.warning(self._warning)

    async def _recover_on_load(self) -> None:
        """A record with the switch off at load: a switch-on (or its save-failure restore) was cut short, typically
        by Decky killing the plugin 5 s into an unload. Some services may be stopped and Disabled/Manual, so put the
        recorded state back now (busy while it runs) and tell the owner how it went."""
        rec = self.record
        timeout, state, why = await self._timeout(len(rec.running))
        if state is None:
            self._warning = ("A previous Armoury Crate switch did not finish, and the services could not "
                             f"be checked at load ({why}). Turn the switch on and off again to put "
                             "them back.")
            self.log.warning(self._warning)
            return
        self.log.info(f"{self.owner}: a previous Armoury Crate switch did not finish; putting the services back")
        r = await self._call("armoury_restore", self._restore_args(), timeout)
        if not r.get("ok"):
            self._warning = ("A previous Armoury Crate switch did not finish, and putting the services "
                             f"back failed: {r.get('message')} Turn the switch on and off again to "
                             "retry.")
            self.log.warning(self._warning)
            return
        rec.recorded, rec.running = {}, []
        msg = "A previous Armoury Crate switch did not finish, so Armoury Crate was put back."
        skipped = (r.get("data") or {}).get("skipped") if isinstance(r.get("data"), dict) else None
        if skipped:
            msg += " Not installed any more, skipped: " + ", ".join(map(str, skipped)) + "."
        saved = self._save()
        if saved:
            msg += " The cleared record could not be saved, so this runs again at the next load."
        self._warning = msg
        self.log.info(f"{self.owner}: {msg}")

    # ---------- the switch ----------

    async def set(self, on: bool) -> dict:
        async with self._lock:
            try:
                held = await self._take_record_lock()
            except ArmouryLocked:
                return _fail(LOCKED)
            except OSError as e:
                return _fail(f"Could not open the Armoury Crate record ({e}), so nothing was changed.")
            try:
                self.record.load()
                return await (self._on() if on else self._off())
            finally:
                held.__exit__(None, None, None)

    async def _on(self) -> dict:
        rec = self.record
        if rec.replaced:
            return {"ok": True, "message": ""}
        timeout, state, why = await self._timeout(0)
        if state is None:
            return _fail(f"Could not read the Armoury Crate services ({why}), so nothing was changed.")
        provisional = not rec.recorded
        if provisional:
            types, running = clean_record(state)
            rec.recorded, rec.running = types, running
            try:
                rec.save()   # on disk before anything is stopped
            except OSError as e:
                rec.recorded, rec.running = {}, []
                return _fail(f"Could not save {self.owner}'s settings ({e}), so nothing was changed.")
        elif self._merge_new_services(state):
            self._save()

        r = await self._call("armoury_stop", None, timeout)
        if not r.get("ok"):
            d = r.get("data") if isinstance(r.get("data"), dict) else {}
            if provisional and d.get("rolledBack") is True:
                # The helper put every service back, so the provisional record is no longer needed.
                rec.recorded, rec.running = {}, []
                self._save()
            return _fail(str(r.get("message")))

        rec.replaced = True
        try:
            rec.save()   # the switch reports success only once this is on disk
        except OSError as e:
            rec.replaced = False
            back = await self._call("armoury_restore", self._restore_args(), timeout)
            tail = ("Armoury Crate was put back." if back.get("ok")
                    else "Putting Armoury Crate back also failed: " + str(back.get("message")))
            return _fail(f"Could not save {self.owner}'s settings ({e}). {tail}")
        self._warning = ""
        if self.on_replaced is not None:   # "then apply our stored settings"
            try:
                self.on_replaced()
            except Exception:
                self.log.exception("Applying the settings after the Armoury Crate switch failed")
        return {"ok": True, "message": ""}

    async def _off(self) -> dict:
        rec = self.record
        if not rec.recorded:
            if not rec.replaced:
                return {"ok": True, "message": ""}
            rec.replaced = False
            msg = self._save()
            return {"ok": True, "message": ("No recorded Armoury Crate state was found, so the services were left as "
                                            "they are. " + msg).strip()}
        timeout, _, _ = await self._timeout(len(rec.running))
        r = await self._call("armoury_restore", self._restore_args(), timeout)
        if not r.get("ok"):
            return _fail(str(r.get("message")))
        rec.replaced, rec.recorded, rec.running = False, {}, []
        self._warning = ""
        msgs = []
        skipped = (r.get("data") or {}).get("skipped") if isinstance(r.get("data"), dict) else None
        if skipped:
            msgs.append("Not installed any more, skipped: " + ", ".join(map(str, skipped)) + ".")
        try:
            rec.save()
        except OSError as e:
            return _fail(f"Armoury Crate was restored, but the setting could not be saved ({e}).")
        return {"ok": True, "message": " ".join(msgs)}
