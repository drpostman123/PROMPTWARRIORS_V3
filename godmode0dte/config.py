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
from datetime import time
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
    min_equity: float = Field(2_000.0, ge=0, description="Refuse to trade below this account equity.")
    max_data_staleness_sec: float = Field(5.0, gt=0, description="Reject intents on stale quotes.")
    min_intent_spacing_sec: float = Field(60.0, ge=0, description="Burst protection between intents.")
    lockout_file: str = "state/lockout.json"

    # Score band -> fraction of the per-trade cap. Keys are lower score bounds.
    sizing_ladder: dict[int, float] = Field(
        default={93: 0.50, 95: 0.75, 97: 1.00},
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
    """Max points per Setup Score component. Must sum to 100."""

    opening_range: int = 15
    breakout_confirmation: int = 20
    mtf_alignment: int = 20
    regime: int = 15
    macro_cluster: int = 10
    event_sentiment: int = 5
    dow_vix_preference: int = 5
    microstructure: int = 10

    @model_validator(mode="after")
    def _sum_100(self) -> "ScoreWeights":
        total = sum(self.__dict__.values())
        if total != 100:
            raise ValueError(f"score weights must sum to 100, got {total}")
        return self


class SignalConfig(BaseModel):
    min_score: float = Field(93.0, ge=0, le=100)
    weights: ScoreWeights = ScoreWeights()

    # Opening range (09:30-09:35 ET)
    or_start: time = time(9, 30)
    or_end: time = time(9, 35)
    or_min_width_pct: float = Field(0.05, description="OR width as % of price, lower bound.")
    or_max_width_pct: float = Field(0.35, description="OR width as % of price, upper bound.")
    or_max_width_atr_mult: float = Field(1.20, description="OR width must be <= this x 5m ATR(14).")

    # Breakout confirmation
    breakout_buffer_pct: float = Field(0.02, description="Close must clear OR edge by this % of price.")
    min_rel_volume: float = Field(1.30, description="5m volume vs 20-day same-slot average.")
    min_close_location: float = Field(0.70, description="Close location value within breakout bar.")

    # Multi-timeframe alignment
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    rsi_long_band: tuple[float, float] = (55.0, 78.0)
    rsi_short_band: tuple[float, float] = (22.0, 45.0)
    adx_period: int = 14
    adx_floor: float = 20.0

    # Entry window (ET). Signals outside this window are rejected.
    entry_window_start: time = time(9, 40)
    entry_window_end: time = time(11, 30)

    # Regime
    min_regime_confidence: float = Field(0.65, ge=0, le=1)

    # VIX preference bands
    vix_sweet_low: float = 13.0
    vix_sweet_high: float = 24.0
    vix_hard_max: float = 32.0

    # Day-of-week points (Mon..Fri)
    dow_points: dict[str, int] = Field(default={"mon": 2, "tue": 3, "wed": 2, "thu": 3, "fri": 1})


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
    long_delta_min: float = 0.55
    long_delta_max: float = 0.70
    width_strikes_spy: int = Field(2, description="Vertical width in $1 SPY strikes.")
    width_points_spx: int = Field(20, description="Vertical width in SPX points.")
    min_debit_pct_of_width: float = Field(0.30, description="Debit floor: below this the fill is fantasy.")
    max_debit_pct_of_width: float = Field(0.55, description="Debit cap: preserves reward:risk >= ~0.8.")
    max_leg_spread_pct_of_mid: float = Field(6.0, description="Per-leg bid-ask limit as % of leg mid.")
    max_leg_spread_abs: float = Field(0.10, description="Per-leg absolute bid-ask limit (SPY scale; x10 SPX).")
    min_open_interest: int = 250
    quote_staleness_sec: float = 3.0

    # Entry ladder: start at mid, walk toward ask in steps.
    ladder_start_frac: float = Field(0.50, description="0.5 = start at mid of natural/mid range.")
    ladder_step_frac: float = 0.15
    ladder_step_wait_sec: float = 5.0
    ladder_max_steps: int = 3
    entry_abandon_sec: float = 30.0


class ExitConfig(BaseModel):
    profit_target_pct: dict[int, float] = Field(
        default={93: 60.0, 95: 80.0, 97: 100.0},
        description="Score band -> profit target as % of debit paid.",
    )
    hard_stop_pct: float = Field(50.0, description="Exit if vertical value drops this % below debit.")
    time_stop: time = time(15, 15)
    force_flat: time = time(15, 45)
    structure_stop: bool = Field(True, description="Exit on close back through the opening-range midpoint.")


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
