import asyncio
import os
import sys

from overdrive.helper_client import RESTARTED, HelperClient

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_helper.py")


def spawner(*extra, counter=None):
    async def start():
        if counter is not None:
            counter.append(1)
        return await asyncio.create_subprocess_exec(
            sys.executable, FAKE, *extra,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    return start


class Sleeps:
    def __init__(self):
        self.delays = []

    async def __call__(self, delay):
        self.delays.append(delay)


async def until(cond, limit=15.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + limit
    while not cond():
        assert loop.time() < end, "condition not reached"
        await asyncio.sleep(0.02)


def test_call_roundtrip_and_ids():
    async def run():
        c = HelperClient(start_process=spawner())
        try:
            r1 = await c.call("hello")
            r2 = await c.call("set_mode", {"mode": "silent"})
            assert r1["ok"] is True and r1["data"]["version"] == "0.1.0"
            assert r2["ok"] is True and r2["data"] == {"mode": "silent"}
            assert r2["id"] == r1["id"] + 1
            # concurrent calls each get their own reply
            rs = await asyncio.gather(*(c.call("set_fps_limit", {"fps": f}) for f in (30, 60, 90)))
            assert [r["data"]["fps"] for r in rs] == [30, 60, 90]
        finally:
            await c.stop()
    asyncio.run(run())


def test_pending_request_fails_on_helper_exit():
    async def run():
        sleeps = Sleeps()
        starts = []
        c = HelperClient(start_process=spawner("--exit-on", "armoury_stop", counter=starts), sleep=sleeps)
        try:
            assert (await c.call("hello"))["ok"] is True
            r = await asyncio.wait_for(c.call("armoury_stop", timeout=300), 10)
            assert r["ok"] is False
            assert r["message"] == RESTARTED == "Overdrive helper restarted, try again"
            # restarted with the first backoff step and usable again
            await until(lambda: len(starts) == 2)
            assert (await c.call("hello"))["ok"] is True
            assert sleeps.delays == [1]
        finally:
            await c.stop()
    asyncio.run(run())


def test_other_pending_calls_fail_too_on_exit():
    async def run():
        c = HelperClient(start_process=spawner("--hang-on", "sensors", "--exit-on", "armoury_stop"), sleep=Sleeps())
        try:
            await c.call("hello")
            hanging = asyncio.ensure_future(c.call("sensors"))
            await asyncio.sleep(0.2)
            r = await c.call("armoury_stop")
            assert r["message"] == RESTARTED
            assert (await asyncio.wait_for(hanging, 5))["message"] == RESTARTED
        finally:
            await c.stop()
    asyncio.run(run())


def test_restarts_with_backoff_then_unavailable():
    async def run():
        sleeps = Sleeps()
        starts = []
        c = HelperClient(start_process=spawner("--exit-at-start", "7", counter=starts), sleep=sleeps)
        try:
            c.start()
            await until(lambda: c.unavailable_reason != "")
            assert len(starts) == 5
            assert sleeps.delays == [1, 2, 4, 4]
            assert "5 times in 10 minutes" in c.unavailable_reason
            assert "code 7" in c.unavailable_reason
            r = await c.call("hello")
            assert r["ok"] is False and r["message"] == c.unavailable_reason
            assert len(starts) == 5   # no more starts once unavailable
        finally:
            await c.stop()
    asyncio.run(run())


def test_old_starts_leave_the_window():
    async def run():
        now = [1000.0]
        sleeps = Sleeps()

        async def fake_sleep(d):
            await sleeps(d)
            now[0] += 200          # each wait also lets 200 s pass, so starts age out of the 10 min window

        starts = []
        c = HelperClient(start_process=spawner("--exit-at-start", "7", counter=starts),
                         sleep=fake_sleep, clock=lambda: now[0])
        try:
            c.start()
            await until(lambda: len(starts) >= 8)
            assert c.unavailable_reason == ""
        finally:
            await c.stop()
    asyncio.run(run())


def test_spawn_failure_counts_as_a_start():
    async def run():
        sleeps = Sleeps()

        async def broken():
            raise FileNotFoundError("asus-helper.exe not found")

        c = HelperClient(start_process=broken, sleep=sleeps)
        try:
            r = await c.call("hello", timeout=5)
            assert r["ok"] is False
            assert "asus-helper.exe not found" in c.unavailable_reason
            assert sleeps.delays == [1, 2, 4, 4]
        finally:
            await c.stop()
    asyncio.run(run())


def test_timeout_reply_and_helper_still_usable():
    async def run():
        c = HelperClient(start_process=spawner("--hang-on", "sensors"))
        try:
            r = await c.call("sensors", timeout=0.5)
            assert r["ok"] is False and "did not answer" in r["message"]
            assert (await c.call("hello"))["ok"] is True
        finally:
            await c.stop()
    asyncio.run(run())


def test_two_timeouts_in_a_row_kill_and_restart_helper():
    async def run():
        sleeps = Sleeps()
        starts = []
        c = HelperClient(start_process=spawner("--hang-on", "sensors", counter=starts), sleep=sleeps)
        try:
            await c.call("hello")
            first = c._proc
            assert "did not answer" in (await c.call("sensors", timeout=0.3))["message"]
            assert c._proc is first                       # one timeout is not enough
            assert (await c.call("hello"))["ok"] is True  # an answer resets the count
            assert "did not answer" in (await c.call("sensors", timeout=0.3))["message"]
            assert c._proc is first
            assert "did not answer" in (await c.call("sensors", timeout=0.3))["message"]
            await until(lambda: len(starts) == 2 and c._proc is not None and c._proc is not first)
            assert first.returncode is not None
            assert sleeps.delays == [1]                   # counted toward the restart budget
            assert "did not answer 2 requests in a row" in c.last_error
            assert (await c.call("hello"))["ok"] is True
        finally:
            await c.stop()
    asyncio.run(run())


def test_repeated_kills_end_unavailable():
    async def run():
        c = HelperClient(start_process=spawner("--hang-on", "sensors"), sleep=Sleeps())
        try:
            for _ in range(40):
                if c.unavailable_reason:
                    break
                await c.call("sensors", timeout=0.2)
            assert "5 times in 10 minutes" in c.unavailable_reason
            assert "did not answer" in c.unavailable_reason
        finally:
            await c.stop()
    asyncio.run(run())


def test_timeouts_during_armoury_call_never_kill():
    async def run():
        starts = []
        c = HelperClient(start_process=spawner("--slow-on", "armoury_stop", "--slow-secs", "1.5", counter=starts),
                         sleep=Sleeps())
        try:
            await c.call("hello")
            first = c._proc
            stop = asyncio.ensure_future(c.call("armoury_stop", timeout=300))
            await asyncio.sleep(0.1)
            assert c.busy is True
            for _ in range(3):
                assert "did not answer" in (await c.call("sensors", timeout=0.2))["message"]
            r = await stop
            assert r["ok"] is True                         # the slow service work finished normally
            assert c._proc is first and len(starts) == 1
            assert c.busy is False
            # a timed-out call that overlapped the armoury call does not count afterwards either
            assert (await c.call("hello"))["ok"] is True
        finally:
            await c.stop()
    asyncio.run(run())


def test_armoury_state_is_a_normal_read():
    async def run():
        c = HelperClient(start_process=spawner("--hang-on", "armoury_state,sensors"), sleep=Sleeps(),
                         default_timeout=0.2)
        try:
            await c.call("hello")
            for _ in range(60):
                if c.unavailable_reason:
                    break
                polls = asyncio.gather(c.call("armoury_state"), c.call("armoury_state"), c.call("sensors"))
                await asyncio.sleep(0.05)
                assert c.busy is False                     # a state read never counts as a service switch
                await polls
            assert "did not answer" in c.unavailable_reason
        finally:
            await c.stop()
    asyncio.run(run())


def test_grace_after_armoury_timeout():
    async def run():
        starts = []
        c = HelperClient(start_process=spawner("--slow-on", "armoury_stop", "--slow-secs", "1.5",
                                               "--hang-on", "sensors", counter=starts), sleep=Sleeps())
        try:
            await c.call("hello")
            first = c._proc
            r = await c.call("armoury_stop", timeout=0.3)
            assert "did not answer" in r["message"]
            assert c.busy is False
            # the helper is still stopping services: the next reads time out but must not kill it
            for _ in range(3):
                assert "did not answer" in (await c.call("sensors", timeout=0.2))["message"]
            assert c._proc is first and len(starts) == 1
            assert (await c.call("hello", timeout=5))["ok"] is True
        finally:
            await c.stop()
    asyncio.run(run())


def test_stop_waits_for_armoury_call():
    async def run():
        c = HelperClient(start_process=spawner("--slow-on", "armoury_stop", "--slow-secs", "1"))
        await c.call("hello")
        proc = c._proc
        stop_call = asyncio.ensure_future(c.call("armoury_stop", timeout=300))
        await asyncio.sleep(0.1)
        await c.stop()
        assert (await stop_call)["ok"] is True             # not cut short by unload
        assert proc.returncode is not None
    asyncio.run(run())


def test_write_counts_against_deadline():
    class Stuck:
        """stdin whose drain never returns, like a hung helper with a full pipe."""
        def write(self, b):
            pass

        async def drain(self):
            await asyncio.Event().wait()

        def close(self):
            pass

    class Proc:
        def __init__(self):
            self.stdin = Stuck()
            self.returncode = None
            self.stdout = asyncio.StreamReader()

        def kill(self):
            self.returncode = -9
            self.stdout.feed_eof()

        async def wait(self):
            return self.returncode

    async def run():
        async def start():
            return Proc()
        c = HelperClient(start_process=start, sleep=Sleeps())
        try:
            r = await asyncio.wait_for(c.call("hello", timeout=0.3), 5)
            assert "did not answer" in r["message"]
        finally:
            await c.stop()
    asyncio.run(run())


def test_late_reply_is_dropped():
    async def run():
        c = HelperClient(start_process=spawner("--slow-on", "sensors"))
        try:
            r = await c.call("sensors", timeout=0.3)
            assert r["ok"] is False and "did not answer" in r["message"]
            # the late sensors reply arrives first and must not be taken as hello's answer
            h = await c.call("hello", timeout=5)
            assert h["ok"] is True and h["data"]["version"] == "0.1.0"
            assert c._pending == {}
        finally:
            await c.stop()
    asyncio.run(run())


def test_stop_ends_process_and_refuses_calls():
    async def run():
        c = HelperClient(start_process=spawner())
        await c.call("hello")
        proc = c._proc
        await c.stop()
        assert proc.returncode is not None
        r = await c.call("hello")
        assert r["ok"] is False
    asyncio.run(run())


def test_default_start_uses_bin_exe_without_window(monkeypatch, tmp_path):
    seen = {}

    async def fake_exec(*argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        raise FileNotFoundError("nope")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    async def run():
        c = HelperClient(plugin_dir=str(tmp_path), runtime_dir=str(tmp_path / "rt"), sleep=Sleeps())
        await c.call("hello", timeout=5)
        await c.stop()
    asyncio.run(run())
    assert seen["argv"] == (os.path.join(str(tmp_path), "bin", "asus-helper.exe"),)
    if os.name == "nt":
        assert seen["kw"]["creationflags"] & 0x08000000
    assert seen["kw"]["cwd"] == str(tmp_path / "rt")


def test_frame_sample_timeouts_never_kill():
    async def run():
        starts = []
        c = HelperClient(start_process=spawner("--hang-on", "frame_sample,sensors", counter=starts), sleep=Sleeps())
        try:
            await c.call("hello")
            first = c._proc
            for _ in range(3):   # Auto TDP's short samples time out during a helper stall
                assert "did not answer" in (await c.call("frame_sample", {}, 0.2))["message"]
            assert c._proc is first and len(starts) == 1
            # they do not add to other calls' count either: one more timeout alone kills nothing
            assert "did not answer" in (await c.call("sensors", timeout=0.2))["message"]
            assert c._proc is first
            assert (await c.call("hello"))["ok"] is True
        finally:
            await c.stop()
    asyncio.run(run())


def test_foreground_timeouts_never_kill():
    async def run():
        starts = []
        c = HelperClient(start_process=spawner("--hang-on", "foreground,sensors", counter=starts), sleep=Sleeps())
        try:
            await c.call("hello")
            first = c._proc
            for _ in range(3):   # Controller Auto's short polls time out during a helper stall
                assert "did not answer" in (await c.call("foreground", {}, 0.2))["message"]
            assert c._proc is first and len(starts) == 1
            assert "did not answer" in (await c.call("sensors", timeout=0.2))["message"]
            assert c._proc is first
            assert (await c.call("hello"))["ok"] is True
        finally:
            await c.stop()
    asyncio.run(run())
