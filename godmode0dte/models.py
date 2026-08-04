"""Shared domain models. Pure data — importable by every layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Direction(str, Enum):
    LONG = "long"    # bullish -> call debit vertical
    SHORT = "short"  # bearish -> put debit vertical


class Regime(str, Enum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    UNKNOWN = "unknown"


class VolRegime(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    ELEVATED = "elevated"
    EXTREME = "extreme"


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    ts: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True)
class OpeningRange:
    high: float
    low: float
    volume: float
    complete: bool = False

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2.0


@dataclass(frozen=True)
class RegimeState:
    """Output of the regime layer. HMM or rules, same shape."""

    regime: Regime
    vol_regime: VolRegime
    confidence: float                      # 0..1
    probabilities: dict[str, float] = field(default_factory=dict)
    source: str = "rules"                  # "rules" | "hmm"


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    points: float
    max_points: float
    detail: str = ""


@dataclass(frozen=True)
class SetupScore:
    """Transparent 0-100 score with full component breakdown."""

    total: float
    direction: Optional[Direction]
    components: tuple[ScoreComponent, ...]
    hard_gate_failures: tuple[str, ...] = ()
    ts: Optional[datetime] = None

    @property
    def tradeable(self) -> bool:
        return not self.hard_gate_failures and self.direction is not None


@dataclass(frozen=True)
class VerticalSpec:
    """A fully specified defined-risk debit vertical."""

    underlying: str
    direction: Direction
    expiration: str                 # YYYY-MM-DD (today, 0DTE)
    long_strike: float
    short_strike: float
    width: float                    # dollars per share / points
    debit: float                    # limit debit per spread (per share)
    contracts: int
    long_symbol: str = ""
    short_symbol: str = ""

    @property
    def max_loss(self) -> float:
        """Total dollars at risk = debit paid (defined risk)."""
        return self.debit * self.contracts * 100

    @property
    def max_gain(self) -> float:
        return (self.width - self.debit) * self.contracts * 100


@dataclass(frozen=True)
class TradeIntent:
    """What the signal engine is allowed to produce. It carries no ability
    to trade — only the RiskGovernor can turn it into an ApprovedTrade."""

    score: SetupScore
    vertical: VerticalSpec
    ts: datetime
    quote_ts: datetime               # freshness of the option quotes used


@dataclass(frozen=True)
class Rejection:
    reason: str
    detail: str
    ts: datetime


@dataclass(frozen=True)
class Position:
    trade_id: str
    vertical: VerticalSpec
    entry_debit: float               # actual fill, per share
    entry_ts: datetime
    score_at_entry: float
    or_mid: float                    # opening-range midpoint for the structure stop
    current_value: float = 0.0
    exit_ts: Optional[datetime] = None
    exit_value: Optional[float] = None
    exit_reason: Optional[str] = None

    @property
    def open(self) -> bool:
        return self.exit_ts is None

    @property
    def risk_dollars(self) -> float:
        """Heat contribution: max(entry_debit, current mark) x contracts x 100.

        Using the max means winners keep consuming risk budget at their marked
        value while losers still count at full entry risk (spec §2.3 P2 —
        entry-debit-only undercounts what a winner has at risk).
        """
        per_share = max(self.entry_debit, self.current_value)
        return per_share * self.vertical.contracts * 100

    @property
    def pnl(self) -> float:
        val = self.exit_value if self.exit_value is not None else self.current_value
        return (val - self.entry_debit) * self.vertical.contracts * 100
