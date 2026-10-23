"""Puts the stored settings back on the device: on start, on resume from sleep and when AC power changes.

Every poll (5 s) compares the wall clock with the monotonic clock (a gap over 30 s means the PC slept; a poll that
took far longer than 5 s on the monotonic clock means the loop itself was suspended) and reads the AC line status.

User changes and reapply steps share one lock. A user change bumps `generation` before it waits for the lock, so a
reapply step that runs after the bump sees a newer generation and drops the rest of that reapply; the user's write
always lands last. A dropped reapply is redone (with the then-current settings) at the next poll. The same happens
while an armoury_* call is in flight (client.busy). The resume baseline is taken again after each reapply, so a slow
reapply is never mistaken for sleep.
"""
import asyncio
import contextlib
import logging
import time
from typing import Callable, Optional

from .settings import CURVE_MODES, Settings, curve_mode, preset_curve

POLL = 5.0
RESUME_GAP = 30.0


def ac_online_win32() -> Optional[bool]:
    """Win32 GetSystemPowerStatus: True on AC, False on battery, None when unknown or not on Windows."""
    try:
        import ctypes
        from ctypes import wintypes

        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
                        ("BatteryLifePercent", wintypes.BYTE), ("SystemStatusFlag", wintypes.BYTE),
                        ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]

        status = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None
        line = status.ACLineStatus & 0xFF
        return True if line == 1 else False if line == 0 else None   # 255 = unknown
    except Exception:
        return None


def fan_plan(s: Settings) -> "tuple[Optional[dict], str, str]":
    """(curve to write after the mode write or None, note, warning) for the stored mode and preset.

    Writing the performance mode puts the firmware curve back (G-Helper's fan reset), so the "default" preset needs
    no curve write unless the device curve sits below G-Helper's minimum (ruling P6: then the raised curve is
    written and the note says so)."""
    if s.fan_preset == "custom" and s.fan_curve:
        return s.fan_curve, "", ""
    base = (s.default_curve or {}).get(curve_mode(s.mode))
    if base is None:
        if s.fan_preset == "default":
            return None, "", ""
        return None, "", "The device's default fan curve is not known yet, so the fan preset was not applied."
    curve, adjusted = preset_curve(s.fan_preset, base)
    if curve is None:
        if s.fan_preset == "default":
            return None, "", ""
        return None, "", "The device's default fan curve is not usable, so the fan preset was not applied."
    note = "Adjusted to the safe minimum" if adjusted else ""
    if s.fan_preset == "default" and not adjusted:
        return None, note, ""
    return curve, note, ""


def mode_args(s: Settings) -> dict:
    args = {"mode": s.mode}
    if s.mode == "manual":
        args.update(spl=s.spl, sppt=s.sppt, fppt=s.fppt)
    return args


class Dropped(Exception):
    pass


