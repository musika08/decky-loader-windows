"""Runs bin\\asus-helper.exe as one long-lived child process and talks JSON lines to it.

Request {"id":n,"cmd":"name","args":{...}}, reply {"id":n,"ok":bool,"message":"","data":...}. call() always returns
a reply dict; failures on this side (helper gone, no answer, unavailable) come back as ok=false replies too.

Restarts: when the helper exits it is started again after 1, 2, 4, 4 ... s; at most 5 starts per 10 minutes, after
that the client stays unavailable (unavailable_reason) until the plugin is reloaded. Calls in flight when the helper
exits resolve at once with RESTARTED; a reply that arrives after its call gave up is dropped, never applied.

Hung helper (ruling P11): two calls in a row that time out kill the helper, which then restarts within the same
budget. Only armoury_stop and armoury_restore are long service calls ("busy"): calls that overlapped one never count,
this client never kills the helper while one is in flight (Decky still can, see stop()), and after one times out the helper gets a grace period
(ARMOURY_GRACE) because it may still be finishing the service work. armoury_state is an ordinary read.
"""
import asyncio
import json
import logging
import os
import time
from collections import deque
from typing import Awaitable, Callable, Optional

RESTARTED = "Overdrive helper restarted, try again"
STOPPING = "Overdrive is stopping."
CREATE_NO_WINDOW = 0x08000000
BACKOFF = (1, 2, 4)
MAX_STARTS = 5
WINDOW = 600.0
READ_LIMIT = 1 << 20
TIMEOUTS_BEFORE_KILL = 2
# Auto TDP's frame sample and Controller Auto's foreground have a short timeout (2 s, every 300 ms): their timeouts do
# not count toward a kill, so a short helper stall does not kill the helper. Auto TDP's own failures pause it, whose
# mode write then counts as usual; a failed foreground decides Gamepad.
NOT_COUNTED = ("frame_sample", "foreground")
LONG_ARMOURY = ("armoury_stop", "armoury_restore")
ARMOURY_GRACE = 60.0   # one more listed service's worst case (30 s stop, doubled by a rollback)

StartProcess = Callable[[], Awaitable["asyncio.subprocess.Process"]]


def failure(message: str, data=None) -> dict:
    return {"id": None, "ok": False, "message": message, "data": data}


def is_armoury(cmd: str) -> bool:
    """A long service call (stop/restore). armoury_state is a normal read and is not one."""
    return cmd in LONG_ARMOURY


