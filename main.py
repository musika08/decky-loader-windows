import asyncio
import contextlib
import os
import sys

import decky  # type: ignore

# The loader appends py_modules to sys.path; older Windows builds did not, so make sure.
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PY_MODULES = os.path.join(PLUGIN_DIR, "py_modules")
if _PY_MODULES not in sys.path:
    sys.path.append(_PY_MODULES)

from overdrive import device  # noqa: E402
from overdrive.auto_tdp_loop import AutoTdpLoop  # noqa: E402
from overdrive.helper_client import HelperClient  # noqa: E402
from overdrive.reapply import (Reapplier, fan_args, fan_note, fan_plan, mode_args, mode_write,  # noqa: E402
                               rewrite_fans)
from overdrive.cpu_usage import CpuUsage  # noqa: E402
from overdrive.settings import (ALLOWED_FPS, ARMOURY_SERVICES, FANS, MODES, PRESETS, START_TYPES,  # noqa: E402
                                Settings, check_curve, clamp_power, curve_mode, preset_curve)

NOT_ALLY = "Overdrive supports the ROG Ally family only"
BUSY = "Overdrive is switching Armoury Crate services. Try again when it finishes."
AUTO_TDP_UNSUPPORTED = "Auto TDP needs the AMD driver's frame-rate reading, which this driver does not offer."
# get_sensors().autoTdp when there is no loop (not an Ally)
AUTO_TDP_IDLE = {"on": False, "watts": None, "fps": None, "best": None, "paused": "", "supported": False}
ARMOURY_MIN_TIMEOUT = 300.0      # ruling P8: armoury_* calls get at least 5 minutes
ARMOURY_PER_SERVICE = 60.0       # each service may wait 30 s, doubled when a rollback runs


