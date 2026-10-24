import asyncio
import os
import sys

import decky  # type: ignore

# The loader appends py_modules to sys.path; older Windows builds did not, so make sure.
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PY_MODULES = os.path.join(PLUGIN_DIR, "py_modules")
if _PY_MODULES not in sys.path:
    sys.path.append(_PY_MODULES)

from traction import controls, device  # noqa: E402
from traction.armoury_record import RECORD_DIR, ArmouryRecord  # noqa: E402
from traction.armoury_switch import ArmourySwitch  # noqa: E402
from traction.controller_auto import ControllerAuto  # noqa: E402
from traction.helper_client import HelperClient  # noqa: E402
from traction.overdrive_compat import UPDATE_NOTE, old_overdrive  # noqa: E402
from traction.reapply import Reapplier  # noqa: E402
from traction.settings import CONTROLLER_MODES, Settings, migrate_from_overdrive  # noqa: E402

NAME = "Traction"
HELPER_EXE = "traction-helper.exe"
NOT_ALLY = "Traction supports the ROG Ally family only"
BUSY = "Traction is switching Armoury Crate services. Try again when it finishes."
NO_CONTROLLER = "The controller was not found; the choice is saved and applied when it is back."
ARMOURY_RUNNING = "Armoury Crate is running, so these settings may be overwritten."
UNREAD = {"controller": {"found": False}, "touchscreen": {"found": False, "enabled": False}}


class TractionHelperClient(HelperClient):
    """The shared HelperClient, running bin\\traction-helper.exe (the shared copy names Overdrive's exe)."""

    @property
    def exe_path(self) -> str:
        return os.path.join(self._plugin_dir, "bin", HELPER_EXE)


def settings_path() -> str:
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")


def overdrive_settings_path() -> str:
    """Overdrive's settings file: Decky gives each plugin <settings root>\\<plugin folder>, and Overdrive keeps
    overdrive.json in its folder (Overdrive main.py settings_path)."""
    root = os.path.dirname(os.path.normpath(decky.DECKY_PLUGIN_SETTINGS_DIR))
    return os.path.join(root, "overdrive", "overdrive.json")


def plugins_dir() -> str:
    """Decky's plugins folder (Traction's own folder's parent), where an installed Overdrive is looked for."""
    return os.path.dirname(os.path.normpath(getattr(decky, "DECKY_PLUGIN_DIR", "") or PLUGIN_DIR))


def record_dir() -> str:
    """The Armoury Crate record shared with Overdrive."""
    return RECORD_DIR


def _fail(message: str) -> dict:
    return {"ok": False, "message": message}


