"""Perfect-setup detector: the discretionary eye, mechanized.

Computes one MetricSnapshot per tick from live data, then evaluates the
operator's condition lists. A side fires only when the required fraction
of its conditions pass (default: ALL of them — "perfect" means perfect).
Every fire and every near-miss (>= 75% of conditions) is logged with the
full metric snapshot, so the operator can tune conditions against evidence
instead of memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np

from godmode0dte.features.bars import resample
from godmode0dte.features.indicators import atr, rsi, vwap
from godmode0dte.models import Bar, Direction
from godmode0dte.monitoring.logging import get_logger
from godmode0dte_v2.config import ConvictionCondition, V2Config

log = get_logger("v2.conviction")


@dataclass(frozen=True)
class MetricSnapshot:
    rsi_2m: Optional[float]
    rsi_5m: Optional[float]
    vwap_stretch_atr: Optional[float]
    day_move_pct: Optional[float]
    book_imbalance: Optional[float]
    rel_volume: Optional[float]
    vix: Optional[float]
    minutes_since_open: float

    def get(self, metric: str) -> Optional[float]:
        return getattr(self, metric)


@dataclass(frozen=True)
class ConvictionSignal:
    direction: Direction               # LONG = buy calls (oversold), SHORT = buy puts (overbought)
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    fraction: float
    snapshot: MetricSnapshot
    ts: datetime

    @property
    def perfect(self) -> bool:
        return not self.failed


def _passes(cond: ConvictionCondition, value: Optional[float]) -> bool:
    if value is None:
        return False                    # missing data NEVER counts toward perfect
    if cond.op == "<=":
        return value <= cond.value
    if cond.op == ">=":
        return value >= cond.value
    if cond.op == "abs>=":
        return abs(value) >= cond.value
    return abs(value) <= cond.value


class PerfectSetupDetector:
    def __init__(self, cfg: V2Config) -> None:
        self._cfg = cfg
        self._tz = ZoneInfo(cfg.timezone)
        self._last_fire: dict[Direction, datetime] = {}

    def compute_snapshot(self, bars_1m: list[Bar], warm_5m: list[Bar],
                         book_imbalance: Optional[float], rel_volume: Optional[float],
                         vix: Optional[float], now: datetime) -> Optional[MetricSnapshot]:
        if len(bars_1m) < 6:
            return None
        closes1 = np.array([b.close for b in bars_1m])
        bars2 = resample(bars_1m, 2, drop_partial=True)
        bars5_ctx = warm_5m + resample(bars_1m, 5, drop_partial=True)
        r2 = float(rsi(np.array([b.close for b in bars2]), 14)[-1]) if len(bars2) >= 16 else None
        r5 = float(rsi(np.array([b.close for b in bars5_ctx]), 14)[-1]) if len(bars5_ctx) >= 16 else None
        atr5 = float(np.nan_to_num(atr(bars5_ctx, 14)[-1])) if len(bars5_ctx) >= 15 else 0.0
        v = vwap(bars_1m)
        price = closes1[-1]
        stretch = float((price - v[-1]) / atr5) if atr5 > 0 else None
        day_move = 100.0 * (price - bars_1m[0].open) / bars_1m[0].open
        local = now.astimezone(self._tz)
        mins = (local.hour - 9) * 60 + local.minute - 30
        return MetricSnapshot(
            rsi_2m=r2, rsi_5m=r5, vwap_stretch_atr=stretch,
            day_move_pct=round(day_move, 3), book_imbalance=book_imbalance,
            rel_volume=rel_volume, vix=vix, minutes_since_open=float(mins))

    def evaluate(self, snap: MetricSnapshot, now: datetime) -> Optional[ConvictionSignal]:
        """Return the best firing side's signal, or a near-miss log, or None."""
        best: Optional[ConvictionSignal] = None
        for direction, conds in ((Direction.LONG, self._cfg.conviction.long_call_conditions),
                                 (Direction.SHORT, self._cfg.conviction.long_put_conditions)):
            enabled = [c for c in conds if c.enabled]
            if not enabled:
                continue
            last = self._last_fire.get(direction)
            if last and (now - last).total_seconds() / 60 < self._cfg.conviction.cooldown_min:
                continue
            passed, failed = [], []
            for c in enabled:
                (passed if _passes(c, snap.get(c.metric)) else failed).append(
                    f"{c.metric}{c.op}{c.value}")
            frac = len(passed) / len(enabled)
            sig = ConvictionSignal(direction=direction, passed=tuple(passed),
                                   failed=tuple(failed), fraction=frac,
                                   snapshot=snap, ts=now)
            required = self._cfg.conviction.min_conditions_pct / 100.0
            if frac >= required and (best is None or frac > best.fraction):
                best = sig
            elif frac >= 0.75:
                log.info("v2_near_miss", direction=direction.value,
                         fraction=round(frac, 2), failed=list(sig.failed))
        if best is not None:
            self._last_fire[best.direction] = now
            log.info("v2_perfect_setup", direction=best.direction.value,
                     passed=list(best.passed), snapshot=vars(best.snapshot))
        return best
