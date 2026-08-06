"""Config system: pydantic-validated YAML with safe defaults.

Secrets (Tastytrade credentials) come from environment variables only —
never from YAML. ``paper_mode`` defaults to True and live trading also
requires the ``GODMODE_CONFIRM_LIVE=YES`` environment variable.

Hard risk constraints are validated here so a mis-edited YAML cannot
loosen them: per-trade risk is clamped to <= 4%, heat to <= 7%,
concurrency to <= 2, daily loss limit to <= 6%.
"""

from __future__ import annotations

import os
from datetime import date, time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# Absolute ceilings. Config may tighten these, never loosen them.
HARD_MAX_TRADE_RISK_PCT = 4.0
HARD_MAX_HEAT_PCT = 7.0
HARD_MAX_CONCURRENT = 2
HARD_DAILY_LOSS_LIMIT_PCT = 6.0


class RiskConfig(BaseModel):
    max_trade_risk_pct: float = Field(4.0, gt=0, le=HARD_MAX_TRADE_RISK_PCT)
    max_heat_pct: float = Field(7.0, gt=0, le=HARD_MAX_HEAT_PCT)
    max_concurrent_positions: int = Field(2, ge=1, le=HARD_MAX_CONCURRENT)
    daily_loss_limit_pct: float = Field(6.0, gt=0, le=HARD_DAILY_LOSS_LIMIT_PCT)
    # 1 SPY contract at worst fill $0.90 + $2.60 fees = $92.60 needs $2,315 at
    # the 4% cap; two concurrent 1-lots under 7% heat need $2,646; a -6% day
    # from $3,000 leaves $2,820 — still tradeable. Below 3000 the account is
    # structurally unsizeable and the runtime says so loudly (round-4 audit).
    min_equity: float = Field(3_000.0, ge=0, description="Refuse to trade below this account equity.")
    # Small-account minimum unit: when the ladder's target (e.g. 2%) cannot
    # fit one contract but the HARD 4% cap (incl. friction) can, take 1 and
    # log the overshoot. Never loosens the cap.
    allow_one_lot_minimum: bool = True
    # Fee-aware approval floor: the gross profit target per contract must
    # clear this multiple of round-trip friction or the trade is junk
    # economics regardless of the score.
    fee_floor_mult: float = Field(5.0, ge=1.0)
    # Spec §2.3: hard daily approval cap and the soft-loss tier.
    daily_trade_cap: int = Field(3, ge=1)
    # Day-trade budget. PDT was ELIMINATED effective 2026-06-04 (SEC-approved
    # 2026-04-14) with an 18-month broker phase-in — margin_large (no budget)
    # is the default. Set margin_small if YOUR broker still enforces a
    # counter (VERIFY-LIVE); cash logs the T+1 good-faith-violation caveat.
    account_type: Literal["margin_small", "margin_large", "cash"] = "margin_large"
    day_trades_per_5d: int = Field(3, ge=0)
    day_trade_file: str = "state/day_trades.json"
    max_data_staleness_sec: float = Field(5.0, gt=0, description="Reject intents on stale quotes.")
    min_intent_spacing_sec: float = Field(60.0, ge=0, description="Burst protection between intents.")
    lockout_file: str = "state/lockout.json"

    # Score band -> fraction of the per-trade cap. Keys are lower score bounds.
    # Phase A (launch): flat 2% (0.5 x 4%). Unlock Phase B {93:0.5, 97:0.75} then
    # C {93:0.5, 97:1.0} only per the calibration governance in docs/DESIGN_SPEC.md §2.1.
    sizing_ladder: dict[int, float] = Field(
        default={93: 0.50},
        description="Score band lower-bound -> multiplier on max_trade_risk_pct.",
    )

    @field_validator("sizing_ladder")
    @classmethod
    def _ladder_sane(cls, v: dict[int, float]) -> dict[int, float]:
        if not v:
            raise ValueError("sizing_ladder must not be empty")
        for band, mult in v.items():
            if not (0 < mult <= 1.0):
                raise ValueError(f"ladder multiplier for band {band} must be in (0, 1]")
        return dict(sorted(v.items()))


class ScoreWeights(BaseModel):
    """Max points per Setup Score component. Must sum to 100.

    Defaults follow the debate-arbitrated weights (docs/DESIGN_SPEC.md §1.2):
    microstructure and event cleanliness carry ZERO score weight — they act as
    hard gates instead ("cost control is not alpha"; calendar points reward
    the modal state and inflate scores exactly at threshold).
    """

    opening_range: int = 20
    breakout_confirmation: int = 25
    mtf_alignment: int = 21
    regime: int = 20
    macro_cluster: int = 8
    event_sentiment: int = 0
    dow_vix_preference: int = 6
    microstructure: int = 0

    @model_validator(mode="after")
    def _sum_100(self) -> "ScoreWeights":
        total = sum(self.__dict__.values())
        if total != 100:
            raise ValueError(f"score weights must sum to 100, got {total}")
        return self


