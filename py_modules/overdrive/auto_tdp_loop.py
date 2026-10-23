"""Auto TDP loop: every 300 ms reads the frame rate (frame_sample), steps the AutoTdp controller and writes its
decision as three equal power limits (set_power). See docs/specs/2026-10-24-overdrive-auto-tdp-design.md sections 3,
4.3 and 7.

- Loop writes take the reapplier's lock and are dropped when a user change happened since the sample (the user change
  resets the controller through the reapply). They are not user changes: no generation bump, nothing saved.
- Samples are skipped while an Armoury Crate switch runs.
- Three failed samples in a row write the mode again (its normal watts) and pause sampling until reset().
- A set_power refusal stops the loop and writes the mode again; a driver without frame metrics stops it without writes.
- Writing the mode again also writes the fan curves again (a mode write puts the firmware curves back).
- The watts on the device count as known only after a confirmed mode write or set_power; until then (and whenever
  they differ from the controller's) each sample writes the controller's watts.
- Start, stop and pause are logged; single watt steps are not. A sample that keeps raising is logged once a minute.
"""
import asyncio
import logging
import math
import time
from typing import Optional

from .auto_tdp import PERIOD, AutoTdp
from .reapply import mode_write, rewrite_fans, transport_failure
from .settings import MODE_POWER, Settings

FAILURES_TO_PAUSE = 3
SAMPLE_TIMEOUT = 2.0
NO_READING = "no frame-rate reading"
ERROR_LOG_EVERY = 60.0   # seconds between stack traces of a sample that keeps raising


