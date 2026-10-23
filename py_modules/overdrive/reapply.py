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

from .helper_client import RESTARTED
from .settings import CURVE_MODES, FANS, MODE_POWER, Settings, curve_mode, preset_curve

POLL = 5.0
RESUME_GAP = 30.0
TRANSPORT_RETRIES = 3   # reapplies that hit a helper restart/timeout are retried at the next poll, at most this often


def transport_failure(reply: dict) -> bool:
    """A reply that failed on the way (helper restarted, did not answer, not running), not a device refusal."""
    if reply.get("ok"):
        return False
    msg = str(reply.get("message") or "")
    return (msg == RESTARTED or msg.startswith("Overdrive helper did not answer")
            or msg.startswith("Overdrive helper is not running"))


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


ADJUSTED = "Adjusted to the safe minimum"


def fan_plan(s: Settings, fan: str = "cpu") -> "tuple[Optional[dict], str, str]":
    """(curve to write after the mode write or None, note, warning) for one fan ("cpu" or "gpu"), the stored mode
    and the preset. Each fan's preset curve is derived from that fan's own default curve.

    Writing the performance mode puts the firmware curve of both fans back (G-Helper's fan reset), so the "default"
    preset needs no curve write unless the device curve sits below G-Helper's minimum (ruling P6: then the raised
    curve is written and the note says so). "recommended" is one fixed curve for both fans, written as is. Under
    "custom", a fan without a custom curve keeps its default curve."""
    custom = s.custom_curve(fan)
    if s.fan_preset == "custom" and custom:
        return custom, "", ""
    preset = "default" if s.fan_preset == "custom" else s.fan_preset
    if preset == "recommended":   # a fixed curve: written as is, no device curve needed
        return preset_curve(preset, None)[0], "", ""
    base = (s.default_curves(fan) or {}).get(curve_mode(s.mode))
    which, tail = ("", "") if fan == "cpu" else ("GPU ", " to the GPU fan")
    if base is None:
        if preset == "default":
            return None, "", ""
        return None, "", f"The device's default {which}fan curve is not known yet, so the fan preset was not applied{tail}."
    curve, adjusted = preset_curve(preset, base)
    if curve is None:
        if preset == "default":
            return None, "", ""
        return None, "", f"The device's default {which}fan curve is not usable, so the fan preset was not applied{tail}."
    note = ADJUSTED if adjusted else ""
    if preset == "default" and not adjusted:
        return None, note, ""
    return curve, note, ""


def fan_note(s: Settings) -> str:
    """The panel's note for both fans: which ones were raised to G-Helper's minimum."""
    adjusted = [f for f in FANS if fan_plan(s, f)[1]]
    if not adjusted:
        return ""
    if adjusted == ["cpu"]:
        return ADJUSTED
    if adjusted == ["gpu"]:
        return "GPU fan adjusted to the safe minimum"
    return "Both fans adjusted to the safe minimum"


def fan_args(curve: dict, fan: str) -> dict:
    """set_fans arguments; the CPU fan is the helper's default, so only the GPU fan is named."""
    args = {"temps": curve["temps"], "speeds": curve["speeds"]}
    if fan != "cpu":
        args["fan"] = fan
    return args


async def rewrite_fans(client, s: Settings) -> "tuple[str, bool]":
    """After a mode write the firmware curve of both fans is back; put the chosen preset or custom curves on again
    (CPU, then GPU, as G-Helper's AutoFans). Used by user changes (main.py) and Auto TDP's restore. The caller holds
    the reapplier's lock. (message for the owner, a set_fans write failed)."""
    msgs = []
    failed = False
    for fan in FANS:
        curve, _, warn = fan_plan(s, fan)
        if curve is None:
            if warn:
                msgs.append(warn)
            continue
        r = await client.call("set_fans", fan_args(curve, fan))
        if not r.get("ok"):
            failed = True
            what = "fan curve" if fan == "cpu" else "GPU fan curve"
            msgs.append(f"The {what} was not put back: " + str(r.get("message")))
    return " ".join(msgs), failed


def mode_args(s: Settings) -> dict:
    return mode_write(s.mode, (s.spl, s.sppt, s.fppt))


