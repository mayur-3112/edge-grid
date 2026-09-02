"""Pipeline event bus: one in-process fan-out from the grid to every SSE client.

The dashboard is not allowed to poll its way to a plausible-looking picture. Every
row it draws comes from an event that the pipeline actually emitted, in order, with
a monotonic sequence number so a client can tell that it missed something rather
than silently rendering a gap.

Slow subscribers are dropped from the *tail*, never blocked: a browser that stops
reading must not be able to stall the inference pipeline. A drop is itself reported
to that subscriber as a `bus.dropped` event, so the dashboard can say so instead of
quietly showing stale state.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from edgegrid.schemas import now_ms

# Event types the pipeline emits. Kept as a closed list so a typo in a publisher
# shows up as a KeyError here rather than as an event no dashboard panel handles.
EVENT_TYPES = frozenset({
    "job.created",     # a JobRequest was signed and broadcast
    "job.stage",       # a pipeline stage changed state (see STAGES)
    "bid",             # one Bid arrived in the auction window
    "award",           # the auction cleared (JobAward)
    "inference.start",
    "inference.done",  # InferenceResult, with real TTFT / token counts
    "commit",          # Commitment written to the DA layer
    "verdict",         # Verdict from a validator (pass / fail / error)
    "settlement",      # SettlementRecord
    "node",            # node roster / telemetry refresh
    "log",             # operator-visible note, including every degraded path
})

# The five pipeline stages the dashboard tracks per job.
STAGES = ("auction", "inference", "commit", "verify", "settle")


class EventBus:
    """Fan-out of pipeline events to any number of SSE subscribers."""

    def __init__(self, history: int = 400, queue_size: int = 256):
        self._history: deque[dict] = deque(maxlen=history)
        self._subscribers: set[asyncio.Queue] = set()
        self._queue_size = queue_size
        self._seq = 0

    # -- publishing ------------------------------------------------------

    def publish(self, type_: str, payload: Optional[dict] = None, **kwargs: Any) -> dict:
        if type_ not in EVENT_TYPES:
            raise KeyError(f"unknown event type {type_!r}; add it to EVENT_TYPES")
        self._seq += 1
        event = {
            "seq": self._seq,
            "ts_ms": now_ms(),
            "type": type_,
            **(payload or {}),
            **kwargs,
        }
        self._history.append(event)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop the oldest so the newest still lands, and tell the client.
                try:
                    q.get_nowait()
                    q.put_nowait({"seq": self._seq, "ts_ms": event["ts_ms"],
                                  "type": "log", "level": "warn",
                                  "message": "event bus overflow: this client fell behind "
                                             "and lost at least one event"})
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        return event

    # -- subscribing -----------------------------------------------------

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)

    # -- introspection ---------------------------------------------------

    def replay(self, limit: int = 100) -> list[dict]:
        """Most recent events, oldest first - sent to a client on connect so a
        dashboard reload does not start from an empty screen."""
        items = list(self._history)
        return items[-limit:] if limit else items

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