class HelperClient:
    def __init__(self, start_process: Optional[StartProcess] = None, *, plugin_dir: Optional[str] = None,
                 runtime_dir: Optional[str] = None, sleep=asyncio.sleep, clock=time.monotonic,
                 logger: Optional[logging.Logger] = None, default_timeout: float = 10.0):
        self._start_process = start_process or self._spawn
        self._plugin_dir = plugin_dir or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._runtime_dir = runtime_dir
        self._sleep = sleep
        self._clock = clock
        self._log = logger or logging.getLogger("overdrive")
        self._proc = None
        self._reader: Optional[asyncio.Task] = None
        self._starter: Optional[asyncio.Task] = None
        self._pending: "dict[int, asyncio.Future]" = {}
        self._next_id = 1
        self._starts: deque = deque()
        self._stopping = False
        self._armoury_inflight = 0
        self._armoury_epoch = 0          # bumped whenever an armoury_* call starts
        self._armoury_until = 0.0        # loop time by which every armoury_* call in flight has given up
        self._timeouts = 0               # consecutive non-armoury timeouts
        self._grace_until = 0.0          # after a long armoury call timed out, no kills before this loop time
        self.armoury_grace = ARMOURY_GRACE
        self._kill_reason = ""
        self.last_error = ""
        self.unavailable_reason = ""
        self.default_timeout = default_timeout

    @property
    def exe_path(self) -> str:
        return os.path.join(self._plugin_dir, "bin", "asus-helper.exe")

    @property
    def busy(self) -> bool:
        """True while armoury_stop or armoury_restore is in flight."""
        return self._armoury_inflight > 0

    async def _spawn(self):
        cwd = self._runtime_dir or self._plugin_dir
        try:
            os.makedirs(cwd, exist_ok=True)
        except OSError:
            cwd = self._plugin_dir
        kw = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
        return await asyncio.create_subprocess_exec(
            self.exe_path, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=cwd, limit=READ_LIMIT, **kw)

    # ---------- lifecycle ----------

    def start(self) -> None:
        """Starts the helper in the background (no-op when running, starting, stopped or unavailable)."""
        if self._stopping or self.unavailable_reason or self._proc is not None:
            return
        if self._starter is None or self._starter.done():
            self._starter = asyncio.ensure_future(self._start_loop(restart=False))

    async def _start_loop(self, restart: bool) -> None:
        while not self._stopping:
            now = self._clock()
            while self._starts and now - self._starts[0] >= WINDOW:
                self._starts.popleft()
            if len(self._starts) >= MAX_STARTS:
                self.unavailable_reason = (f"Overdrive helper stopped {MAX_STARTS} times in 10 minutes. "
                                           f"Last error: {self.last_error or 'unknown'}")
                self._log.error(self.unavailable_reason)
                return
            if restart or self._starts:
                await self._sleep(BACKOFF[min(max(len(self._starts) - 1, 0), len(BACKOFF) - 1)])
                if self._stopping:
                    return
            restart = True
            self._starts.append(self._clock())
            try:
                proc = await self._start_process()
            except Exception as e:  # missing exe, access denied, ...
                self.last_error = f"could not start {os.path.basename(self.exe_path)}: {e}"
                self._log.warning(self.last_error)
                continue
            if self._stopping:
                await self._end(proc)
                return
            self._proc = proc
            self._timeouts = 0
            self._reader = asyncio.ensure_future(self._read_loop(proc))
            self._log.info("Overdrive helper started")
            return

    async def stop(self) -> None:
        # Try not to cut an armoury_* call short (the helper would die halfway through changing services): wait for
        # it, bounded by that call's own timeout. New calls are refused meanwhile. On a Decky unload this wait is
        # really bounded by Decky: it kills the plugin process 5 s after the stop request
        # (plugin/plugin.py kill_if_still_running), and the helper shares the plugin's KILL_ON_JOB_CLOSE job
        # (localplatform/winprocess.py _create_job), so both die. main.py repairs that at the next load.
        self._stopping = True
        loop = asyncio.get_running_loop()
        while self._armoury_inflight > 0 and loop.time() < self._armoury_until:
            await asyncio.sleep(0.05)
        if self._starter is not None and not self._starter.done():
            self._starter.cancel()
            try:
                await self._starter
            except (asyncio.CancelledError, Exception):
                pass
        proc, self._proc = self._proc, None
        self._fail_pending(STOPPING)
        if proc is not None:
            await self._end(proc)
        if self._reader is not None and not self._reader.done():
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass

    async def _end(self, proc) -> None:
        """Closes stdin (the helper exits at EOF); kills it if it is still there after 3 s."""
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), 3.0)
        except (asyncio.TimeoutError, Exception):
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), 3.0)
            except Exception:
                pass

    def _kill(self, reason: str) -> None:
        """Kills a hung helper; the read loop then fails pending calls and restarts it within the budget."""
        proc = self._proc
        if proc is None or self._armoury_inflight > 0:
            return
        self._kill_reason = reason
        self._log.warning(f"Overdrive helper killed: {reason}")
        try:
            proc.kill()
        except Exception:
            pass

    # ---------- reading ----------

    async def _read_loop(self, proc) -> None:
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    self._log.warning("Overdrive helper wrote a line that is not JSON")
                    continue
                if not isinstance(msg, dict):
                    continue
                rid = msg.get("id")
                fut = self._pending.pop(rid, None) if isinstance(rid, int) else None
                if fut is None:
                    self._log.info(f"Dropped a late or unknown helper reply (id {rid!r})")
                elif not fut.done():
                    self._timeouts = 0
                    fut.set_result(msg)
        except (asyncio.CancelledError, ConnectionError, OSError, ValueError):
            pass
        if proc is not self._proc:
            return
        code = None
        try:
            code = await asyncio.wait_for(proc.wait(), 5.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self._proc = None
        if self._kill_reason:
            self.last_error, self._kill_reason = self._kill_reason, ""
        elif code is None:
            self.last_error = "the helper stopped responding and was killed"
        else:
            self.last_error = f"the helper exited with code {code}"
        self._log.warning(f"Overdrive: {self.last_error}")
        self._fail_pending(RESTARTED)
        if not self._stopping:
            self._starter = asyncio.ensure_future(self._start_loop(restart=True))

    def _fail_pending(self, message: str) -> None:
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_result(failure(message))

    # ---------- calls ----------

    async def call(self, cmd: str, args: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
        """Sends one request; timeout None means default_timeout (10 s)."""
        if timeout is None:
            timeout = self.default_timeout
        if self._stopping:
            return failure(STOPPING)
        if self.unavailable_reason:
            return failure(self.unavailable_reason)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        armoury = is_armoury(cmd)
        if armoury:
            self._armoury_inflight += 1
            self._armoury_epoch += 1
            self._armoury_until = max(self._armoury_until, deadline)
        epoch = self._armoury_epoch
        try:
            return await self._call(loop, deadline, cmd, args, timeout, armoury, epoch)
        finally:
            if armoury:
                self._armoury_inflight -= 1

    async def _call(self, loop, deadline, cmd, args, timeout, armoury, epoch) -> dict:
        if self._proc is None:
            self.start()
            starter = self._starter
            if starter is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(starter), max(deadline - loop.time(), 0.01))
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
        proc = self._proc
        if proc is None:
            return failure(self.unavailable_reason or
                           f"Overdrive helper is not running ({self.last_error or 'starting'}).")

        rid = self._next_id
        self._next_id += 1
        fut = loop.create_future()
        self._pending[rid] = fut
        req = {"id": rid, "cmd": cmd}
        if args is not None:
            req["args"] = args
        line = (json.dumps(req) + "\n").encode("utf-8")

        async def exchange():
            # The write and its drain count against the deadline too: a hung helper stops reading its stdin.
            proc.stdin.write(line)
            await proc.stdin.drain()
            return await fut

        try:
            return await asyncio.wait_for(exchange(), max(deadline - loop.time(), 0.01))
        except asyncio.TimeoutError:
            if armoury:
                # The helper may still be finishing the service work: start counting afresh, after a grace period.
                self._timeouts = 0
                self._grace_until = max(self._grace_until, loop.time() + self.armoury_grace)
            overlapped = (self._armoury_inflight > 0 or epoch != self._armoury_epoch
                          or loop.time() < self._grace_until)
            if not armoury and not overlapped and proc is self._proc and cmd not in NOT_COUNTED:
                self._timeouts += 1
                if self._timeouts >= TIMEOUTS_BEFORE_KILL:
                    self._timeouts = 0
                    self._kill(f"it did not answer {TIMEOUTS_BEFORE_KILL} requests in a row")
            return failure(f"Overdrive helper did not answer in {timeout:g} s.")
        except (ConnectionError, OSError, AttributeError, RuntimeError):
            return failure(RESTARTED)
        finally:
            self._pending.pop(rid, None)