def mode_write(mode: str, manual_power=None) -> dict:
    """set_mode arguments: Manual with the user's limits, every other mode with its own watts (MODE_POWER)."""
    args = {"mode": mode}
    if mode == "manual":
        args.update(spl=manual_power[0], sppt=manual_power[1], fppt=manual_power[2])
    else:
        w = MODE_POWER[mode]
        args.update(spl=w, sppt=w, fppt=w)
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
        self.busy_check: Optional[Callable[[], bool]] = None   # main.py: an Armoury operation is running
        self._deferring = False
        self._retries = 0          # consecutive reapplies retried after a transport failure
        self.on_applied: Optional[Callable[[], None]] = None   # main.py: after a reapply's mode write landed
        self.applies = 0           # bumped when a reapply starts: Auto TDP drops a write sampled before it
        self.applying = 0          # reapplies in progress: Auto TDP writes nothing meanwhile

    # ---------- user changes ----------

    @contextlib.asynccontextmanager
    async def user_change(self):
        """Wrap every user-initiated write: bumps the generation first, then holds the shared lock."""
        self.generation += 1
        async with self.lock:
            yield

    # ---------- applying ----------

    def _busy(self) -> bool:
        if getattr(self.client, "busy", False):
            return True
        return bool(self.busy_check and self.busy_check())

    def _defer(self) -> None:
        """Remember to reapply once the Armoury operation is over; logged once per deferral, not every poll."""
        self.rerun = True
        if not self._deferring:
            self._deferring = True
            self.log.info("Reapply deferred while Armoury Crate services are being switched")

    async def _step(self, gen: int, cmd: str, args: dict) -> dict:
        async with self.lock:
            if gen != self.generation or self._busy():
                raise Dropped()
            return await self.client.call(cmd, args)

    async def read_default_curves(self) -> None:
        """Read each mode's firmware curve of each fan once (reads only) and store it. Modes that could not be read
        (a partial result) are read again at the next reapply; curves already stored are never read again."""
        changed = False
        for fan in FANS:
            have = dict(self.settings.default_curves(fan) or {})
            missing = [m for m in CURVE_MODES if m not in have]
            if not missing:
                continue
            curves = {}
            for m in missing:
                args = {"mode": m} if fan == "cpu" else {"mode": m, "fan": fan}
                r = await self.client.call("get_fans", args)
                data = r.get("data") if r.get("ok") else None
                if (isinstance(data, dict) and isinstance(data.get("temps"), list)
                        and isinstance(data.get("speeds"), list)):
                    curves[m] = {"temps": list(data["temps"]), "speeds": list(data["speeds"])}
                else:
                    self.log.warning(f"Could not read the {m} {fan.upper()} fan curve: {r.get('message')}")
            if curves:
                have.update(curves)
                self.settings.set_default_curves(fan, have)
                changed = True
        if changed:
            try:
                self.settings.save()
            except OSError as e:
                self.log.warning(f"Could not save the default fan curves: {e}")

    async def apply_all(self) -> bool:
        """Applies the stored settings. False when dropped because of a newer user change.

        While it runs, `applying` is set, and `applies` was bumped at its start, so an Auto TDP write can never land
        between its steps (the lock is released between steps)."""
        self.applies += 1
        self.applying += 1
        try:
            return await self._apply_all()
        finally:
            self.applying -= 1

    async def _apply_all(self) -> bool:
        gen = self.generation
        if self._busy():
            # An armoury_* call holds the single-threaded helper for minutes: queueing behind it would only time
            # out and then run later. Try again at the next poll.
            self._defer()
            return False
        self._deferring = False
        await self.read_default_curves()
        s = self.settings
        if not s.user_set:
            self.log.info("Reapply skipped: no Overdrive settings chosen yet")
            return True
        problems = []
        transport = False
        mode_ok = False
        try:
            r = await self._step(gen, "set_mode", mode_args(s))
            transport |= transport_failure(r)
            if not r.get("ok"):
                problems.append(r.get("message") or "The performance mode was not applied.")
            else:
                mode_ok = True
                self.fan_note = fan_note(s)
                for fan in FANS:   # CPU then GPU, as G-Helper's AutoFans
                    curve, _, warn = fan_plan(s, fan)
                    if warn:
                        problems.append(warn)
                    if curve is not None:
                        r = await self._step(gen, "set_fans", fan_args(curve, fan))
                        transport |= transport_failure(r)
                        if not r.get("ok"):
                            what = "fan curve" if fan == "cpu" else "GPU fan curve"
                            problems.append(r.get("message") or f"The {what} was not applied.")
            if s.fps != 0:   # 0 (off) is only sent when the owner picks it, so an Adrenalin limit is left alone
                r = await self._step(gen, "set_fps_limit", {"fps": s.fps})
                transport |= transport_failure(r)
                if not r.get("ok"):
                    problems.append(r.get("message") or "The FPS limit was not applied.")
        except Dropped:
            self.rerun = True
            self.log.info("Reapply dropped: a newer change was made meanwhile")
            return False
        # A helper restart or timeout (e.g. right after resume) is retried at the next poll, a few times at most;
        # a device refusal is not (it would only be refused again).
        if transport and self._retries < TRANSPORT_RETRIES:
            self._retries += 1
            self.rerun = True
            self.log.info(f"Reapply will be retried at the next poll ({self._retries}/{TRANSPORT_RETRIES})")
        else:
            self._retries = 0
            self.rerun = False
        self.warning = " ".join(problems)
        if problems:
            self.log.warning(f"Reapply incomplete: {self.warning}")
        else:
            self.log.info("Settings reapplied")
        # Only a mode write that landed put the mode's watts on the device: Auto TDP restarts from them then. After a
        # failed mode write the loop keeps the watts it knows (or a pause) until a reapply's mode write lands.
        if mode_ok and self.on_applied is not None:
            try:
                self.on_applied()
            except Exception:
                self.log.exception("Auto TDP reset after the reapply failed")
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
            reasons.append("redo")
        if not reasons:
            return False
        if self._busy():
            self._defer()
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
