"""Tick-to-bar aggregation and multi-timeframe resampling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from godmode0dte.models import Bar


class BarAggregator:
    """Builds fixed-interval bars from trade prints (or mid-quote samples)."""

    def __init__(self, seconds: int = 60) -> None:
        self._seconds = seconds
        self._current: Optional[dict] = None
        self.bars: list[Bar] = []

    def _bucket(self, ts: datetime) -> datetime:
        epoch = int(ts.replace(tzinfo=ts.tzinfo or timezone.utc).timestamp())
        return datetime.fromtimestamp(epoch - epoch % self._seconds, tz=timezone.utc)

    def add(self, price: float, volume: float, ts: datetime) -> Optional[Bar]:
        """Add a print; returns the completed bar when a bucket rolls over."""
        bucket = self._bucket(ts)
        completed: Optional[Bar] = None
        if self._current is None or bucket > self._current["ts"]:
            if self._current is not None:
                completed = self._finalize()
                self.bars.append(completed)
            self._current = {
                "ts": bucket, "open": price, "high": price, "low": price,
                "close": price, "volume": 0.0,
            }
        c = self._current
        c["high"] = max(c["high"], price)
        c["low"] = min(c["low"], price)
        c["close"] = price
        c["volume"] += volume
        return completed

    def _finalize(self) -> Bar:
        c = self._current
        return Bar(ts=c["ts"], open=c["open"], high=c["high"], low=c["low"],
                   close=c["close"], volume=c["volume"])


def resample(bars: list[Bar], minutes: int) -> list[Bar]:
    """Resample 1-minute bars to a higher timeframe. Partial last bucket included."""
    if not bars:
        return []
    out: list[Bar] = []
    bucket: list[Bar] = []
    span = timedelta(minutes=minutes)
    start = bars[0].ts
    for b in bars:
        if b.ts >= start + span:
            out.append(_merge(bucket))
            while b.ts >= start + span:
                start += span
            bucket = []
        bucket.append(b)
    if bucket:
        out.append(_merge(bucket))
    return out


def _merge(bucket: list[Bar]) -> Bar:
    return Bar(
        ts=bucket[0].ts,
        open=bucket[0].open,
        high=max(b.high for b in bucket),
        low=min(b.low for b in bucket),
        close=bucket[-1].close,
        volume=sum(b.volume for b in bucket),
    )
