"""WebSocket-clients van de live kaart: snapshot bij verbinden, daarna gebundelde updates.

Elke client heeft een eigen wachtrij en schrijftaak. ``tick`` zet berichten
alleen in wachtrijen en wacht nooit op een socket: een telefoon die zonder
afmelden van de wifi verdwijnt, mag de updates voor de rest niet ophouden.
"""

import asyncio
from contextlib import suppress
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from aisws.tracker import Tracker

SEND_TIMEOUT_S = 5.0
MAX_QUEUE = 30


class _Client:
    def __init__(self, ws: WebSocket, max_queue: int) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self.writer: asyncio.Task[None] | None = None


class Hub:
    def __init__(
        self, tracker: Tracker, send_timeout_s: float = SEND_TIMEOUT_S, max_queue: int = MAX_QUEUE
    ) -> None:
        self.tracker = tracker
        self.send_timeout_s = send_timeout_s
        self.max_queue = max_queue
        self.clients: set[_Client] = set()
        self._closing: set[asyncio.Task[None]] = set()  # vaste referentie, anders ruimt de GC ze op

    async def serve(self, ws: WebSocket) -> None:
        await ws.accept()
        client = _Client(ws, self.max_queue)
        # Snapshot in de wachtrij en registreren zonder tussenliggende await:
        # zo mist de client geen enkele wijziging die daarna komt.
        client.queue.put_nowait({"type": "snapshot", "vessels": self.tracker.live_vessels()})
        self.clients.add(client)
        client.writer = asyncio.create_task(self._write(client))
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            self.clients.discard(client)
            client.writer.cancel()

    async def _write(self, client: _Client) -> None:
        try:
            while True:
                message = await client.queue.get()
                await asyncio.wait_for(client.ws.send_json(message), self.send_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._drop(client)

    def _drop(self, client: _Client) -> None:
        """Client afsluiten zonder erop te wachten; de browser verbindt zelf opnieuw."""
        self.clients.discard(client)
        if client.writer is not None and client.writer is not asyncio.current_task():
            client.writer.cancel()

        async def close() -> None:
            with suppress(Exception):
                await asyncio.wait_for(client.ws.close(), self.send_timeout_s)

        task = asyncio.get_running_loop().create_task(close())
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def tick(self, now: float) -> None:
        """Verouderde schepen opruimen en wijzigingen in de wachtrij van elke client zetten."""
        self.tracker.sweep(now)
        changed, removed = self.tracker.drain_changes()
        if not (changed or removed) or not self.clients:
            return
        vessels = []
        for mmsi in sorted(changed):
            state = self.tracker.vessel(mmsi)
            if state is not None and state.live:
                vessels.append(state.as_live())
        message = {"type": "update", "vessels": vessels, "removed": sorted(removed)}
        for client in list(self.clients):
            try:
                client.queue.put_nowait(message)
            except asyncio.QueueFull:
                self._drop(client)
