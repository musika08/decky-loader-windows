import asyncio

from overdrive.reapply_core import ReapplierCore


class Client:
    busy = False

    def __init__(self):
        self.calls = []

    async def call(self, cmd, args=None, timeout=10.0):
        self.calls.append((cmd, args))
        return {"ok": True, "message": "", "data": None}


class TwoStep(ReapplierCore):
    """A subclass that writes twice through `_step`; the lock is free between the two writes."""

    def __init__(self, client, between):
        super().__init__(client, power_source=lambda: None)
        self.between = between

    async def _apply_steps(self, gen):
        await self._step(gen, "x", {})
        await self.between()
        await self._step(gen, "x", {})
        return [], False, True


def test_subclass_steps_run_and_dropped_on_user_change():
    async def go():
        client = Client()
        done = []

        async def user_change_between():
            async with r.user_change():
                done.append(1)

        r = TwoStep(client, user_change_between)
        applied = []
        r.on_applied = lambda: applied.append(1)
        assert await r.apply_all() is False
        assert r.rerun is True and done == [1] and applied == []
        assert len(client.calls) == 1                       # the second write never went out
        # nothing in the way the second time: both steps run and on_applied fires
        r.between = lambda: asyncio.sleep(0)
        assert await r.apply_all() is True
        assert r.rerun is False and applied == [1] and len(client.calls) == 3

    asyncio.run(go())
