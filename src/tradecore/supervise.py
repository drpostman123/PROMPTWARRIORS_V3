"""Supervised task loop: one exception must never silently kill a safety
loop while positions are open. The wrapper logs the crash, waits, and
re-enters the task; CancelledError always propagates so shutdown works.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import structlog


async def supervised(
    name: str,
    factory: Callable[[], Awaitable[Any]],
    stop: asyncio.Event,
    log: structlog.BoundLogger,
    restart_delay: float = 1.0,
    crash_event: str = "task_crashed",
) -> None:
    while not stop.is_set():
        try:
            await factory()
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:                  # noqa: BLE001 — log, restart the loop
            log.error(crash_event, task=name, error=str(e))
            await asyncio.sleep(restart_delay)