def ceiling(settings: Settings) -> int:
    """The highest watts Auto TDP may use: the mode's watts, or the Sustained slider in Manual."""
    if settings.mode == "manual":
        return int(settings.spl)
    return MODE_POWER[settings.mode]


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class AutoTdpLoop:
    def __init__(self, client, settings: Settings, reapplier, clock=time, sleep=asyncio.sleep,
                 logger: Optional[logging.Logger] = None):
        self.client = client
        self.settings = settings
        self.reapplier = reapplier
        self.clock = clock
        self.sleep = sleep
        self.log = logger or logging.getLogger("overdrive")
        self.ctl: Optional[AutoTdp] = None
        self._on = False
        self._task: Optional[asyncio.Task] = None
        self._epoch = 0          # bumped by start/reset/stop: a sample taken before is not used after
        self._fails = 0
        self._fps: Optional[float] = None
        self._paused = ""
        self._supported = True
        self._written: Optional[int] = None   # watts confirmed on the device (mode write or set_power), else None
        self._error_logged_at: Optional[float] = None
        self.last_sample: Optional[tuple] = None   # (fps, monotonic time) of the last good frame_sample (ControllerAuto)

    # ---------- control ----------

    def start(self) -> None:
        """Starts at the ceiling. Nothing is written here; the watts on the device are unknown (Armoury Crate's or an
        earlier session's), so the first sample writes the controller's watts unless a mode write lands first."""
        if self._on:
            return
        self._on = True
        self._epoch += 1
        self._fails = 0
        self._fps = None
        self._paused = ""
        self.ctl = AutoTdp(ceiling(self.settings), self.settings.fps, self.clock.monotonic())
        self._written = None
        self.log.info(f"Auto TDP started (up to {self.ctl.ceiling} W)")
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    async def stop(self, restore: bool = True) -> None:
        was_on = self._on
        task, self._task = self._task, None
        self._halt()
        self._paused = ""
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if was_on:
            self.log.info("Auto TDP stopped")
        if restore:
            await self._restore_mode()

    def reset(self, mode_written: bool = False) -> None:
        """New ceiling and FPS limit from the settings; clears a pause. Idle when stopped. `mode_written`: a mode
        write just landed, so the device is at the ceiling; otherwise (an FPS limit change) the device keeps the
        watts last confirmed, and the next sample writes the controller's watts when they differ."""
        if not self._on or self.ctl is None:
            return
        was_paused = bool(self._paused)
        self._epoch += 1
        self._fails = 0
        self._paused = ""
        self.ctl.reset(ceiling(self.settings), self.settings.fps, self.clock.monotonic())
        if mode_written:
            self._written = self.ctl.watts
        if was_paused:
            self.log.info(f"Auto TDP resumed (up to {self.ctl.ceiling} W)")

    def after_mode_write(self) -> None:
        """The reapplier's on_applied: a reapply's mode write landed."""
        self.reset(mode_written=True)

    def status(self) -> dict:
        on = self._on and self.ctl is not None
        best = None
        if on and self.ctl.fps_limit == 0 and self.ctl.phase == "trim":
            best = self.ctl.target
        return {"on": on, "watts": self.ctl.watts if on else None, "fps": self._fps, "best": best,
                "paused": self._paused, "supported": self._supported}

    # ---------- sampling ----------

    async def _run(self) -> None:
        # Only the current task samples: a task halted by a refusal (still finishing its restore) ends here even when
        # the switch was turned off and on meanwhile and a new task started.
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
                    self.log.exception("Auto TDP sample failed")
            else:
                self._error_logged_at = None

    async def run_once(self) -> None:
        """One sample and, when the controller's watts differ from those confirmed on the device, one write."""
        if not self._on or self._paused or self.ctl is None:
            return
        if self.reapplier._busy():
            return
        epoch, gen = self._epoch, (self.reapplier.generation, self.reapplier.applies)
        r = await self.client.call("frame_sample", {}, SAMPLE_TIMEOUT)
        if epoch != self._epoch or not self._on:
            return
        data = r.get("data") if isinstance(r.get("data"), dict) else {}
        if data.get("supported") is False:
            self._supported = False
            self.log.info("Auto TDP stopped: the AMD driver offers no frame-rate reading")
            self._halt()
            return
        fps = data.get("fps")
        if transport_failure(r) or not r.get("ok") or not _finite(fps):
            errors = data.get("errors") or []
            reason = r.get("message") or (str(errors[0]) if errors else "") or NO_READING
            await self._failed(reason)
            return
        self._fails = 0
        self._fps = float(fps)
        self.last_sample = (float(fps), self.clock.monotonic())
        gpu = data.get("gpuPowerW")
        gpu = int(gpu) if _finite(gpu) else None
        self.ctl.step(float(fps), gpu, self.clock.monotonic())
        if self.ctl.watts != self._written:   # a new decision, or watts not (or no longer) known on the device
            await self._write(self.ctl.watts, epoch, gen)

    def _rollback(self, epoch: int) -> None:
        """A write that did not land: the controller goes back to the watts the device really has (when known). Any
        difference from those is written again at the next sample."""
        if epoch == self._epoch and self.ctl is not None and self._written is not None:
            self.ctl.watts = self._written

    async def _write(self, watts: int, epoch: int, gen: tuple) -> None:
        rp = self.reapplier
        async with rp.lock:
            if (gen != (rp.generation, rp.applies) or rp.applying or epoch != self._epoch or not self._on
                    or rp._busy()):
                # A user change or reapply (both end in a reset to the ceiling), a stop, or an Armoury switch.
                self._rollback(epoch)
                return
            r = await self.client.call("set_power", {"spl": watts, "sppt": watts, "fppt": watts})
        if r.get("ok"):
            if epoch == self._epoch:
                self._written = watts
            return
        self._rollback(epoch)   # the controller goes on from the last confirmed watts
        if transport_failure(r):
            if epoch == self._epoch:
                self._written = None   # a write that timed out may still land: written again at the next sample
            await self._failed(r.get("message") or NO_READING)
            return
        msg = r.get("message") or "The power limit was not applied."
        self.log.info(f"Auto TDP stopped: {msg}")
        self._halt()
        self._paused = msg
        await self._restore_mode()

    async def _failed(self, reason: str) -> None:
        self._fails += 1
        if self._fails < FAILURES_TO_PAUSE:
            return
        self._paused = f"Auto TDP paused: {reason}"
        self.log.info(self._paused)
        self.ctl.reset(ceiling(self.settings), self.settings.fps, self.clock.monotonic())
        epoch = self._epoch
        self._written = None   # known again only once the restore's mode write is confirmed
        if await self._restore_mode() and epoch == self._epoch:
            self._written = self.ctl.watts

    # ---------- helpers ----------

    def _halt(self) -> None:
        """Stops sampling; the background task ends at its next wake-up (or is cancelled by stop())."""
        self._on = False
        self._epoch += 1
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _restore_mode(self) -> bool:
        """Writes the mode again (its normal watts, or Manual's limits), then the fan curves it reset, under the same
        lock. Not a user change: no generation bump. True when the mode write landed."""
        s = self.settings
        async with self.reapplier.lock:
            r = await self.client.call("set_mode", mode_write(s.mode, (s.spl, s.sppt, s.fppt)))
            if not r.get("ok"):
                # The watts may still be trimmed: the next reapply poll writes the mode again (and resumes a pause).
                self.reapplier.rerun = True
                self.log.warning(f"Auto TDP could not put the mode's power back: {r.get('message')}")
                return False
            msg, failed = await rewrite_fans(self.client, s)
        if failed:
            # The firmware fan curve runs instead of the chosen one: the next reapply poll writes them again.
            self.reapplier.rerun = True
            self.log.warning(f"Auto TDP put the mode's power back, but not the fan curves: {msg}")
        return True
