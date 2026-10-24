import asyncio
import os
import sys

import decky  # type: ignore

# The loader appends py_modules to sys.path; older Windows builds did not, so make sure.
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PY_MODULES = os.path.join(PLUGIN_DIR, "py_modules")
if _PY_MODULES not in sys.path:
    sys.path.append(_PY_MODULES)

from overdrive import controls, device  # noqa: E402
from overdrive.armoury_record import RECORD_DIR, ArmouryLocked, ArmouryRecord  # noqa: E402
from overdrive.armoury_switch import ArmourySwitch, migrate_from_settings  # noqa: E402
from overdrive.auto_tdp_loop import AutoTdpLoop  # noqa: E402
from overdrive.helper_client import HelperClient  # noqa: E402
from overdrive.reapply import (Reapplier, fan_args, fan_note, fan_plan, mode_args, mode_write,  # noqa: E402
                               rewrite_fans)
from overdrive.cpu_usage import CpuUsage  # noqa: E402
from overdrive.settings import (ALLOWED_FPS, FANS, MODES, PRESETS, Settings,  # noqa: E402
                                check_curve, clamp_power, curve_mode, preset_curve)

NOT_ALLY = "Overdrive supports the ROG Ally family only"
BUSY = "Overdrive is switching Armoury Crate services. Try again when it finishes."
NOT_MIGRATED = ("Overdrive's Armoury Crate record could not be moved to the record shared with Traction yet "
                "({why}), so nothing was changed. Try again in a moment.")
MIGRATE_WAIT = 2.0               # how long a switch waits for the shared record's lock to move the old record
AUTO_TDP_UNSUPPORTED = "Auto TDP needs the AMD driver's frame-rate reading, which this driver does not offer."
# get_sensors().autoTdp when there is no loop (not an Ally)
AUTO_TDP_IDLE = {"on": False, "watts": None, "fps": None, "best": None, "paused": "", "supported": False}
# get_state's new controls when nothing was read (not an Ally, or nothing read yet while an Armoury switch runs)
CONTROLS_UNREAD = {"cpu_boost": {"on": False, "supported": False}, "power_mode": {"value": "other", "supported": False},
                   "auto_sr": {"installed": False, "on": None}}


def settings_path() -> str:
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "overdrive.json")


def record_dir() -> str:
    """The Armoury Crate record shared with Traction."""
    return RECORD_DIR


def _fail(message: str) -> dict:
    return {"ok": False, "message": message}


