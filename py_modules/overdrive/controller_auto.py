"""Controller Auto mode: Gamepad in Steam and games, Desktop (mouse) for other apps. See
docs/specs/2026-10-24-overdrive-controls-design.md sections 3.3, 4 and 5.2.

- Gamepad when the foreground exe is steam.exe or steamwebhelper.exe (Steam, its overlay and its keyboard), when no
  window is in front (""), when a process draws frames (fps > 0), or when the frame rate is unknown (a failed reply,
  no AMD reading, a non-finite value). Desktop only for another app in front with a valid reading of exactly 0 fps.
  Steam Big Picture can only be steered with a gamepad, so every doubt decides Gamepad.
- Decided every 300 ms; switches after 3 equal decisions in a row (G-Helper's _autoCount == 3); only a change is
  written. The written state starts unknown, so the first streak of 3 always writes.
- One sampler, two consumers: Auto TDP's last frame sample is used when it is younger than 0.6 s, otherwise this loop
  asks frame_sample itself. A failed, unsupported or non-finite reading is unknown (None): Gamepad.
- Nothing is asked or written while an Armoury Crate switch runs. Writes take the reapplier's lock; they are not user
  changes (no generation bump, nothing saved). Never call stop() from inside reapplier.user_change(): it takes the
  same (non-reentrant) lock.
- The written mode is forgotten after a reapply (reapplier.applies changed) or an Armoury switch, so the next streak
  writes again; no write lands while a reapply runs (reapplier.applying).
- A write refused with "Controller not found" (no controller attached) is not tried again for NOT_FOUND_RETRY seconds,
  so a missing controller is not searched for (every HID device listed) at every tick.
- An error inside the loop is logged (once a minute at most), Gamepad is written and the loop goes on.
- stop() writes Gamepad (or the given fixed mode, when the owner's stored choice is Desktop).
"""
import asyncio
import logging
import time
from typing import Optional

from .auto_tdp import PERIOD
from .auto_tdp_loop import ERROR_LOG_EVERY, SAMPLE_TIMEOUT, _finite

STEAM_EXES = ("steam.exe", "steamwebhelper.exe")
STREAK = 3
FRESH = 0.6            # seconds: Auto TDP's sample is used when younger than this
NOT_FOUND_RETRY = 5.0  # seconds between writes while the helper reports no controller
NOT_FOUND = "controller not found"   # the helper's AsusHid.NotFound message, compared in lower case
GAMEPAD, DESKTOP = "gamepad", "desktop"


