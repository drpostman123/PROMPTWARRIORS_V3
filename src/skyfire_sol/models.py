"""Frozen data models shared across agents.

TradeIntent is pure data — no route, no transaction, no capability. The
only object that can reach the executor is ApprovedOrder, minted solely
by the SafetyGate (see safety/gate.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SleeveId(str, Enum):
    MEME_ROTATION = "MEME_ROTATION"
    CORE_HOLD = "CORE_HOLD"
    YIELD = "YIELD"
    PERPS = "PERPS"
    HL_ROTATION = "HL_ROTATION"


class Side(str, Enum):
    BUY = "buy"       # spend quote mint (USDC/SOL) for base
    SELL = "sell"     # sell base back to quote


class Urgency(str, Enum):
    NORMAL = "normal"
    URGENT = "urgent"   # breaker flatten / trailing-stop exit: Jito path eligible


class IntentKind(str, Enum):
    ENTRY = "entry"     # adds risk — full gate pipeline, CEO can veto
    EXIT = "exit"       # reduces risk — skips allocation checks, CEO cannot veto
    REBALANCE = "rebalance"


class RegimeState(str, Enum):
    RISK_ON = "risk_on"
    CHOPPY = "choppy"
    RISK_OFF = "risk_off"
    UNKNOWN = "unknown"   # maps to the most defensive caps


@dataclass(frozen=True)
class TokenFacts:
    """Everything the rug filter and entry scorer know about a token.
    ``None`` means the datum could not be fetched — gates fail closed."""

    mint: str
    symbol: str
    pair_address: Optional[str] = None
    price_usd: Optional[float] = None
    liquidity_usd: Optional[float] = None
    volume_1h_usd: Optional[float] = None
    volume_6h_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    price_change_1h_pct: Optional[float] = None
    pair_created_at: Optional[datetime] = None
    mint_authority_revoked: Optional[bool] = None
    freeze_authority_revoked: Optional[bool] = None
    lp_locked_or_burned_days: Optional[float] = None
    top10_holder_pct_ex_lp: Optional[float] = None
    holder_count: Optional[int] = None
    holder_count_prev: Optional[int] = None
    deployer: Optional[str] = None
    deployer_prior_rugs: Optional[int] = None
    sell_route_exists: Optional[bool] = None
    fetched_at: Optional[datetime] = None


@dataclass(frozen=True)
class RugCheckResult:
    gate: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class RugVerdict:
    mint: str
    passed: bool
    first_failure: Optional[str]          # gate name, None when passed
    checks: tuple[RugCheckResult, ...]
    ts: datetime


@dataclass(frozen=True)
class EntrySignal:
    mint: str
    symbol: str
    score: float
    vol_accel: float
    holder_growth: float
    above_ema: bool
    blowoff: bool
    price_usd: float
    ts: datetime


@dataclass(frozen=True)
class TradeIntent:
    """A sleeve's wish. Pure data; carries no capability."""

    intent_id: str
    sleeve: SleeveId
    kind: IntentKind
    side: Side
    mint: str                 # the non-quote token being bought/sold
    quote_mint: str           # what it's priced/settled in (USDC or wSOL)
    size_usd: float
    urgency: Urgency
    reason: str
    ts: datetime
    position_id: Optional[str] = None    # exits reference the position
    qty_raw: Optional[int] = None        # exits: exact raw units to sell


@dataclass(frozen=True)
class HlIntent:
    """HL_ROTATION sleeve wish: open or close one Hyperliquid perp
    position. Pure data, like TradeIntent."""

    intent_id: str
    coin: str                 # HL market name, e.g. "PUMP"
    action: str               # "open" (long) | "close"
    notional_usd: float       # target notional for opens; informational on close
    current_notional_usd: float
    mark_px: float            # mark at intent time (sizing; venue re-bounds slippage)
    reason: str
    ts: datetime


@dataclass(frozen=True)
class HlMarketStat:
    """One Hyperliquid perp market's rotation-relevant stats."""

    coin: str
    mark_px: float
    day_volume_usd: float
    ret_24h_pct: float
    funding_pct_hr: float
    open_interest_usd: float
    max_leverage: int


