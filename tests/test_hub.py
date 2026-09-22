import asyncio

from starlette.websockets import WebSocketDisconnect

from aisws.config import TrackerConfig
from aisws.hub import Hub
from aisws.messages import PositionReport
from aisws.tracker import Tracker

MMSI = 244000001


def pos(mmsi=MMSI):
    return PositionReport(1, mmsi, 51.40, 3.60, 10.0, 90.0, 90, 0, "A")


class FakeSocket:
    """Neppe WebSocket: legt verzonden berichten vast en kan blijven hangen,
    zoals een telefoon die zonder afmelden van de wifi verdwijnt."""

    def __init__(self, stuck_after=None, gate=None):
        self.sent = []
        self.stuck_after = stuck_after
        self.gate = gate
        self._gone = asyncio.Event()

    async def accept(self):
        pass

    @property
    def stuck(self):
        return self.stuck_after is not None and len(self.sent) >= self.stuck_after

    async def send_json(self, message):
        if self.stuck:
            await asyncio.Event().wait()
        if self.gate is not None and not self.sent:
            await self.gate.wait()
        self.sent.append(message)

    async def receive_text(self):
        await self._gone.wait()
        raise WebSocketDisconnect()

    async def close(self, code=1000):
        if self.stuck:
            await asyncio.Event().wait()

    def disconnect(self):
        self._gone.set()


async def finish(sockets, tasks):
    for ws in sockets:
        ws.disconnect()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def test_stuck_client_does_not_hold_up_the_others():
    async def scenario():
        tracker = Tracker(TrackerConfig(), 51.44, 3.58)
        hub = Hub(tracker, send_timeout_s=0.05)
        stuck, healthy = FakeSocket(stuck_after=1), FakeSocket()  # snapshot komt nog aan
        tasks = [asyncio.create_task(hub.serve(ws)) for ws in (stuck, healthy)]
        await asyncio.sleep(0.01)
        tracker.handle(pos(), now=100)
        await asyncio.wait_for(hub.tick(now=100), timeout=0.5)
        await asyncio.sleep(0.3)
        assert [m["type"] for m in healthy.sent] == ["snapshot", "update"]
        assert len(hub.clients) == 1
        await finish((stuck, healthy), tasks)

    asyncio.run(scenario())


def test_changes_during_the_snapshot_are_not_lost():
    async def scenario():
        tracker = Tracker(TrackerConfig(), 51.44, 3.58)
        hub = Hub(tracker, send_timeout_s=1)
        gate = asyncio.Event()
        ws = FakeSocket(gate=gate)
        task = asyncio.create_task(hub.serve(ws))
        await asyncio.sleep(0.01)  # de snapshot wacht nog op de trage verbinding
        tracker.handle(pos(), now=100)
        await hub.tick(now=100)
        gate.set()
        await asyncio.sleep(0.05)
        assert [m["type"] for m in ws.sent] == ["snapshot", "update"]
        assert [v["mmsi"] for v in ws.sent[1]["vessels"]] == [MMSI]
        await finish((ws,), [task])

    asyncio.run(scenario())


def test_client_that_falls_far_behind_is_dropped():
    async def scenario():
        tracker = Tracker(TrackerConfig(), 51.44, 3.58)
        hub = Hub(tracker, send_timeout_s=3600, max_queue=5)
        ws = FakeSocket(stuck_after=1)
        task = asyncio.create_task(hub.serve(ws))
        await asyncio.sleep(0.01)
        for now in range(100, 110):
            tracker.handle(pos(), now=now)
            await asyncio.wait_for(hub.tick(now=now), timeout=0.5)
        assert hub.clients == set()
        await finish((ws,), [task])

    asyncio.run(scenario())