class Plugin:
    def __init__(self) -> None:
        self.client = None
        self.settings = None
        self.reapplier = None
        self.controller_auto = None
        self.armoury = None              # the Armoury Crate switch (its record is shared with Overdrive)
        self._old = False                # an Overdrive older than 0.2.0 drives the controller: Traction writes none
        self._plugins_dir = ""
        self._controls = None            # the last controller/touchscreen reading (shown while a switch runs)
        self._startup = None
        self._late_start = None          # the controller start once an old Overdrive was updated

    def _attach(self, client, settings, reapplier, record=None, old: bool = False, plugins: str = "") -> None:
        self.client, self.settings, self.reapplier = client, settings, reapplier
        self._old, self._plugins_dir = old, plugins
        if client is not None and record is not None:
            self.armoury = ArmourySwitch(client, record, NAME, logger=decky.logger,
                                         on_replaced=self._armoury_replaced)
        if reapplier is not None:
            reapplier.busy_check = self._busy
            reapplier.blocked = lambda: self._old
            if client is not None:
                # Traction has no Auto TDP: the loop asks frame_sample itself.
                self.controller_auto = ControllerAuto(client, reapplier, None, logger=decky.logger)

    def _busy(self) -> bool:
        return bool(self.armoury is not None and self.armoury.busy) or bool(getattr(self.client, "busy", False))

    def _refuse_write(self):
        """A reply refusing a write now (not an Ally, or Armoury services are being switched), else None.

        While an armoury_* call runs the single-threaded helper is busy for minutes; a write sent then would time out
        here yet still run later, so it is refused instead of queued."""
        if self.client is None:
            return _fail(NOT_ALLY)
        if self._busy():
            return _fail(BUSY)
        return None

    def _refuse_controller(self):
        """_refuse_write, plus the update note while an old Overdrive drives the controller."""
        return self._refuse_write() or (_fail(UPDATE_NOTE) if self._old else None)

    async def _main(self) -> None:
        if not device.is_ally():
            decky.logger.info(f"Traction: not an Ally ({device.product_name()!r}); helper not started")
            return
        settings = Settings.load(settings_path(), logger=decky.logger)   # an unreadable file is kept, never lost
        if migrate_from_overdrive(overdrive_settings_path(), settings):
            decky.logger.info(f"Traction: took the controller settings from Overdrive ({settings.controller_mode}, "
                              f"vibration {'on' if settings.grip_vibration else 'off'})")
            self.settings = settings
            self._save()
        plugins = plugins_dir()
        old = old_overdrive(plugins)
        runtime_dir = getattr(decky, "DECKY_PLUGIN_RUNTIME_DIR", "") or None
        client = TractionHelperClient(plugin_dir=PLUGIN_DIR, runtime_dir=runtime_dir, logger=decky.logger, name=NAME)
        # Two views of one record file: the switch's (changed only under its locks) and the reapplier's (read only).
        folder = record_dir()
        reapplier = Reapplier(client, settings, logger=decky.logger, record=ArmouryRecord(folder, decky.logger))
        self._attach(client, settings, reapplier, ArmouryRecord(folder, decky.logger), old=old, plugins=plugins)
        client.start()
        self._startup = asyncio.ensure_future(self._startup_sequence())
        decky.logger.info("Traction started")

    def _desktop_not_kept_on_load(self) -> None:
        """C7: Desktop does not survive a plugin load. A stored Desktop becomes Gamepad (and is saved) before the
        first reapply, so the device and the panel agree on Gamepad after a Decky restart or a boot. Within a running
        session the reapplies (resume, AC change, helper restart) keep writing Desktop."""
        s = self.settings
        if s is None or s.controller_mode != "desktop":
            return
        s.controller_mode = "gamepad"
        decky.logger.info("Controller mode Desktop is not kept across a plugin load: Gamepad")
        self._save()

    async def _startup_sequence(self) -> None:
        self._desktop_not_kept_on_load()
        if self._old:
            decky.logger.info("Traction: an Overdrive older than 0.2.0 is installed; nothing is written to the "
                              "controller and the Armoury Crate switch is left to it")
            return
        await self._start_writes()

    async def _start_writes(self) -> None:
        """The load-time Armoury check (P8 re-stop, recovery), the reapplier and, when stored, Controller Auto."""
        if self.armoury is not None:
            await self.armoury.on_load()
        self.reapplier.start()
        if self.settings.controller_mode == "auto" and self.controller_auto is not None:
            self.controller_auto.start()   # the reapply writes Gamepad first; the loop decides from there

    def _check_overdrive_updated(self) -> None:
        """An old Overdrive updated while Traction runs: start what the load held back."""
        if not self._old or not self._plugins_dir or not self._startup_done() or old_overdrive(self._plugins_dir):
            return
        self._old = False
        decky.logger.info("Traction: Overdrive was updated; the controller settings are applied from now on")
        self._late_start = asyncio.ensure_future(self._start_writes())

    async def _unload(self) -> None:
        # Try not to kill the helper halfway through changing services: wait for the Armoury operation to finish,
        # bounded by its own call timeout. Decky does not wait that long: it kills the plugin process 5 s after
        # asking it to stop (decky-loader plugin/plugin.py kill_if_still_running), and the helper runs in the
        # plugin's job object with KILL_ON_JOB_CLOSE (localplatform/winprocess.py _create_job), so it dies too.
        # A switch cut short that way is put right at the next load (ArmourySwitch.on_load: recovery or the P8
        # re-stop).
        loop = asyncio.get_running_loop()
        while self.armoury is not None and self.armoury.busy and loop.time() < self.armoury.until:
            await asyncio.sleep(0.05)
        for task in (self._startup, self._late_start):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        # Each step runs even when an earlier one failed. Gamepad first, whatever the stored mode, while the helper
        # still runs: Big Picture must never be left with a mouse-only controller. Nothing at all while an old
        # Overdrive drives the controller.
        if self.controller_auto is not None and not self._old:
            await _unload_step("put the controller back to Gamepad", self.controller_auto.stop())
        if self.reapplier is not None:
            await _unload_step("stop the reapplier", self.reapplier.stop())
        if self.client is not None:
            await _unload_step("stop the helper", self.client.stop())
        decky.logger.info("Traction unloaded")

    def _startup_done(self) -> bool:
        return self._startup is None or self._startup.done()

    # ---------- reads ----------

    async def get_support(self) -> dict:
        product = device.product_name()
        return {"supported": device.is_ally(lambda: product), "product": product}

    async def get_state(self) -> dict:
        support = await self.get_support()
        s = self.settings or Settings("")
        state = {
            "support": support,
            "armoury": {"installed": False, "replaced": False, "warning": ""},
            "controller": {"mode": s.controller_mode, "found": False},
            "vibration": bool(s.grip_vibration),
            "touchscreen": dict(UNREAD["touchscreen"]),
            "blocked": "", "note": "", "busy": False, "warning": "", "unavailable": "",
        }
        if self.client is None:
            return state
        self._check_overdrive_updated()
        state["blocked"] = UPDATE_NOTE if self._old else ""
        state["busy"] = self._busy()
        replaced = False
        if self.armoury is not None:
            replaced = self.armoury.replaced()   # the shared record: either plugin may switch
            if not state["busy"]:   # never queue behind a long service switch
                await self.armoury.check_installed()
            state["armoury"] = {"installed": self.armoury.installed, "replaced": replaced,
                                "warning": self.armoury.warning}
        state["note"] = "" if replaced else ARMOURY_RUNNING
        if not state["busy"]:
            controller, r = await controls.read_controller(self.client)
            touch = (self._controls or UNREAD)["touchscreen"]
            if not controls.transport_failure(r):
                touch = await controls.touchscreen_status(self.client)
                self._controls = {"controller": controller, "touchscreen": dict(touch)}
            elif self._controls is not None:
                controller = self._controls["controller"]
            # else: the helper is gone or hung; the touchscreen read would wait its 15 s. The last reading is shown
            # and read again at the next poll.
            state["controller"]["found"] = bool(controller["found"])
            state["touchscreen"] = {"found": bool(touch["found"]), "enabled": bool(touch["enabled"])}
        elif self._controls is not None:   # while busy, the last reading: a call would queue behind the switch
            state["controller"]["found"] = bool(self._controls["controller"]["found"])
            state["touchscreen"] = dict(self._controls["touchscreen"])
        state["unavailable"] = self.client.unavailable_reason or ""
        state["warning"] = " ".join(w for w in (s.warning, self.reapplier.warning) if w)
        return state

    # ---------- user changes ----------

    def _save(self) -> str:
        try:
            self.settings.save()
            return ""
        except OSError as e:
            decky.logger.error(f"Could not save Traction's settings: {e}")
            return f"Applied, but the setting could not be saved ({e})."

    async def _controller_found(self) -> bool:
        r = await self.client.call("controller_info", {})
        d = r.get("data") if r.get("ok") else None
        return isinstance(d, dict) and bool(d.get("found"))

    async def set_controller_mode(self, mode) -> dict:
        """Gamepad, Desktop or Auto. Saved once written, or when no controller is found (it applies when the
        controller is back). Auto writes Gamepad, then the Controller Auto loop decides."""
        refused = self._refuse_controller()
        if refused:
            return refused
        if mode not in CONTROLLER_MODES:
            return _fail("Controller mode must be gamepad, desktop or auto.")
        s, ca = self.settings, self.controller_auto
        # R10: stop() takes the reapplier's lock (not reentrant), so a running loop is stopped before user_change.
        # It writes Gamepad; Auto chosen again starts the loop afresh below.
        if ca is not None and ca.running:
            await ca.stop()
        result = None
        async with self.reapplier.user_change():
            if self._busy():   # an Armoury switch may have begun while this waited for the lock
                result = _fail(BUSY)
            else:
                note = ""
                if await self._controller_found():
                    r = await controls.apply_controller(self.client, s, mode)
                    if not r.get("ok"):
                        result = _fail(str(r.get("message") or "The controller mode was not changed."))
                else:
                    note = NO_CONTROLLER
                if result is None:
                    s.controller_mode = mode
                    s.user_set = True   # a user choice: reapplies (resume, helper restart) write it again
                    result = {"ok": True, "message": " ".join(m for m in (note, self._save()) if m)}
        if ca is not None:
            if s.controller_mode == "auto":
                ca.start()   # Auto chosen, or a failed change away from Auto: the loop goes on
            elif ca.running:
                # A concurrent Auto choice started the loop after this call stopped it, then this call stored
                # Gamepad or Desktop. Stopped here, outside the lock (R10). The mode written is read under the lock
                # (C9), so a choice made while stop() waited for it is the one written.
                await ca.stop(lambda: s.controller_mode)
        return result

    async def set_grip_vibration(self, on) -> dict:
        refused = self._refuse_controller()
        if refused:
            return refused
        if not isinstance(on, bool):
            return _fail("Expected on or off.")
        s = self.settings
        async with self.reapplier.user_change():
            if self._busy():   # an Armoury switch may have begun while this waited for the lock
                return _fail(BUSY)
            note = ""
            if await self._controller_found():
                # Only the vibration is written: the controller mode (and a running Auto loop) is left alone.
                r = await self.client.call("set_vibration", {"strength": 100 if on else 0})
                if not r.get("ok"):
                    return _fail(str(r.get("message") or "The grip vibration was not changed."))
            else:
                note = NO_CONTROLLER
            s.grip_vibration = on
            s.user_set = True   # reapplied with the controller mode after sleep
            return {"ok": True, "message": " ".join(m for m in (note, self._save()) if m)}

    async def set_touchscreen(self, enabled) -> dict:
        """Works whatever Overdrive's version (Overdrive 0.1.0's touchscreen toggle is the same Windows device)."""
        refused = self._refuse_write()
        if refused:
            return refused
        if not isinstance(enabled, bool):
            return _fail("Expected on or off.")
        async with self.reapplier.user_change():
            if self._busy():   # an Armoury switch may have begun while this waited for the lock
                return _fail(BUSY)
            r = await controls.set_touchscreen(self.client, enabled)
        return {"ok": bool(r.get("ok")), "message": str(r.get("message") or "")}

    # ---------- Armoury Crate ----------
    # The switch, the record and its rules are ArmourySwitch's (armoury_switch.py, shared with Overdrive).

    def _armoury_replaced(self) -> None:
        """Armoury Crate was stopped: Traction's settings are applied from now on ("then apply our stored
        settings", at the next poll)."""
        s = self.settings
        if not s.user_set:
            s.user_set = True
            self._save()
        self.reapplier.rerun = True

    async def set_armoury_replaced(self, on) -> dict:
        # An Overdrive older than 0.2.0 keeps the Armoury Crate record in its own settings: switching here would
        # work on a record it does not read, so the toggle is read-only until it is updated.
        refused = self._refuse_controller()
        if refused:
            return refused
        if not isinstance(on, bool):
            return _fail("Expected on or off.")
        return await self.armoury.set(on)


async def _unload_step(what: str, coro) -> None:
    """One unload step; a failure is logged and the later steps still run."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as e:
        decky.logger.error(f"Traction could not {what} at unload: {e}")
