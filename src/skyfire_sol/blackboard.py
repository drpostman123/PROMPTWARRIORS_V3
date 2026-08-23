"""BotState blackboard: single-writer shared state.

The NAV/marks task is the only writer; it swaps an immutable Snapshot
atomically (one attribute assignment — safe under the GIL and asyncio).
Everyone else (CEO, sleeves, dashboard, gate) reads ``.snapshot()``.

The safety fields in the snapshot are a read-only *mirror* published by
safety/ — the CEO can look, never touch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from skyfire_sol.models import (
    Position,
    RegimeRead,
    RegimeState,
    SafetyStateView,
    SleevePerf,
    Snapshot,
)


def _empty_snapshot() -> Snapshot:
    return Snapshot(
        ts=datetime.now(timezone.utc),
        nav_usd=0.0,
        sleeve_navs={},
        allocations_current={},
        allocations_target={},
        positions=(),
        prices_usd={},
        balances_raw={},
        regime=RegimeRead(
            state=RegimeState.UNKNOWN, sol_trend=0.0, meme_breadth=0.0,
            agg_meme_volume_usd=0.0, funding_rate_pct_hr=None,
            ts=datetime.now(timezone.utc)),
        safety=SafetyStateView(),
        perf=(),
    )


class Blackboard:
    def __init__(self) -> None:
        self._snapshot: Snapshot = _empty_snapshot()

    def snapshot(self) -> Snapshot:
        return self._snapshot

    def swap(self, snap: Snapshot) -> None:
        """Called only by the NAV task (the single writer)."""
        self._snapshot = snap

    # Convenience reads -------------------------------------------------
    def nav_usd(self) -> float:
        return self._snapshot.nav_usd

    def nav_age_s(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self._snapshot.ts).total_seconds()

    def positions(self) -> tuple[Position, ...]:
        return self._snapshot.positions

    def sleeve_nav(self, sleeve: str) -> float:
        return self._snapshot.sleeve_navs.get(sleeve, 0.0)

    def perf(self) -> tuple[SleevePerf, ...]:
        return self._snapshot.perf
