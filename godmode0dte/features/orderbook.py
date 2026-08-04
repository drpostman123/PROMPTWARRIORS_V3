"""Top-of-book imbalance and liquidity-thinning detection.

    I = (V_bid - V_ask) / (V_bid + V_ask)

Positive: buyers stacked, sellers thin. Negative: the floor is fake.
The candle tells you what happened; the book tells you what's about to.

Scope honesty: DXLink gives L1 (inside market) sizes, not the full depth
ladder — this reads displayed size at the touch, EWMA-smoothed against
quote flicker, plus a thinning detector: total displayed depth vs its
rolling session median. Per the design governance (DESIGN_SPEC §1.2),
imbalance carries ZERO score weight until the logged data earns it via
the logistic test — but a book stacked *against* the trade direction or
a thinning inside market is an execution hazard and may gate immediately.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Optional

from godmode0dte.models import Direction


@dataclass(frozen=True)
class BookState:
    imbalance: float          # EWMA of I, in [-1, 1]
    imbalance_raw: float      # instantaneous I
    depth: float              # bid_size + ask_size (displayed, inside)
    depth_median: float       # rolling session median depth
    thinning: bool            # depth below thin_frac x median
    ts: Optional[datetime]


class BookPulse:
    """Streaming L1 imbalance + depth baseline for one symbol."""

    def __init__(self, ewma_alpha: float = 0.10, depth_window: int = 600,
                 thin_frac: float = 0.35) -> None:
        self._alpha = ewma_alpha
        self._thin_frac = thin_frac
        self._depths: deque[float] = deque(maxlen=depth_window)
        self._ewma: Optional[float] = None
        self._last_raw = 0.0
        self._last_ts: Optional[datetime] = None

    def update(self, bid_size: float, ask_size: float, ts: datetime) -> None:
        total = bid_size + ask_size
        if total <= 0:
            return
        raw = (bid_size - ask_size) / total
        self._last_raw = raw
        self._ewma = raw if self._ewma is None else self._alpha * raw + (1 - self._alpha) * self._ewma
        self._depths.append(total)
        self._last_ts = ts

    @property
    def ready(self) -> bool:
        return self._ewma is not None and len(self._depths) >= 30

    def state(self) -> BookState:
        depth = self._depths[-1] if self._depths else 0.0
        depth_med = median(self._depths) if len(self._depths) >= 30 else 0.0
        return BookState(
            imbalance=round(self._ewma, 4) if self._ewma is not None else 0.0,
            imbalance_raw=round(self._last_raw, 4),
            depth=depth,
            depth_median=depth_med,
            thinning=bool(depth_med > 0 and depth < self._thin_frac * depth_med),
            ts=self._last_ts,
        )

    def conflicts(self, direction: Direction, threshold: float) -> bool:
        """Book stacked against the trade: I opposes direction beyond threshold."""
        if not self.ready:
            return False
        i = self._ewma or 0.0
        return i < -threshold if direction is Direction.LONG else i > threshold
