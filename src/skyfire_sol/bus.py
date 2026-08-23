"""In-process pub/sub message bus.

Topics fan out to per-subscriber bounded asyncio.Queues. Telemetry-class
topics drop-oldest under pressure; intent/fill-class topics never drop —
a full critical queue back-pressures the publisher instead.

The bus carries observability and workflow traffic ONLY. The approval
edge (SafetyGate -> ExecutionAgent) is a direct typed call minting a
capability token — it deliberately does not ride this bus, otherwise the
token guarantee would be decorative.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, AsyncIterator

from tradecore.logging import get_logger

log = get_logger("bus")

# Topics where dropping a message loses money or corrupts the book.
CRITICAL_TOPICS = {"intents", "fills", "exits", "allocations", "persist"}


class Bus:
    def __init__(self, maxsize: int = 512) -> None:
        self._maxsize = maxsize
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subs[topic].append(q)
        return q

    async def publish(self, topic: str, msg: Any) -> None:
        for q in self._subs[topic]:
            if topic in CRITICAL_TOPICS:
                await q.put(msg)                  # back-pressure, never drop
            else:
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:         # telemetry: drop oldest
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        q.put_nowait(msg)
                    except asyncio.QueueFull:
                        log.warning("bus_drop", topic=topic)

    async def stream(self, topic: str) -> AsyncIterator[Any]:
        q = self.subscribe(topic)
        while True:
            yield await q.get()
