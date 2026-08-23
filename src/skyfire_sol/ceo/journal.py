"""CEO allocation-decision journal — every reallocation with reasoning.
This is CEO training data for the self-improving loop. Rows ride the
persist topic; the CEO never touches the DB writer directly."""

from __future__ import annotations

from typing import Awaitable, Callable

from skyfire_sol.models import AllocationTargets


async def journal_allocation(
    publish: Callable[[str, object], Awaitable[None]],
    proposed: AllocationTargets,
    clamped: dict[str, float],
    greedy_scores: dict[str, float],
    applied: bool = True,
) -> None:
    await publish("persist", {
        "table": "allocations",
        "ts": proposed.ts.isoformat(),
        "regime": proposed.regime.value,
        "greedy_scores_json": greedy_scores,
        "targets_json": proposed.targets,
        "clamped_targets_json": clamped,
        "reasoning": proposed.reasoning,
        "applied": 1 if applied else 0,
    })