def _int(v):
    """An int from the panel (JSON numbers may arrive as whole floats), else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def _int_list(v):
    if not isinstance(v, list):
        return None
    out = [_int(x) for x in v]
    return None if any(x is None for x in out) else out


class Plugin:
    def __init__(self) -> None:
        self.client = None
        self.settings = None
        self.reapplier = None
        self.auto_tdp_loop = None
        self.armoury = None              # the Armoury Crate switch (its record is shared with Traction)
        self._controls = None            # the last read_controls reply (shown while an Armoury switch runs)
        self._startup = None
        self._late_load = None           # the load-time Armoury check, once a late move of the old record worked
        self._migrate_error = ""         # why the old Armoury record is not in the shared record yet
        self._migrate_locked = False     # ... because another plugin held the shared record's lock
        self.cpu_usage = CpuUsage()      # CPU % between two sensor polls (None on the first)

    def _attach(self, client, settings, reapplier, record=None) -> None:
        self.client, self.settings, self.reapplier = client, settings, reapplier
        if client is not None and record is not None:
            self.armoury = ArmourySwitch(client, record, "Overdrive", logger=decky.logger,
                                         on_replaced=self._armoury_replaced)
        if reapplier is not None:
            reapplier.busy_check = self._busy
            if client is not None:
                self.auto_tdp_loop = AutoTdpLoop(client, settings, reapplier, logger=decky.logger)
                # a reapply's mode write landed: restart at the ceiling it set
                reapplier.on_applied = self.auto_tdp_loop.after_mode_write

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

    async def _main(self) -> None:
        if not device.is_ally():
            decky.logger.info(f"Overdrive: not an Ally ({device.product_name()!r}); helper not started")
            return
        settings = Settings.load(settings_path(), logger=decky.logger)   # an unreadable file is kept, never lost
        runtime_dir = getattr(decky, "DECKY_PLUGIN_RUNTIME_DIR", "") or None
        client = HelperClient(plugin_dir=PLUGIN_DIR, runtime_dir=runtime_dir, logger=decky.logger)
        # Two views of one record file: the switch's (changed only under its locks) and the reapplier's (read only).
        folder = record_dir()
        reapplier = Reapplier(client, settings, logger=decky.logger, record=ArmouryRecord(folder, decky.logger))
        self._attach(client, settings, reapplier, ArmouryRecord(folder, decky.logger))
        client.start()
        self._startup = asyncio.ensure_future(self._startup_sequence())
        decky.logger.info("Overdrive started")

    def _migrate_armoury(self, timeout: float = 0.0) -> bool:
        """Overdrive 0.1 kept the Armoury Crate record in overdrive.json: move it to the shared record once (only when
        that does not exist yet), then save the settings without it. Until the move worked the old fields stay in
        overdrive.json, so a failed move never loses them, and the switch is refused (the shared record would be
        empty while services may be stopped). True when nothing is left to move."""
        s = self.settings
        if not s.armoury_pending():
            return True
        dropped = (f"armoury_replaced={s.armoury_replaced!r}, armoury_recorded={dict(s.armoury_recorded)!r}, "
                   f"armoury_running={list(s.armoury_running)!r}")
        try:
            moved = migrate_from_settings(self.armoury.record, s, timeout)
        except (ArmouryLocked, OSError) as e:
            if str(e) != self._migrate_error:   # logged once per reason, not at every poll
                decky.logger.warning(f"Overdrive: the Armoury Crate record was not moved to the shared record yet: {e}")
            self._migrate_error = str(e) or type(e).__name__
            self._migrate_locked = isinstance(e, ArmouryLocked)
            return False
        self._migrate_error = ""
        if moved:
            decky.logger.info("Overdrive: the Armoury Crate record was moved to the shared record")
        else:
            # The values go to the log so the originals can be recovered by hand if the shared record was wrong.
            decky.logger.warning("Overdrive: the shared Armoury Crate record already holds a record; the old record "
                                 f"in overdrive.json was dropped: {dropped}")
        s.armoury_migrated()
        self._save()
        return True

    async def _migrate_armoury_waiting(self) -> bool:
        """_migrate_armoury, waiting up to MIGRATE_WAIT for the shared record's lock without blocking the loop."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + MIGRATE_WAIT
        while True:
            if self._migrate_armoury(0.0):
                return True
            if not self._migrate_locked or loop.time() >= deadline:
                return False
            await asyncio.sleep(0.05)

    def _old_armoury_note(self) -> str:
        if self.settings is None or not self.settings.armoury_pending():
            return ""
        return ("Overdrive's Armoury Crate record could not be moved to the record shared with Traction yet "
                f"({self._migrate_error}); the Armoury Crate switch waits until it can.")

    async def _startup_sequence(self) -> None:
        if self.armoury is not None:
            # The load-time check (P8 re-stop, recovery) runs on the shared record only: while the old record could
            # not be moved there it waits, and runs once a later move works (get_state, or the switch).
            if await self._migrate_armoury_waiting():
                await self.armoury.on_load()
        self.reapplier.start()
        if self.settings.auto_tdp and self.auto_tdp_loop is not None:
            self.auto_tdp_loop.start()   # the reapply just started writes the mode; on_applied resets the loop then

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
        for task in (self._startup, self._late_load):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        # Each step runs even when an earlier one failed.
        if self.auto_tdp_loop is not None:
            # Never leave the Ally at trimmed watts: write the mode again when Auto TDP is running or stored on (a
            # loop halted by a refusal may have left trimmed watts if its restore failed). With Auto TDP off nothing
            # is written.
            restore = bool(self.auto_tdp_loop.status()["on"] or (self.settings is not None and self.settings.auto_tdp))
            await _unload_step("stop Auto TDP", self.auto_tdp_loop.stop(restore=restore))
        if self.reapplier is not None:
            await _unload_step("stop the reapplier", self.reapplier.stop())
        if self.client is not None:
            await _unload_step("stop the helper", self.client.stop())
        decky.logger.info("Overdrive unloaded")

    # ---------- reads ----------

    async def get_support(self) -> dict:
        product = device.product_name()
        return {"supported": device.is_ally(lambda: product), "product": product}

    def _effective_curve(self, s: Settings, fan: str = "cpu"):
        """The curve this fan runs with now: the custom or preset curve, else the device's own (raised to the safe
        minimum when needed); None when it is not known."""
        curve, _, _ = fan_plan(s, fan)
        if curve is not None:
            return curve
        if s.fan_preset in ("default", "custom"):
            base = (s.default_curves(fan) or {}).get(curve_mode(s.mode))
            return preset_curve("default", base)[0]
        return None

    async def get_state(self) -> dict:
        support = await self.get_support()
        s = self.settings or Settings("")
        state = {
            "support": support, "mode": s.mode, "spl": s.spl, "sppt": s.sppt, "fppt": s.fppt, "fps": s.fps,
            "fan_preset": s.fan_preset, "fan_curve": self._effective_curve(s, "cpu"),
            "fan_curve_gpu": self._effective_curve(s, "gpu"),
            "armoury": {"installed": False, "replaced": False},
            "unavailable": "", "warning": "", "fan_note": "", "busy": False, "user_set": s.user_set,
            # The owner's own limits exist only once Manual was chosen; otherwise the firmware's limits apply.
            "power_set": bool(s.user_set and s.mode == "manual"),
            "device": _device_readback(None),
            "auto_tdp": {"on": False, "supported": False},
        }
        _merge_controls(state, CONTROLS_UNREAD, s)
        if self.client is None:
            return state
        if self.auto_tdp_loop is not None:   # the stored switch; "supported" is False once the driver had no reading
            state["auto_tdp"] = {"on": bool(s.auto_tdp), "supported": bool(self.auto_tdp_loop.status()["supported"])}
        state["busy"] = self._busy()
        if self.armoury is not None and s.armoury_pending() and not state["busy"] and self._startup_done():
            # a quick retry of the move (no waiting); then the load-time check it held back
            if self._migrate_armoury(0.0):
                self._late_load = asyncio.ensure_future(self.armoury.on_load())
        if self.armoury is not None and s.armoury_pending():
            state["armoury"]["replaced"] = bool(s.armoury_replaced)   # the old record says, until it is moved
        elif self.armoury is not None:
            state["armoury"]["replaced"] = self.armoury.replaced()   # the shared record: either plugin may switch
            if not state["busy"]:   # never queue behind a long service switch
                await self.armoury.check_installed()
        if not state["busy"]:
            r = await self.client.call("status")   # reads only; shown, never written back
            if r.get("ok"):
                state["device"] = _device_readback(r.get("data"))
            if not controls.transport_failure(r):
                self._controls = await controls.read_controls(self.client)
            # else: the helper is gone or hung; five more calls would each wait their timeout. The last reading
            # is shown and the controls are read again at the next poll.
        if self._controls is not None:   # while busy, the last reading: a call would queue behind the switch
            _merge_controls(state, self._controls, s)
        state["armoury"]["installed"] = bool(self.armoury is not None and self.armoury.installed)
        state["unavailable"] = self.client.unavailable_reason or ""
        armoury_warning = self.armoury.warning if self.armoury is not None else ""
        state["warning"] = " ".join(w for w in (s.warning, self.reapplier.warning, armoury_warning,
                                                self._old_armoury_note()) if w)
        state["fan_note"] = fan_note(s)
        return state

    async def get_sensors(self) -> dict:
        if self.client is None:
            return {"ok": False, "message": NOT_ALLY, "cpuTempC": None, "fanRpm": None, "gpuFanRpm": None,
                    "gpuTempC": None, "cpuUse": None, "gpuUse": None, "cpuPowerW": None, "gpuPowerW": None,
                    "errors": [], "autoTdp": dict(AUTO_TDP_IDLE)}
        r = await self.client.call("sensors", None, 5.0)
        d = r.get("data") if r.get("ok") and isinstance(r.get("data"), dict) else {}
        out = {"ok": bool(r.get("ok")), "message": r.get("message") or ""}
        # The helper's readings (read only); cpuUse is measured here (GetSystemTimes between two polls).
        for k in ("cpuTempC", "fanRpm", "gpuFanRpm", "gpuTempC"):
            out[k] = d.get(k)
        out["cpuUse"] = self.cpu_usage.sample()
        for k in ("gpuUse", "cpuPowerW", "gpuPowerW"):
            out[k] = d.get(k)
        out["errors"] = list(d.get("errors") or [])
        out["autoTdp"] = self.auto_tdp_loop.status() if self.auto_tdp_loop is not None else dict(AUTO_TDP_IDLE)
        return out

    # ---------- user changes ----------

    def _save(self) -> str:
        try:
            self.settings.save()
            return ""
        except OSError as e:
            decky.logger.error(f"Could not save Overdrive's settings: {e}")
            return f"Applied, but the setting could not be saved ({e})."

    async def _rewrite_fans(self) -> str:
        """After a mode write the firmware curve of both fans is back; put the chosen preset or custom curves on
        again (CPU, then GPU)."""
        self.reapplier.fan_note = fan_note(self.settings)
        msg, _ = await rewrite_fans(self.client, self.settings)
        return msg

    def _auto_tdp_reset(self, mode_written: bool) -> None:
        """After a user write that set the mode's watts (mode_written) or the FPS limit: Auto TDP restarts from the
        new ceiling and limit (sync, idle when stopped; safe inside user_change)."""
        if self.auto_tdp_loop is not None:
            self.auto_tdp_loop.reset(mode_written=mode_written)

    async def set_mode(self, mode, spl=None, sppt=None, fppt=None) -> dict:
        refused = self._refuse_write()
        if refused:
            return refused
        if mode not in MODES:
            return _fail("Mode must be silent, balanced, turbo or manual.")
        s = self.settings
        if mode == "manual":
            vals = [s.spl if spl is None else _int(spl), s.sppt if sppt is None else _int(sppt),
                    s.fppt if fppt is None else _int(fppt)]
            if any(v is None for v in vals):
                return _fail("Power limits must be whole watts.")
            args = mode_write(mode, clamp_power(*vals))
        elif spl is not None or sppt is not None or fppt is not None:
            return _fail("Power limits can only be set in Manual mode.")
        else:
            args = mode_write(mode)
        async with self.reapplier.user_change():
            if self._busy():   # an Armoury switch may have begun while this waited for the lock
                return _fail(BUSY)
            r = await self.client.call("set_mode", args)
            if not r.get("ok"):
                return _fail(str(r.get("message")))
            d = r.get("data") if isinstance(r.get("data"), dict) else {}
            s.mode = mode
            if mode == "manual":
                s.spl, s.sppt, s.fppt = clamp_power(_int(d.get("spl")) or args["spl"],
                                                    _int(d.get("sppt")) or args["sppt"],
                                                    _int(d.get("fppt")) or args["fppt"])
            s.user_set = True
            msgs = [await self._rewrite_fans(), self._save()]
            self._auto_tdp_reset(mode_written=True)
        return {"ok": True, "message": " ".join(m for m in msgs if m)}

    async def set_fps(self, fps) -> dict:
        refused = self._refuse_write()
        if refused:
            return refused
        fps = _int(fps)
        if fps not in ALLOWED_FPS:
            return _fail("FPS limit must be off or one of 30, 40, 45, 50, 60, 75, 90, 120.")
        async with self.reapplier.user_change():
            if self._busy():   # an Armoury switch may have begun while this waited for the lock
                return _fail(BUSY)
            r = await self.client.call("set_fps_limit", {"fps": fps})   # 0 = off at the protocol level
            if not r.get("ok"):
                return _fail(str(r.get("message")))
            self.settings.fps = fps
            self.settings.user_set = True
            self._auto_tdp_reset(mode_written=False)   # the watts on the device are unchanged
            return {"ok": True, "message": self._save()}

    async def set_fan_preset(self, name) -> dict:
        refused = self._refuse_write()
        if refused:
            return refused
        s = self.settings
        if name not in PRESETS:
            return _fail("Fan preset must be default, recommended, quiet, cool or custom.")
        if name == "custom" and not s.has_custom():
            return _fail("There is no custom fan curve yet; use Edit curve first.")
        async with self.reapplier.user_change():
            if self._busy():   # an Armoury switch may have begun while this waited for the lock
                return _fail(BUSY)
            old = s.fan_preset
            s.fan_preset = name
            plans = {fan: fan_plan(s, fan) for fan in FANS}
            if plans["cpu"][2]:   # the CPU fan's preset cannot be made: change nothing
                s.fan_preset = old
                return _fail(plans["cpu"][2])
            # The GPU fan's preset cannot be made (e.g. its default curve was never read): it keeps the device's
            # own curve and the reply says so.
            warns = [plans["gpu"][2]] if plans["gpu"][2] else []
            writes = []
            if any(curve is None for curve, _, _ in plans.values()):
                # The device's own curve: writing the mode again puts back both fans' firmware curves.
                writes.append(("set_mode", mode_args(s)))
            for fan in FANS:
                curve = plans[fan][0]
                if curve is not None:
                    writes.append(("set_fans", fan_args(curve, fan)))
            for cmd, args in writes:
                r = await self.client.call(cmd, args)
                if not r.get("ok"):
                    s.fan_preset = old
                    return _fail(str(r.get("message")))
            self.reapplier.fan_note = fan_note(s)
            s.user_set = True
            if writes and writes[0][0] == "set_mode":   # the mode's watts were written again
                self._auto_tdp_reset(mode_written=True)
            return {"ok": True, "message": " ".join(m for m in warns + [self._save()] if m)}

    async def set_fan_curve(self, temps, speeds, fan="cpu") -> dict:
        """One fan's custom curve ("cpu" or "gpu"). The preset becomes custom; the other fan keeps the curve it runs
        with now (its preset curve becomes its custom curve when the preset was not custom yet)."""
        refused = self._refuse_write()
        if refused:
            return refused
        if fan not in FANS:
            return _fail("Fan must be cpu or gpu.")
        t, sp = _int_list(temps), _int_list(speeds)
        reason = check_curve(t, sp) if t is not None and sp is not None else "The fan curve needs 8 points."
        if reason:
            return _fail(reason)
        async with self.reapplier.user_change():
            if self._busy():   # an Armoury switch may have begun while this waited for the lock
                return _fail(BUSY)
            curve = {"temps": t, "speeds": sp}
            r = await self.client.call("set_fans", fan_args(curve, fan))
            if not r.get("ok"):
                return _fail(str(r.get("message")))
            s = self.settings
            if s.fan_preset != "custom":
                for other in FANS:
                    if other != fan:
                        s.set_custom_curve(other, fan_plan(s, other)[0])
            s.set_custom_curve(fan, curve)
            s.fan_preset = "custom"
            s.user_set = True
            self.reapplier.fan_note = fan_note(s)
            return {"ok": True, "message": self._save()}

    async def reset_defaults(self) -> dict:
        """Balanced, factory limits, the device's default fan curve, FPS limit off. Armoury settings untouched."""
        refused = self._refuse_write()
        if refused:
            return refused
        s = self.settings
        # Armoury Crate's defaults have no Auto TDP, so it goes off. Stopped here, before user_change: stop() may
        # wait for a loop write holding the reapplier's lock, which is not reentrant. No restore: Balanced follows.
        auto_was_on, _ = await self._auto_tdp_off(restore=False)   # a failed save shows in the final save message
        async with self.reapplier.user_change():
            if self._busy():   # an Armoury switch may have begun while this waited for the lock
                if auto_was_on:
                    self.reapplier.rerun = True   # the watts may be trimmed: the next poll writes the mode again
                return _fail(BUSY)
            r = await self.client.call("set_mode", mode_write("balanced"))
            if not r.get("ok"):
                if auto_was_on:
                    self.reapplier.rerun = True
                return _fail(str(r.get("message")))
            s.mode = "balanced"
            s.spl = s.sppt = s.fppt = 30
            s.fan_preset = "default"
            msgs = [await self._rewrite_fans()]
            r = await self.client.call("set_fps_limit", {"fps": 0})
            if r.get("ok"):
                s.fps = 0
            else:
                msgs.append("The FPS limit was not turned off: " + str(r.get("message")))
            s.user_set = True
            msgs.append(self._save())
            self._auto_tdp_reset(mode_written=True)
        return {"ok": True, "message": " ".join(m for m in msgs if m)}

    # ---------- Auto TDP ----------

    async def _auto_tdp_off(self, restore: bool) -> "tuple[bool, str]":
        """Stores the switch off and stops the loop (writing the mode again when `restore` and it was on). Never
        call inside user_change: stop() awaits the loop task, which may hold the reapplier's lock, and then takes
        that lock itself. (was on, stored or running; the save message)."""
        s, loop = self.settings, self.auto_tdp_loop
        was_on = bool(s.auto_tdp) or bool(loop is not None and loop.status()["on"])
        msg = ""
        if s.auto_tdp:
            s.auto_tdp = False
            msg = self._save()
        if loop is not None:
            await loop.stop(restore=restore and was_on)
        return was_on, msg

    async def set_auto_tdp(self, on) -> dict:
        """The Auto TDP switch: on starts the loop at the mode's watts; off stops it and writes the mode again."""
        refused = self._refuse_write()
        if refused:
            return refused
        if not isinstance(on, bool):
            return _fail("Expected on or off.")
        loop, s = self.auto_tdp_loop, self.settings
        if not on:
            _, msg = await self._auto_tdp_off(restore=True)
            return {"ok": True, "message": msg}
        if loop is None or not loop.status()["supported"]:
            return _fail(AUTO_TDP_UNSUPPORTED)
        async with self.reapplier.user_change():
            if self._busy():   # an Armoury switch may have begun while this waited for the lock
                return _fail(BUSY)
            s.auto_tdp = True
            s.user_set = True   # a user choice: reapplies run, and each one resets the loop
            msg = self._save()
        loop.start()
        # The watts on the device are not known (Armoury Crate's or an earlier session's may be lower): the next
        # reapply poll writes the mode and the fan curves, and its mode write restarts the loop at the ceiling.
        self.reapplier.rerun = True
        return {"ok": True, "message": msg}

    # ---------- Windows settings ----------

    async def _user_write(self, write) -> dict:
        """A refusable user write under the reapplier's lock; `write` returns the coroutine making the reply."""
        refused = self._refuse_write()
        if refused:
            return refused
        async with self.reapplier.user_change():
            if self._busy():   # an Armoury switch may have begun while this waited for the lock
                return _fail(BUSY)
            r = await write()
        return {"ok": bool(r.get("ok")), "message": str(r.get("message") or "")}

    async def set_cpu_boost(self, on) -> dict:
        if not isinstance(on, bool):
            return self._refuse_write() or _fail("Expected on or off.")
        return await self._user_write(lambda: controls.set_cpu_boost(self.client, self.settings, on))

    async def set_power_mode(self, value) -> dict:
        if value not in controls.POWER_MODES:
            return self._refuse_write() or _fail("Battery performance must be eco, balanced or best.")
        return await self._user_write(lambda: controls.set_power_mode(self.client, value))

    async def set_auto_sr(self, on) -> dict:
        if not isinstance(on, bool):
            return self._refuse_write() or _fail("Expected on or off.")
        return await self._user_write(lambda: controls.set_auto_sr(self.client, on))

    # ---------- Armoury Crate ----------
    # The switch, the record and its rules are ArmourySwitch's (armoury_switch.py, shared with Traction).

    def _armoury_replaced(self) -> None:
        """Armoury Crate was stopped: Overdrive's settings are applied from now on ("then apply our stored
        settings", at the next poll)."""
        s = self.settings
        if not s.user_set:
            s.user_set = True
            self._save()
        self.reapplier.rerun = True

    async def set_armoury_replaced(self, on) -> dict:
        refused = self._refuse_write()
        if refused:
            return refused
        if not isinstance(on, bool):
            return _fail("Expected on or off.")
        # Never switch on an empty shared record while the old one (perhaps with services stopped) is not moved yet:
        # the start types read now would be the stopped ones, and the original ones would then be dropped.
        if self.settings.armoury_pending() and not await self._migrate_armoury_waiting():
            return _fail(NOT_MIGRATED.format(why=self._migrate_error))
        return await self.armoury.set(on)

    def _startup_done(self) -> bool:
        return self._startup is None or self._startup.done()


DEVICE_MODES = ("silent", "balanced", "turbo", "other")


async def _unload_step(what: str, coro) -> None:
    """One unload step; a failure is logged and the later steps still run."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as e:
        decky.logger.error(f"Overdrive could not {what} at unload: {e}")


def _merge_controls(state: dict, read: dict, s: Settings) -> None:
    """get_state's new controls from a read_controls reply."""
    for key in ("cpu_boost", "power_mode", "auto_sr"):
        state[key] = dict(read.get(key) or CONTROLS_UNREAD[key])


def _device_readback(data) -> dict:
    """What the device reports (helper status): mode silent/balanced/turbo, "other" for a mode Overdrive does not set
    (Armoury Crate's own, e.g. raw 7), or None; power limits in watts, None when not reported (0 on the Ally)."""
    d = data if isinstance(data, dict) else {}
    out = {"mode": d.get("mode") if d.get("mode") in DEVICE_MODES else None}
    for key in ("spl", "sppt", "fppt"):
        v = _int(d.get(key))
        out[key] = v if v is not None and v > 0 else None
    return out