def settings_path() -> str:
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "overdrive.json")


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
        self._startup = None
        self._armoury_lock = asyncio.Lock()
        self._armoury_installed = None
        self._armoury_warning = ""
        self.cpu_usage = CpuUsage()      # CPU % between two sensor polls (None on the first)
        self._armoury_busy = False       # an Armoury switch or the load-time re-stop is running
        self._armoury_until = 0.0        # loop time by which that operation's helper call gives up

    def _attach(self, client, settings, reapplier) -> None:
        self.client, self.settings, self.reapplier = client, settings, reapplier
        if reapplier is not None:
            reapplier.busy_check = self._busy
            if client is not None:
                self.auto_tdp_loop = AutoTdpLoop(client, settings, reapplier, logger=decky.logger)
                # a reapply's mode write landed: restart at the ceiling it set
                reapplier.on_applied = self.auto_tdp_loop.after_mode_write

    def _busy(self) -> bool:
        return self._armoury_busy or bool(getattr(self.client, "busy", False))

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
        self._attach(client, settings, Reapplier(client, settings, logger=decky.logger))
        client.start()
        self._startup = asyncio.ensure_future(self._startup_sequence())
        decky.logger.info("Overdrive started")

    async def _startup_sequence(self) -> None:
        if self.settings.armoury_replaced:
            await self._restop_on_load()
        elif self.settings.armoury_recorded:
            await self._recover_on_load()
        self.reapplier.start()
        if self.settings.auto_tdp and self.auto_tdp_loop is not None:
            self.auto_tdp_loop.start()   # the reapply just started writes the mode; on_applied resets the loop then

    async def _unload(self) -> None:
        # Try not to kill the helper halfway through changing services: wait for the Armoury operation to finish,
        # bounded by its own call timeout. Decky does not wait that long: it kills the plugin process 5 s after
        # asking it to stop (decky-loader plugin/plugin.py kill_if_still_running), and the helper runs in the
        # plugin's job object with KILL_ON_JOB_CLOSE (localplatform/winprocess.py _create_job), so it dies too.
        # A switch cut short that way is put right at the next load (_recover_on_load, or the P8 re-stop).
        loop = asyncio.get_running_loop()
        while self._armoury_busy and loop.time() < self._armoury_until:
            await asyncio.sleep(0.05)
        if self._startup is not None and not self._startup.done():
            self._startup.cancel()
            try:
                await self._startup
            except (asyncio.CancelledError, Exception):
                pass
        if self.auto_tdp_loop is not None:
            # Never leave the Ally at trimmed watts: write the mode again when Auto TDP is running or stored on (a
            # loop halted by a refusal may have left trimmed watts if its restore failed). With Auto TDP off nothing
            # is written.
            restore = bool(self.auto_tdp_loop.status()["on"] or (self.settings is not None and self.settings.auto_tdp))
            await self.auto_tdp_loop.stop(restore=restore)
        if self.reapplier is not None:
            await self.reapplier.stop()
        if self.client is not None:
            await self.client.stop()
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
            "armoury": {"installed": False, "replaced": s.armoury_replaced},
            "unavailable": "", "warning": "", "fan_note": "", "busy": False, "user_set": s.user_set,
            # The owner's own limits exist only once Manual was chosen; otherwise the firmware's limits apply.
            "power_set": bool(s.user_set and s.mode == "manual"),
            "device": _device_readback(None),
            "auto_tdp": {"on": False, "supported": False},
        }
        if self.client is None:
            return state
        if self.auto_tdp_loop is not None:   # the stored switch; "supported" is False once the driver had no reading
            state["auto_tdp"] = {"on": bool(s.auto_tdp), "supported": bool(self.auto_tdp_loop.status()["supported"])}
        state["busy"] = self._busy()
        if self._armoury_installed is None and not state["busy"]:   # never queue behind a long service switch
            r = await self.client.call("armoury_state")
            if r.get("ok") and isinstance(r.get("data"), dict):
                self._armoury_installed = bool(r["data"].get("installed"))
        if not state["busy"]:
            r = await self.client.call("status")   # reads only; shown, never written back
            if r.get("ok"):
                state["device"] = _device_readback(r.get("data"))
        state["armoury"]["installed"] = bool(self._armoury_installed)
        state["unavailable"] = self.client.unavailable_reason or ""
        state["warning"] = " ".join(w for w in (s.warning, self.reapplier.warning, self._armoury_warning) if w)
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

    # ---------- Armoury Crate ----------
    # The record (start types + running services before Overdrive stopped anything) is what armoury_restore puts
    # back, so it must exist on disk before the first service is touched and must never be replaced by types read
    # after a stop. A record with the switch off is provisional (a switch-on that never got its reply); switching
    # off with one still restores it, and the next switch-on keeps it.

    @contextlib.asynccontextmanager
    async def _armoury_op(self):
        async with self._armoury_lock:
            yield

    async def _armoury_timeout(self, extra_running: int):
        """(timeout, armoury_state data or None, failure message). armoury_state is an ordinary read (normal timeout,
        not busy). Timeout = max(5 min, 60 s per listed service running now + 60 s); all listed services count when
        the state is unknown."""
        r = await self.client.call("armoury_state")
        d = r.get("data") if r.get("ok") and isinstance(r.get("data"), dict) else None
        if d is not None:
            self._armoury_installed = bool(d.get("installed"))
            n = len(d.get("running") or [])
        else:
            n = len(ARMOURY_SERVICES)
        n = max(n, extra_running)
        message = "" if d is not None else str(r.get("message") or "no answer")
        return max(ARMOURY_MIN_TIMEOUT, ARMOURY_PER_SERVICE * n + 60.0), d, message

    async def _armoury_call(self, cmd: str, args, timeout: float) -> dict:
        """armoury_stop / armoury_restore: busy (P12) for exactly as long as the call runs."""
        self._armoury_until = asyncio.get_running_loop().time() + timeout + 5.0
        self._armoury_busy = True
        try:
            return await self.client.call(cmd, args, timeout)
        finally:
            self._armoury_busy = False

    def _merge_new_services(self, data) -> bool:
        """Adds services that are missing from a non-empty record (installed later), never changing an entry."""
        s = self.settings
        if not s.armoury_recorded or not isinstance(data, dict):
            return False
        types, running = _clean_record(data)
        added = False
        for name, typ in types.items():
            if name not in s.armoury_recorded:
                s.armoury_recorded[name] = typ
                if name in running and name not in s.armoury_running:
                    s.armoury_running.append(name)
                added = True
        return added

    async def _restop_on_load(self) -> None:
        # Ruling P8: stopped-but-Automatic services may have come back after a reboot, so stop them again while the
        # switch is on. Only the first switch-on records the start types; this run never overwrites them.
        async with self._armoury_op():
            timeout, state, why = await self._armoury_timeout(0)
            if state is None:
                # Never stop blind (an unknown state means the all-services timeout on a possibly hung helper).
                self._armoury_warning = ("Armoury Crate services could not be checked at load, so they were not "
                                         f"stopped again: {why}")
                decky.logger.warning(self._armoury_warning)
                return
            if self._merge_new_services(state):
                self._save()
            r = await self._armoury_call("armoury_stop", None, timeout)
            if r.get("ok"):
                self._armoury_warning = ""
            else:
                self._armoury_warning = "Armoury Crate services could not be stopped again: " + str(r.get("message"))
                decky.logger.warning(self._armoury_warning)

    async def _recover_on_load(self) -> None:
        """A record with the switch off at load: a switch-on (or its save-failure restore) was cut short, typically
        by Decky killing the plugin 5 s into an unload. Some services may be stopped and Disabled/Manual, so put the
        recorded state back now (busy while it runs) and tell the owner how it went."""
        s = self.settings
        async with self._armoury_op():
            timeout, state, why = await self._armoury_timeout(len(s.armoury_running))
            if state is None:
                self._armoury_warning = ("A previous Armoury Crate switch did not finish, and the services could not "
                                         f"be checked at load ({why}). Turn the switch on and off again to put "
                                         "them back.")
                decky.logger.warning(self._armoury_warning)
                return
            decky.logger.info("Overdrive: a previous Armoury Crate switch did not finish; putting the services back")
            r = await self._armoury_call("armoury_restore",
                                         {"recorded": dict(s.armoury_recorded), "running": list(s.armoury_running)},
                                         timeout)
            if not r.get("ok"):
                self._armoury_warning = ("A previous Armoury Crate switch did not finish, and putting the services "
                                         f"back failed: {r.get('message')} Turn the switch on and off again to "
                                         "retry.")
                decky.logger.warning(self._armoury_warning)
                return
            s.armoury_recorded, s.armoury_running = {}, []
            msg = "A previous Armoury Crate switch did not finish, so Armoury Crate was put back."
            skipped = (r.get("data") or {}).get("skipped") if isinstance(r.get("data"), dict) else None
            if skipped:
                msg += " Not installed any more, skipped: " + ", ".join(map(str, skipped)) + "."
            saved = self._save()
            if saved:
                msg += " The cleared record could not be saved, so this runs again at the next load."
            self._armoury_warning = msg
            decky.logger.info(f"Overdrive: {msg}")

    async def set_armoury_replaced(self, on) -> dict:
        refused = self._refuse_write()
        if refused:
            return refused
        if not isinstance(on, bool):
            return _fail("Expected on or off.")
        async with self._armoury_op():
            return await (self._armoury_on() if on else self._armoury_off())

    async def _armoury_on(self) -> dict:
        s = self.settings
        if s.armoury_replaced:
            return {"ok": True, "message": ""}
        timeout, state, why = await self._armoury_timeout(0)
        if state is None:
            return _fail(f"Could not read the Armoury Crate services ({why}), so nothing was changed.")
        provisional = not s.armoury_recorded
        if provisional:
            types, running = _clean_record(state)
            s.armoury_recorded, s.armoury_running = types, running
            try:
                s.save()   # on disk before anything is stopped
            except OSError as e:
                s.armoury_recorded, s.armoury_running = {}, []
                return _fail(f"Could not save Overdrive's settings ({e}), so nothing was changed.")
        elif self._merge_new_services(state):
            self._save()

        r = await self._armoury_call("armoury_stop", None, timeout)
        if not r.get("ok"):
            d = r.get("data") if isinstance(r.get("data"), dict) else {}
            if provisional and d.get("rolledBack") is True:
                # The helper put every service back, so the provisional record is no longer needed.
                s.armoury_recorded, s.armoury_running = {}, []
                self._save()
            return _fail(str(r.get("message")))

        s.armoury_replaced, s.user_set = True, True
        try:
            s.save()   # the switch reports success only once this is on disk
        except OSError as e:
            s.armoury_replaced = False
            back = await self._armoury_call("armoury_restore",
                                            {"recorded": dict(s.armoury_recorded),
                                             "running": list(s.armoury_running)}, timeout)
            tail = ("Armoury Crate was put back." if back.get("ok")
                    else "Putting Armoury Crate back also failed: " + str(back.get("message")))
            return _fail(f"Could not save Overdrive's settings ({e}). {tail}")
        self._armoury_warning = ""
        self.reapplier.rerun = True   # "then apply our stored settings", at the next poll
        return {"ok": True, "message": ""}

    async def _armoury_off(self) -> dict:
        s = self.settings
        if not s.armoury_recorded:
            if not s.armoury_replaced:
                return {"ok": True, "message": ""}
            s.armoury_replaced = False
            msg = self._save()
            return {"ok": True, "message": ("No recorded Armoury Crate state was found, so the services were left as "
                                            "they are. " + msg).strip()}
        timeout, _, _ = await self._armoury_timeout(len(s.armoury_running))
        r = await self._armoury_call("armoury_restore",
                                     {"recorded": dict(s.armoury_recorded), "running": list(s.armoury_running)},
                                     timeout)
        if not r.get("ok"):
            return _fail(str(r.get("message")))
        s.armoury_replaced, s.armoury_recorded, s.armoury_running = False, {}, []
        self._armoury_warning = ""
        msgs = []
        skipped = (r.get("data") or {}).get("skipped") if isinstance(r.get("data"), dict) else None
        if skipped:
            msgs.append("Not installed any more, skipped: " + ", ".join(map(str, skipped)) + ".")
        try:
            s.save()
        except OSError as e:
            return _fail(f"Armoury Crate was restored, but the setting could not be saved ({e}).")
        return {"ok": True, "message": " ".join(msgs)}


DEVICE_MODES = ("silent", "balanced", "turbo", "other")


def _device_readback(data) -> dict:
    """What the device reports (helper status): mode silent/balanced/turbo, "other" for a mode Overdrive does not set
    (Armoury Crate's own, e.g. raw 7), or None; power limits in watts, None when not reported (0 on the Ally)."""
    d = data if isinstance(data, dict) else {}
    out = {"mode": d.get("mode") if d.get("mode") in DEVICE_MODES else None}
    for key in ("spl", "sppt", "fppt"):
        v = _int(d.get(key))
        out[key] = v if v is not None and v > 0 else None
    return out


def _clean_record(data: dict):
    """Start types and running list from an armoury_state/armoury_stop reply, limited to listed services."""
    types = {k: v for k, v in (data.get("startTypes") or {}).items()
             if k in ARMOURY_SERVICES and v in START_TYPES}
    running = []
    for n in data.get("running") or []:
        if isinstance(n, str) and n in types and n not in running:
            running.append(n)
    return types, running