class SignalConfig(BaseModel):
    min_score: float = Field(93.0, ge=90, le=100)   # 90 is the compiled floor (spec §preamble)
    weights: ScoreWeights = ScoreWeights()

    # Opening range (09:30-09:35 ET)
    or_start: time = time(9, 30)
    or_end: time = time(9, 35)
    or_min_width_pct: float = Field(0.07, description="OR width as % of price, lower bound.")
    or_max_width_pct: float = Field(0.35, description="OR width as % of price, upper bound.")
    or_max_width_atr_mult: float = Field(1.20, description="OR width must be <= this x 5m ATR(14).")

    # Breakout confirmation (audit round 2: tightened; chase gate + decay added)
    breakout_buffer_pct: float = Field(0.03, description="Close must clear OR edge by this % of price.")
    min_rel_volume: float = Field(1.30, description="5m volume vs 20-day same-slot average.")
    min_close_location: float = Field(0.75, description="Close location value within breakout bar.")
    max_chase_frac: float = Field(0.50, description="G-S4: reject if close extends past the OR edge by more than this x OR width.")
    breakout_decay_min: float = Field(30.0, description="Breakout points decay linearly to 0 over this many minutes.")

    # Multi-timeframe alignment
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    rsi_long_band: tuple[float, float] = (55.0, 78.0)
    rsi_short_band: tuple[float, float] = (22.0, 45.0)
    adx_period: int = 14
    adx_floor: float = 20.0

    # Entry window (ET). Opens 09:36 — the minute after the opening range
    # completes. The original 09:50 arbitration existed because regime features
    # were blind before 4 closed session bars; the prior-session/premarket
    # candle warmup removes that blindness, so the first-15-minute breakouts
    # (the best 0DTE moves) are in play WITH fully calibrated gates. If the
    # warmup fails (holiday, feed issue) the regime floor still holds entries
    # back until session bars suffice — the gate degrades, never the safety.
    entry_window_start: time = time(9, 36)
    entry_window_end: time = time(11, 30)

    # Regime
    min_regime_confidence: float = Field(0.70, ge=0, le=1)

    # VIX preference bands (spec §1.2 VIXPREF)
    vix_sweet_low: float = 14.0
    vix_sweet_high: float = 22.0
    vix_hard_max: float = 32.0

    # Day-of-week points: zero-weighted by default (spec: activatable only after
    # >=40 same-weekday outcomes with binomial p<0.05).
    dow_points: dict[str, int] = Field(default={"mon": 0, "tue": 0, "wed": 0, "thu": 0, "fri": 0})

    # Order-book layer (L1). Zero score weight — logged for calibration; acts
    # as an execution-hazard gate only. I = (Vbid - Vask) / (Vbid + Vask).
    book_gate_enabled: bool = True
    book_conflict_threshold: float = Field(0.30, ge=0, le=1,
                                           description="Gate when EWMA imbalance opposes direction beyond this.")
    book_ewma_alpha: float = Field(0.10, gt=0, le=1)
    book_thin_frac: float = Field(0.35, gt=0, le=1,
                                  description="Gate when displayed depth < this x rolling median (liquidity thinning).")


class EventConfig(BaseModel):
    blackout_before_min: int = Field(30, description="No entries this many minutes before high-impact events.")
    blackout_after_min: int = Field(15, description="No entries this many minutes after high-impact events.")
    fomc_day_lockout: bool = True
    high_impact_events: list[str] = Field(
        default=["FOMC", "CPI", "PPI", "NFP", "GDP", "PCE", "ISM", "Retail Sales", "Fed Chair Speech"]
    )
    calendar_file: str = "config/econ_calendar.yaml"


class ExecutionConfig(BaseModel):
    underlying: Literal["SPY", "SPX", "AUTO"] = "SPY"
    # Long leg |delta| in [0.45, 0.60], target 0.50 (spec §6.2 — pricier legs
    # are unreachable under the debit cap).
    long_delta_min: float = 0.45
    long_delta_max: float = 0.60
    width_strikes_spy: int = Field(2, description="Vertical width in $1 SPY strikes.")
    width_points_spx: int = Field(20, description="Vertical width in SPX points.")
    min_debit_pct_of_width: float = Field(0.30, description="Debit floor: below this the fill is fantasy.")
    max_debit_pct_of_width: float = Field(0.42, description="Acceptance cap at mid (spec §6.2); ladder cap 0.45W keeps RR >= 1.22.")
    ladder_cap_pct_of_width: float = Field(0.45, description="Absolute worst-fill price cap as fraction of width.")
    max_leg_spread_pct_of_mid: float = Field(10.0, description="Per-leg bid-ask limit as % of leg mid (spec §6.3).")
    max_leg_spread_abs: float = Field(0.05, description="Per-leg absolute bid-ask limit (SPY; SPX uses x12).")
    min_open_interest: int = 500
    quote_staleness_sec: float = 1.5
    max_combo_spread_spy: float = Field(0.08, description="Combined vertical spread gate (spec §6.3).")
    max_combo_spread_spx: float = Field(1.00, description="Combined vertical spread gate, SPX scale.")
    max_contracts_ceiling: int = Field(50, description="Fat-finger ceiling (SPY; SPX uses 5).")
    intent_max_age_sec: float = Field(90.0, description="Abandon entry when the intent is older than this.")
    # Cost is the one variable you fully control: friction per contract for a
    # full round trip (2 legs open + 2 legs close: commissions + clearing +
    # regulatory). Logged on every trade and charged in paper mode so paper
    # results are never fee-blind. SPX index options run higher — set it.
    friction_per_contract: float = Field(2.60, ge=0)

    # Entry ladder: start at mid, walk toward ask in steps.
    ladder_start_frac: float = Field(0.50, description="0.5 = start at mid of natural/mid range.")
    ladder_step_frac: float = 0.15
    ladder_step_wait_sec: float = 5.0
    ladder_max_steps: int = 3
    entry_abandon_sec: float = 30.0


