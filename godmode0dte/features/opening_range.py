"""Opening range (09:30-09:35 ET) tracking, width filter, breakout detection.

Audit round 2 fixes: bar-timestamp dedupe (add_bar is idempotent — the
runtime feeds recent bars every tick and volume must not double-count),
the G-S4 chase gate (never buy a breakout already >50% of the OR width
past the edge), and a first-confirm latch per side so breakout points can
decay with age (spec §1.2 ORB decay).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from godmode0dte.config import SignalConfig
from godmode0dte.models import Bar, Direction, OpeningRange
from godmode0dte.features.indicators import close_location_value


class OpeningRangeTracker:
    """Accumulates the OR window, then classifies breakout bars."""

    def __init__(self, cfg: SignalConfig, tz: str = "America/New_York") -> None:
        self._cfg = cfg
        self._tz = ZoneInfo(tz)
        self._high: Optional[float] = None
        self._low: Optional[float] = None
        self._volume: float = 0.0
        self._complete = False
        self._seen: set[datetime] = set()
        self._first_confirm: dict[Direction, datetime] = {}

    def add_bar(self, bar: Bar) -> None:
        if bar.ts in self._seen:
            return
        self._seen.add(bar.ts)
        t = bar.ts.astimezone(self._tz).time()
        if self._cfg.or_start <= t < self._cfg.or_end:
            self._high = bar.high if self._high is None else max(self._high, bar.high)
            self._low = bar.low if self._low is None else min(self._low, bar.low)
            self._volume += bar.volume
        elif t >= self._cfg.or_end and self._high is not None:
            self._complete = True

    @property
    def range(self) -> Optional[OpeningRange]:
        if self._high is None or self._low is None:
            return None
        return OpeningRange(high=self._high, low=self._low, volume=self._volume,
                            complete=self._complete)

    def width_ok(self, price: float, atr5m: float) -> tuple[bool, str]:
        """Width filter: not too tight (chop), not too wide (exhaustion)."""
        r = self.range
        if r is None or not r.complete:
            return False, "opening range not complete"
        width_pct = 100.0 * r.width / price
        if width_pct < self._cfg.or_min_width_pct:
            return False, f"OR width {width_pct:.3f}% too narrow (chop risk)"
        if width_pct > self._cfg.or_max_width_pct:
            return False, f"OR width {width_pct:.3f}% too wide (exhaustion risk)"
        if atr5m > 0 and r.width > self._cfg.or_max_width_atr_mult * atr5m:
            return False, f"OR width {r.width:.2f} > {self._cfg.or_max_width_atr_mult}x ATR5m"
        return True, "ok"

    def breakout_age_min(self, direction: Direction, now: datetime) -> float:
        """Minutes since this side's breakout first confirmed (0 if just now)."""
        first = self._first_confirm.get(direction)
        return max(0.0, (now - first).total_seconds() / 60.0) if first else 0.0

    def classify_breakout(self, bar: Bar, rel_volume: float) -> tuple[Optional[Direction], dict]:
        """Return (direction, evidence) if `bar` confirms a breakout.

        Confirmation = close beyond the OR edge by a buffer, with relative
        volume and close-location above thresholds — and NOT a chase: a close
        extended more than max_chase_frac x OR width past the edge is refused
        (G-S4); that entry pays top tick for a move that already happened.
        """
        r = self.range
        evidence: dict = {"rel_volume": rel_volume}
        if r is None or not r.complete or r.width <= 0:
            return None, evidence
        buffer = bar.close * self._cfg.breakout_buffer_pct / 100.0
        clv = close_location_value(bar)
        evidence.update(clv=round(clv, 3), or_high=r.high, or_low=r.low)

        direction: Optional[Direction] = None
        if bar.close > r.high + buffer:
            ext = (bar.close - r.high) / r.width
            evidence["chase"] = round(ext, 3)
            if ext > self._cfg.max_chase_frac:
                return None, evidence
            if rel_volume >= self._cfg.min_rel_volume and clv >= self._cfg.min_close_location:
                direction = Direction.LONG
        elif bar.close < r.low - buffer:
            ext = (r.low - bar.close) / r.width
            evidence["chase"] = round(ext, 3)
            if ext > self._cfg.max_chase_frac:
                return None, evidence
            if rel_volume >= self._cfg.min_rel_volume and clv <= 1.0 - self._cfg.min_close_location:
                direction = Direction.SHORT
        if direction is not None:
            self._first_confirm.setdefault(direction, bar.ts)
        return direction, evidence
