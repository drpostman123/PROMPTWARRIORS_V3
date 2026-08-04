"""Opening range (09:30-09:35 ET) tracking, width filter, breakout detection."""

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

    def add_bar(self, bar: Bar) -> None:
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

    def classify_breakout(self, bar: Bar, rel_volume: float) -> tuple[Optional[Direction], dict]:
        """Return (direction, evidence) if `bar` confirms a breakout, else (None, evidence).

        Confirmation = close beyond the OR edge by a buffer, with relative
        volume and close-location both above thresholds.
        """
        r = self.range
        evidence: dict = {"rel_volume": rel_volume}
        if r is None or not r.complete:
            return None, evidence
        buffer = bar.close * self._cfg.breakout_buffer_pct / 100.0
        clv = close_location_value(bar)
        evidence.update(clv=round(clv, 3), or_high=r.high, or_low=r.low)
        if bar.close > r.high + buffer:
            confirmed = rel_volume >= self._cfg.min_rel_volume and clv >= self._cfg.min_close_location
            return (Direction.LONG if confirmed else None), evidence
        if bar.close < r.low - buffer:
            confirmed = rel_volume >= self._cfg.min_rel_volume and clv <= 1.0 - self._cfg.min_close_location
            return (Direction.SHORT if confirmed else None), evidence
        return None, evidence