class ExitConfig(BaseModel):
    """Exit parameters per the arbitrated priority table (spec §7)."""

    profit_target_mult: float = Field(1.65, description="P4a: close at mark >= this x entry debit...")
    profit_target_width_frac: float = Field(0.80, description="...capped at this x width (bid thins beyond).")
    hard_stop_pct: float = Field(50.0, description="P3: mark <= (1 - this%) x debit, 2 consecutive marks.")
    max_hold_min: int = Field(90, description="P5: close if held this long with mark < 1.10 x debit.")
    stale_flush: time = time(14, 50)
    force_flat: time = time(15, 30)
    structure_stop: bool = Field(True, description="P4b: close through OR trigger with P&L < +10% of debit.")
    # NYSE early-close days (13:00 ET). On these dates the runtime shifts
    # force_flat to 12:30 and stale_flush to 11:50 at boot (spec: half-day
    # rule) — a 15:30 flatten after a 13:00 close is a flatten that never runs.
    early_close_dates: list[date] = Field(default_factory=list)


class DataConfig(BaseModel):
    bar_seconds: int = 60
    macro_symbols: dict[str, str] = Field(
        default={"vix": "VIX", "dxy": "DX/Y", "tnx": "TNX", "gold": "/GC", "oil": "/CL", "eurusd": "EUR/USD"}
    )
    snapshot_path: str = "state/snapshot.json"
    trade_log_path: str = "state/trades.jsonl"
    decision_log_path: str = "state/decisions.jsonl"


class AppConfig(BaseModel):
    paper_mode: bool = True
    account_index: int = Field(0, description="Which account in the Tastytrade profile to use.")
    timezone: str = "America/New_York"
    risk: RiskConfig = RiskConfig()
    signal: SignalConfig = SignalConfig()
    events: EventConfig = EventConfig()
    execution: ExecutionConfig = ExecutionConfig()
    exits: ExitConfig = ExitConfig()
    data: DataConfig = DataConfig()

    @model_validator(mode="after")
    def _live_requires_confirmation(self) -> "AppConfig":
        if not self.paper_mode and os.environ.get("GODMODE_CONFIRM_LIVE") != "YES":
            raise ValueError(
                "paper_mode is false but GODMODE_CONFIRM_LIVE=YES is not set. "
                "Live trading requires both the YAML flag and the env var."
            )
        return self

    @model_validator(mode="after")
    def _spx_needs_size(self) -> "AppConfig":
        """SPX is silently unsizeable on small accounts: one contract at the
        0.45W ladder cap on a 20-wide is ~$900 (+higher index fees). Even the
        one-lot minimum under the 4% cap needs ~$23k equity (round-4 audit)."""
        if self.execution.underlying == "SPX":
            one_lot = 0.45 * self.execution.width_points_spx * 100
            if self.risk.min_equity * (self.risk.max_trade_risk_pct / 100.0) < one_lot:
                raise ValueError(
                    f"underlying SPX needs min_equity >= "
                    f"{one_lot / (self.risk.max_trade_risk_pct / 100.0):,.0f} "
                    f"(one contract ~${one_lot:,.0f} at the 4% cap); use SPY"
                )
        return self


def load_config(path: str | Path = "config/config.yaml") -> AppConfig:
    """Load and validate the YAML config; missing file yields safe defaults."""
    p = Path(path)
    raw = yaml.safe_load(p.read_text()) if p.exists() else {}
    return AppConfig.model_validate(raw or {})


class Credentials(BaseModel):
    """Tastytrade credentials, environment-only."""

    username: str
    password: str

    @classmethod
    def from_env(cls) -> "Credentials":
        user = os.environ.get("TASTYTRADE_USERNAME", "")
        pw = os.environ.get("TASTYTRADE_PASSWORD", "")
        if not user or not pw:
            raise RuntimeError("Set TASTYTRADE_USERNAME and TASTYTRADE_PASSWORD environment variables.")
        return cls(username=user, password=pw)