class Reapplier:
    def __init__(self, client, settings: Settings, clock=time, power_source: Optional[Callable] = None,
                 poll: float = POLL, logger: Optional[logging.Logger] = None):
        self.client = client
        self.settings = settings
        self.clock = clock
        self.power_source = power_source or ac_online_win32
        self.poll = poll
        self.log = logger or logging.getLogger("overdrive")
        self.generation = 0
        self.lock = asyncio.Lock()
        self.rerun = False
        self.warning = ""
        self.fan_note = ""
        self._task: Optional[asyncio.Task] = None
        self._wall = self._mono = None
        self._ac: Optional[bool] = None

    # ---------- user changes ----------

    @contextlib.asynccontextmanager
    async def user_change(self):
        """Wrap every user-initiated write: bumps the generation first, then holds the shared lock."""
        self.generation += 1
        async with self.lock:
            yield

    # ---------- applying ----------

    def _busy(self) -> bool:
        return bool(getattr(self.client, "busy", False))

    async def _step(self, gen: int, cmd: str, args: dict) -> dict:
        async with self.lock:
            if gen != self.generation or self._busy():
                raise Dropped()
            return await self.client.call(cmd, args)

    async def read_default_curves(self) -> None:
        """First run: read each mode's firmware curve once (reads only) and store it."""
        if self.settings.default_curve:
            return
        curves = {}
        for m in CURVE_MODES:
            r = await self.client.call("get_fans", {"mode": m})
            data = r.get("data") if r.get("ok") else None
            if isinstance(data, dict) and isinstance(data.get("temps"), list) and isinstance(data.get("speeds"), list):
                curves[m] = {"temps": list(data["temps"]), "speeds": list(data["speeds"])}
            else:
                self.log.warning(f"Could not read the {m} fan curve: {r.get('message')}")
        if curves:
            self.settings.default_curve = curves
            try:
                self.settings.save()
            except OSError as e:
                self.log.warning(f"Could not save the default fan curves: {e}")

    async def apply_all(self) -> bool:
        """Applies the stored settings. False when dropped because of a newer user change."""
        gen = self.generation
        if self._busy():
            # An armoury_* call holds the single-threaded helper for minutes: queueing behind it would only time
            # out and then run later. Try again at the next poll.
            self.rerun = True
            return False
        await self.read_default_curves()
        s = self.settings
        if not s.user_set:
            self.log.info("Reapply skipped: no Overdrive settings chosen yet")
            return True
        problems = []
        try:
            r = await self._step(gen, "set_mode", mode_args(s))
            if not r.get("ok"):
                problems.append(r.get("message") or "The performance mode was not applied.")
            else:
                curve, note, warn = fan_plan(s)
                self.fan_note = note
                if warn:
                    problems.append(warn)
                if curve is not None:
                    r = await self._step(gen, "set_fans", {"temps": curve["temps"], "speeds": curve["speeds"]})
                    if not r.get("ok"):
                        problems.append(r.get("message") or "The fan curve was not applied.")
            if s.fps != 0:   # 0 (off) is only sent when the owner picks it, so an Adrenalin limit is left alone
                r = await self._step(gen, "set_fps_limit", {"fps": s.fps})
                if not r.get("ok"):
                    problems.append(r.get("message") or "The FPS limit was not applied.")
        except Dropped:
            self.rerun = True
            self.log.info("Reapply dropped: a newer change was made meanwhile")
            return False
        self.rerun = False
        self.warning = " ".join(problems)
        if problems:
            self.log.warning(f"Reapply incomplete: {self.warning}")
        else:
            self.log.info("Settings reapplied")
        return True

    # ---------- triggers ----------

    def prime(self) -> None:
        """Takes the current clocks and AC status as the baseline, without applying."""
        self._wall, self._mono = self.clock.time(), self.clock.monotonic()
        ac = self.power_source()
        if ac is not None:
            self._ac = ac

    def _triggers(self) -> "list[str]":
        reasons = []
        wall, mono = self.clock.time(), self.clock.monotonic()
        if self._wall is not None:
            d_wall, d_mono = wall - self._wall, mono - self._mono
            if d_wall - d_mono > RESUME_GAP or d_mono > self.poll + RESUME_GAP:
                reasons.append("resume")
        self._wall, self._mono = wall, mono
        ac = self.power_source()
        if ac is not None:
            if self._ac is not None and ac != self._ac:
                reasons.append("AC on" if ac else "AC off")
            self._ac = ac
        return reasons

    async def tick(self) -> bool:
        """One poll: True when a reapply ran."""
        reasons = self._triggers()
        if self.rerun:
            reasons.append("redo after a newer change")
        if not reasons:
            return False
        self.log.info(f"Reapplying ({', '.join(reasons)})")
        try:
            await self.apply_all()
        finally:
            # Re-baseline after the reapply: its own duration (a busy helper, a lock wait) is not sleep time.
            self._wall, self._mono = self.clock.time(), self.clock.monotonic()
        return True

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        self.prime()
        try:
            await self.apply_all()
        except Exception:
            self.log.exception("Reapply on start failed")
        self._wall, self._mono = self.clock.time(), self.clock.monotonic()
        while True:
            await asyncio.sleep(self.poll)
            try:
                await self.tick()
            except Exception:
                self.log.exception("Reapply failed")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
