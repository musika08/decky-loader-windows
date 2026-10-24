"""The reapply loop shared by Overdrive and Traction: puts the stored settings back on the device on start, on resume
from sleep and when AC power changes. Standard library only (and nothing from the rest of the package).

Every poll (5 s) compares the wall clock with the monotonic clock (a gap over 30 s means the PC slept; a poll that
took far longer than 5 s on the monotonic clock means the loop itself was suspended) and reads the AC line status.

User changes and reapply steps share one lock. A user change bumps `generation` before it waits for the lock, so a
reapply step that runs after the bump sees a newer generation and drops the rest of that reapply; the user's write
always lands last. A dropped reapply is redone (with the then-current settings) at the next poll. The same happens
while an armoury_* call is in flight (client.busy). The resume baseline is taken again after each reapply, so a slow
reapply is never mistaken for sleep.

With a shared Armoury Crate record (`record`, an armoury_record.ArmouryRecord), every poll also looks at it (a stat,
read again only when it changed): when it flips from not replaced to replaced (this plugin or the other one stopped
Armoury Crate), the settings are reapplied ("Armoury switched").

A plugin subclasses ReapplierCore and supplies `_apply_steps` (the device writes, each through `_step`) and, if it
needs to, `_should_apply`, `_before_apply`, `_is_transport_failure` and extra reasons in `_triggers`.
"""
import asyncio
import contextlib
import logging
import time
from typing import Callable, Optional

POLL = 5.0
RESUME_GAP = 30.0
TRANSPORT_RETRIES = 3   # reapplies that hit a helper restart/timeout are retried at the next poll, at most this often


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


class Dropped(Exception):
    pass


class _StepClient:
    """A client whose calls go through the reapplier's `_step`, so helpers such as controls.apply_controller obey the
    same lock, generation and Armoury-busy checks (and raise Dropped) as the other reapply steps."""

    def __init__(self, reapplier: "ReapplierCore", gen: int):
        self._r, self._gen = reapplier, gen
        self.transport = False   # any reply failed on the way (helper restart, timeout)

    async def call(self, cmd: str, args: Optional[dict] = None, timeout: float = 10.0) -> dict:
        r = await self._r._step(self._gen, cmd, args or {})
        self.transport |= self._r._is_transport_failure(r)
        return r


class ReapplierCore:
    SKIP_MESSAGE = "Reapply skipped: no settings chosen yet"
    DEFER_MESSAGE = "Reapply deferred while Armoury Crate services are being switched"
    LOGGER_NAME = "reapply"   # the logger used when none is given; a plugin overrides it

    def __init__(self, client, clock=time, power_source: Optional[Callable] = None, logger: Optional[logging.Logger] = None,
                 poll: float = POLL, record=None):
        self.client = client
        self.record = record       # the shared Armoury Crate record (armoury_record.ArmouryRecord), or None
        self._replaced: Optional[bool] = None   # record.replaced at the last poll
        self.clock = clock
        self.power_source = power_source or ac_online_win32
        self.poll = poll
        self.log = logger or logging.getLogger(self.LOGGER_NAME)
        self.generation = 0
        self.lock = asyncio.Lock()
        self.rerun = False
        self.warning = ""
        self._task: Optional[asyncio.Task] = None
        self._wall = self._mono = None
        self._ac: Optional[bool] = None
        self.busy_check: Optional[Callable[[], bool]] = None   # main.py: an Armoury operation is running
        self._deferring = False
        self._retries = 0          # consecutive reapplies retried after a transport failure
        self.on_applied: Optional[Callable[[], None]] = None   # main.py: after a reapply's mode write landed
        self.applies = 0           # bumped when a reapply starts: Auto TDP drops a write sampled before it
        self.applying = 0          # reapplies in progress: Auto TDP writes nothing meanwhile

    # ---------- for the subclass ----------

    async def _apply_steps(self, gen: int) -> "tuple[list[str], bool, bool]":
        """Write the stored settings to the device, every call through `self._step(gen, cmd, args)` (it raises
        Dropped when a newer user change or an Armoury operation got in). Returns (problems, transport, mode_ok):
        the messages of steps that failed, whether any reply failed on the way (a helper restart or timeout), and
        whether the mode write landed (`on_applied` is called then)."""
        raise NotImplementedError

    def _should_apply(self) -> bool:
        """False while the owner has chosen nothing yet: the reapply then only reads, it never writes."""
        return True

    async def _before_apply(self) -> None:
        """Reads before the writes (not subject to the generation check). Default: nothing."""

    def _is_transport_failure(self, reply: dict) -> bool:
        """A reply that failed on the way (helper restarted, did not answer), not a device refusal."""
        return False

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
            self.log.info(self.DEFER_MESSAGE)

    async def _step(self, gen: int, cmd: str, args: dict) -> dict:
        async with self.lock:
            if gen != self.generation or self._busy():
                raise Dropped()
            return await self.client.call(cmd, args)

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
        await self._before_apply()
        if not self._should_apply():
            self.log.info(self.SKIP_MESSAGE)
            return True
        try:
            problems, transport, mode_ok = await self._apply_steps(gen)
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
        self._replaced = self._record_replaced()

    def _record_replaced(self) -> Optional[bool]:
        """record.replaced now (read again only when the file changed), None without a record or when unreadable."""
        if self.record is None:
            return None
        try:
            self.record.refresh()
        except Exception as e:
            self.log.warning(f"Could not read the Armoury Crate record: {e}")
            return self._replaced
        return bool(self.record.replaced)

    def _triggers(self) -> "list[str]":
        """The reasons to reapply now (resume, AC change). A subclass extends it: `reasons = super()._triggers()`,
        then appends its own."""
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
        replaced = self._record_replaced()
        if replaced and self._replaced is False:
            reasons.append("Armoury switched")
            self.rerun = True   # kept when the reapply is deferred or dropped
        if replaced is not None:
            self._replaced = replaced
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