class ControllerAuto:
    def __init__(self, client, reapplier, autotdp, clock=time, sleep=asyncio.sleep,
                 logger: Optional[logging.Logger] = None):
        self.client = client
        self.reapplier = reapplier
        self.autotdp = autotdp
        self.clock = clock
        self.sleep = sleep
        self.log = logger or logging.getLogger("overdrive")
        self._on = False
        self._task: Optional[asyncio.Task] = None
        self._candidate: Optional[str] = None
        self._count = 0
        self._written: Optional[str] = None   # mode this loop last wrote; None = unknown
        self._error_logged_at: Optional[float] = None
        self._write_failed_logged_at: Optional[float] = None
        self._applies_seen = 0     # reapplier.applies at the last tick: a reapply may have rewritten the controller
        self._busy_seen = False    # an Armoury switch was seen: the controller state is no longer known
        self._not_found_at: Optional[float] = None   # monotonic time of the last "Controller not found" refusal

    # ---------- control ----------

    @property
    def running(self) -> bool:
        """True from start() until stop()."""
        return self._on

    def start(self) -> None:
        if self._on:
            return
        self._on = True
        self._candidate, self._count, self._written = None, 0, None
        self._applies_seen, self._busy_seen = self.reapplier.applies, False
        self._not_found_at = None
        self.log.info("Controller Auto started")
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    async def stop(self, mode: str = GAMEPAD) -> None:
        """Ends the loop and writes Gamepad (even while an Armoury switch runs: Big Picture must never be left with a
        mouse-only controller). `mode` DESKTOP is only passed when the owner's stored choice is Desktop."""
        mode = DESKTOP if mode == DESKTOP else GAMEPAD
        was_on = self._on
        self._on = False
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if was_on:
            self.log.info("Controller Auto stopped")
        async with self.reapplier.lock:
            r = await self.client.call("set_controller_mode", {"mode": mode})
        if not r.get("ok"):
            self.log.warning(f"Controller Auto could not put the controller back to {mode}: {r.get('message')}")
        self._written = mode if r.get("ok") else None

    # ---------- deciding ----------

    @staticmethod
    def decide(exe: str, fps: Optional[float]) -> str:
        exe = (exe or "").strip().lower()
        if exe == "" or exe in STEAM_EXES:
            return GAMEPAD
        if _finite(fps) and fps == 0:
            return DESKTOP   # another app in front and a valid reading of no frames
        return GAMEPAD       # frames drawn, or the frame rate is unknown: every doubt decides Gamepad

    async def _fps(self) -> Optional[float]:
        """The frame rate, or None when it is unknown (failed reply, unsupported, not finite)."""
        sample = getattr(self.autotdp, "last_sample", None) if self.autotdp is not None else None
        if sample is not None and self.clock.monotonic() - sample[1] < FRESH:
            return float(sample[0]) if _finite(sample[0]) else None
        r = await self.client.call("frame_sample", {}, SAMPLE_TIMEOUT)
        data = r.get("data") if r.get("ok") and isinstance(r.get("data"), dict) else None
        if data is None or data.get("supported") is False:
            return None
        fps = data.get("fps")
        return float(fps) if _finite(fps) else None

    async def run_once(self) -> None:
        """One decision and, after 3 equal decisions in a row that differ from the written mode, one write."""
        if not self._on:
            return
        rp = self.reapplier
        if rp._busy():
            self._busy_seen = True
            return
        if rp.applies != self._applies_seen or self._busy_seen:
            # A reapply (resume, AC change, helper restart) or an Armoury switch since the last tick: the controller
            # may have forgotten or been given another mode. Unknown again: the next 3-in-a-row decision is written.
            self._applies_seen, self._busy_seen = rp.applies, False
            self._written, self._candidate, self._count = None, None, 0
            self._not_found_at = None   # the reapply may have found the controller: try again at once
        r = await self.client.call("foreground", {}, SAMPLE_TIMEOUT)
        data = r.get("data") if r.get("ok") and isinstance(r.get("data"), dict) else {}
        exe = data.get("exe") if isinstance(data.get("exe"), str) else ""   # a failed reply: no window, Gamepad
        exe = exe.strip().lower()
        # Steam in front decides Gamepad whatever the frame rate: no frame sample needed.
        fps = 0.0 if (exe == "" or exe in STEAM_EXES) else await self._fps()
        if not self._on:
            return
        d = self.decide(exe, fps)
        if d == self._candidate:
            self._count += 1
        else:
            self._candidate, self._count = d, 1
        if self._count >= STREAK and d != self._written:
            await self._write(d)

    def _backing_off(self) -> bool:
        return (self._not_found_at is not None
                and self.clock.monotonic() - self._not_found_at < NOT_FOUND_RETRY)

    async def _write(self, mode: str) -> None:
        rp = self.reapplier
        if self._backing_off():
            return   # no controller a moment ago: tried again once NOT_FOUND_RETRY has passed
        async with rp.lock:
            if not self._on or rp.applying or rp._busy():
                # Stopped meanwhile, a reapply in progress (its steps release the lock in between) or an Armoury
                # switch: not written, decided again at the next tick.
                if rp._busy():
                    self._busy_seen = True
                return
            r = await self.client.call("set_controller_mode", {"mode": mode})
        if r.get("ok"):
            self._written = mode
            self._write_failed_logged_at = self._not_found_at = None
            return
        self._written = None
        if NOT_FOUND in str(r.get("message") or "").lower():
            self._not_found_at = self.clock.monotonic()
        if mode != GAMEPAD:
            self._count = 0   # Desktop is tried again after another 3 equal decisions (about 1 s), not every tick
        # Gamepad keeps the streak: tried again at the next tick (Big Picture must get its gamepad back).
        now = self.clock.monotonic()
        if self._write_failed_logged_at is None or now - self._write_failed_logged_at >= ERROR_LOG_EVERY:
            self._write_failed_logged_at = now
            self.log.warning(f"Controller Auto could not set {mode}: {r.get('message')}")

    # ---------- loop ----------

    async def _run(self) -> None:
        while self._on and self._task is asyncio.current_task():
            await self.sleep(PERIOD)
            if not (self._on and self._task is asyncio.current_task()):
                break
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                now = self.clock.monotonic()
                if self._error_logged_at is None or now - self._error_logged_at >= ERROR_LOG_EVERY:
                    self._error_logged_at = now
                    self.log.exception("Controller Auto sample failed")
                self._candidate, self._count = None, 0
                try:
                    if self._written != GAMEPAD:
                        await self._write(GAMEPAD)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass   # logged above (rate-limited); tried again at the next error or decision
            else:
                self._error_logged_at = None
