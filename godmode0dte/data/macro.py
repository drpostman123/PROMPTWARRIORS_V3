"""Macro cluster: DXY, VIX, yields, gold/oil, key FX.

Holds latest values + a short intraday history per symbol and scores
directional confirmation for a proposed SPY/SPX trade. Includes the
dedup rule: highly SPX-correlated moves (VIX inverse) are capped so the
same information is not double-counted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from godmode0dte.models import Direction


@dataclass
class MacroSeries:
    values: list[tuple[datetime, float]] = field(default_factory=list)

    def add(self, ts: datetime, value: float) -> None:
        self.values.append((ts, value))
        if len(self.values) > 2000:
            del self.values[:1000]

    @property
    def last(self) -> Optional[float]:
        return self.values[-1][1] if self.values else None

    def change_pct(self, lookback: int = 30) -> Optional[float]:
        """% change vs `lookback` observations ago."""
        if len(self.values) <= lookback:
            return None
        prev = self.values[-lookback - 1][1]
        return 100.0 * (self.values[-1][1] - prev) / prev if prev else None


class MacroCluster:
    """Aggregates macro symbols and produces a confirmation score."""

    def __init__(self) -> None:
        self._series: dict[str, MacroSeries] = {}
        self.last_update: Optional[datetime] = None

    def update(self, key: str, value: float, ts: Optional[datetime] = None) -> None:
        ts = ts or datetime.now(timezone.utc)
        self._series.setdefault(key, MacroSeries()).add(ts, value)
        self.last_update = ts

    def get(self, key: str) -> Optional[float]:
        s = self._series.get(key)
        return s.last if s else None

    @property
    def vix(self) -> Optional[float]:
        return self.get("vix")

    def snapshot(self) -> dict[str, Optional[float]]:
        return {k: s.last for k, s in self._series.items()}

    def confirmation_points(self, direction: Direction, max_points: int) -> tuple[float, str]:
        """Score macro alignment with the trade direction.

        Votes (equity-bullish when):
          - VIX falling intraday        (capped: half weight — dedup vs SPX itself)
          - DXY falling
          - 10Y yield (TNX) falling
          - Gold flat/falling (risk-on), Oil not crashing
        Missing series simply don't vote; score is scaled to voters.
        """
        sign = 1.0 if direction is Direction.LONG else -1.0
        votes: list[tuple[str, float, float]] = []  # (name, vote in [-1,1], weight)

        def add_vote(name: str, chg: Optional[float], bullish_when_down: bool, weight: float = 1.0):
            if chg is None:
                return
            raw = -chg if bullish_when_down else chg
            v = max(-1.0, min(1.0, raw / 0.3))  # +/-0.3% intraday move saturates the vote
            votes.append((name, v * sign, weight))

        vix_s = self._series.get("vix")
        add_vote("vix", vix_s.change_pct(30) if vix_s else None, bullish_when_down=True, weight=0.5)
        for key, down_is_bullish in (("dxy", True), ("tnx", True), ("gold", True)):
            s = self._series.get(key)
            add_vote(key, s.change_pct(30) if s else None, bullish_when_down=down_is_bullish)

        if not votes:
            return 0.0, "no macro data"
        total_w = sum(w for _, _, w in votes)
        score01 = sum(v * w for _, v, w in votes) / total_w        # -1..1
        points = max(0.0, score01) * max_points
        detail = ", ".join(f"{n}:{v:+.2f}" for n, v, _ in votes)
        return round(points, 2), detail