@dataclass(frozen=True)
class PerpIntent:
    """PERPS sleeve wish: change SOL-PERP notional by delta_usd (signed).
    Pure data, like TradeIntent."""

    intent_id: str
    delta_usd: float
    current_notional_usd: float
    reason: str
    ts: datetime


@dataclass(frozen=True)
class CeoVerdict:
    intent_id: str
    action: str               # "approve" | "resize" | "veto"
    size_mult: float          # 1.0 approve; <1 resize; 0 veto
    reasoning: str
    ts: datetime


@dataclass(frozen=True)
class JupQuote:
    input_mint: str
    output_mint: str
    in_amount: int            # raw units
    out_amount: int
    price_impact_pct: float
    slippage_bps: int
    route_json: str           # verbatim quote response, needed by /swap
    ts: datetime


@dataclass(frozen=True)
class Rejection:
    intent_id: str
    check: str                # gate name, e.g. "S7_correlation_bucket"
    detail: str
    ts: datetime


@dataclass(frozen=True)
class Fill:
    intent_id: str
    position_id: str
    sleeve: SleeveId
    side: Side
    mint: str
    quote_mint: str
    tx_sig: str
    in_amount_raw: int        # exact raw input units swapped
    quoted_out: int
    actual_out: int
    realized_slippage_pct: float
    price_impact_pct: float
    priority_fee_lamports: int
    clean: bool               # within clean-fill slippage tolerance
    ts: datetime


@dataclass(frozen=True)
class ExitOrder:
    position_id: str
    frac: float               # fraction of remaining position to sell
    reason: str               # "breaker_flatten" | "take_profit_2x" | "trail_stop" | "time_stop"
    urgency: Urgency


@dataclass
class Position:
    """Mutable book entry, owned by its sleeve agent."""

    position_id: str
    sleeve: SleeveId
    mint: str
    symbol: str
    quote_mint: str
    qty_raw: int              # raw token units held
    decimals: int
    entry_price_usd: float
    entry_ts: datetime
    peak_price_usd: float
    scaled_out: bool = False  # 2x partial take done → runner mode
    last_mark_usd: Optional[float] = None
    last_mark_ts: Optional[datetime] = None


@dataclass(frozen=True)
class AllocationTargets:
    """CEO output: desired share of NAV per sleeve, percent, sums to 100.
    Data only — the SafetyGate clamps before it takes effect."""

    targets: dict[str, float]
    regime: RegimeState
    reasoning: str
    ts: datetime


@dataclass(frozen=True)
class RegimeRead:
    state: RegimeState
    sol_trend: float          # fast EMA / slow EMA - 1
    meme_breadth: float       # share of scanned memes with positive 1h momentum
    agg_meme_volume_usd: float
    funding_rate_pct_hr: Optional[float]
    ts: datetime


@dataclass(frozen=True)
class SleevePerf:
    sleeve: SleeveId
    nav_usd: float
    ret_24h_pct: Optional[float]
    sharpe_7d: Optional[float]
    warmup: bool              # true until 7d of NAV history exists


@dataclass(frozen=True)
class PhantomCandidate:
    """A candidate that passed discovery but was not entered — training data."""

    mint: str
    symbol: str
    stage: str                # rug_reject | entry_reject | ceo_veto | gate_reject | near_miss
    reject_reason: str
    features: dict
    price_usd: Optional[float]
    ts: datetime


@dataclass
class SafetyStateView:
    """Read-only mirror of safety state published to the blackboard.
    The CEO sees this; only safety/ writes the underlying files."""

    breaker: str = "armed"            # armed | tripped | locked
    soft_tier_active: bool = False
    daily_pause_until: Optional[datetime] = None
    kill: bool = False
    probation: bool = True
    clean_fills: int = 0
    hwm_usd: float = 0.0
    drawdown_pct: float = 0.0


@dataclass(frozen=True)
class Snapshot:
    """Immutable blackboard snapshot swapped atomically by the single writer."""

    ts: datetime
    nav_usd: float
    sleeve_navs: dict[str, float]
    allocations_current: dict[str, float]
    allocations_target: dict[str, float]
    positions: tuple[Position, ...]
    prices_usd: dict[str, float]
    balances_raw: dict[str, int]      # mint -> raw units (wSOL key = native SOL)
    regime: RegimeRead
    safety: SafetyStateView
    perf: tuple[SleevePerf, ...] = field(default_factory=tuple)
