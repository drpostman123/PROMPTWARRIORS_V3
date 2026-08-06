"""v2 config: high-conviction aggressive style.

Philosophy in numbers: few trades, heavy size, fast exits into strength,
one hard survival rule. Every threshold of the perfect-setup detector is
config-driven so the operator's exact discretionary conditions drop in as
YAML without code changes.

The coherence law (validated here, enforced again at runtime): per-trade
risk <= daily loss limit. The daily breaker is the ONE survival lock this
style keeps; a single trade that can out-lose the day limit would make it
theater. Ceilings: per-trade 33%, daily 40% — aggressive is the point,
but the account must be able to survive three perfect-looking setups that
were wrong.
"""

from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

from godmode0dte.config import DataConfig, FeeModelConfig

V2_HARD_MAX_TRADE_RISK_PCT = 33.0
V2_HARD_MAX_DAILY_LOSS_PCT = 40.0


class ConvictionCondition(BaseModel):
    """One named condition of the perfect-setup definition.

    metric: what to measure     op: how to compare     value: the bar
    The detector computes the metric snapshot; a setup is PERFECT only when
    EVERY enabled condition passes. Operator's exact discretionary rules go
    here as YAML rows — no code edit needed.
    """

    metric: Literal[
        "rsi_2m",              # RSI(14) on 2m bars — the extreme itself
        "rsi_5m",
        "vwap_stretch_atr",    # (price - session VWAP) / ATR5m, signed
        "day_move_pct",        # % move from today's open, signed
        "book_imbalance",      # L1 EWMA imbalance, signed
        "rel_volume",          # volume climax vs baseline
        "vix",
        "minutes_since_open",
    ]
    op: Literal["<=", ">=", "abs>=", "abs<="]
    value: float
    enabled: bool = True


class ConvictionConfig(BaseModel):
    """The perfect-setup definition. Defaults describe a textbook
    exhaustion extreme; the operator's own conditions REPLACE these."""

    # Fade an overbought extreme with puts / oversold with calls.
    long_put_conditions: list[ConvictionCondition] = Field(default=[
        ConvictionCondition(metric="rsi_2m", op=">=", value=85.0),
        ConvictionCondition(metric="vwap_stretch_atr", op=">=", value=2.5),
        ConvictionCondition(metric="rel_volume", op=">=", value=2.0),
        ConvictionCondition(metric="book_imbalance", op="<=", value=-0.20),
    ])
    long_call_conditions: list[ConvictionCondition] = Field(default=[
        ConvictionCondition(metric="rsi_2m", op="<=", value=15.0),
        ConvictionCondition(metric="vwap_stretch_atr", op="<=", value=-2.5),
        ConvictionCondition(metric="rel_volume", op=">=", value=2.0),
        ConvictionCondition(metric="book_imbalance", op=">=", value=0.20),
    ])
    min_conditions_pct: float = Field(100.0, ge=50.0, le=100.0,
                                      description="100 = ALL conditions (perfect only).")
    cooldown_min: float = Field(20.0, ge=0, description="After a fire, silence per side.")


class V2SizingConfig(BaseModel):
    conviction_risk_pct: float = Field(20.0, gt=0, le=V2_HARD_MAX_TRADE_RISK_PCT,
                                       description="Premium at risk per PERFECT setup, % of equity.")
    daily_loss_limit_pct: float = Field(25.0, gt=0, le=V2_HARD_MAX_DAILY_LOSS_PCT)
    max_trades_per_day: int = Field(2, ge=1, le=4,
                                    description="Heavy hits are rare by definition.")
    min_equity: float = Field(1_000.0, ge=0)
    lockout_file: str = "state/v2_lockout.json"

    @model_validator(mode="after")
    def _coherent(self) -> "V2SizingConfig":
        if self.conviction_risk_pct > self.daily_loss_limit_pct:
            raise ValueError(
                "conviction_risk_pct must be <= daily_loss_limit_pct: the daily "
                "breaker is the one survival rule this style keeps, and a single "
                "trade must not be able to blow through it.")
        return self


class V2ExitConfig(BaseModel):
    """Sell into strength, fast. Tranches are fractions of the position
    sold at premium multiples; the runner trails."""

    scale_outs: list[tuple[float, float]] = Field(
        default=[(2.0, 0.50), (4.0, 0.25)],
        description="(premium multiple, fraction of original size to sell).")
    runner_trail_pct: float = Field(40.0, gt=0, le=90,
                                    description="Trail the remainder this % below its high-water mark.")
    hard_stop_pct: float = Field(50.0, gt=0, le=100,
                                 description="Exit all if premium drops this % from entry.")
    max_hold_min: float = Field(60.0, gt=0, description="An extreme resolves fast or was wrong.")
    force_flat: time = time(15, 30)


class V2InstrumentConfig(BaseModel):
    underlying: Literal["SPY"] = "SPY"     # single-leg 0DTE; SPX premiums don't fit this equity
    target_delta: float = Field(0.40, gt=0.05, lt=0.95,
                                description="|delta| of the long option to buy.")
    max_leg_spread_pct_of_mid: float = 10.0
    min_open_interest: int = 500
    quote_staleness_sec: float = 1.5
    entry_ladder_max_steps: int = 3
    entry_ladder_wait_sec: float = 3.0


class V2Config(BaseModel):
    paper_mode: bool = True                # flip deliberately; same env interlock as v1
    account_index: int = 0
    timezone: str = "America/New_York"
    mode: Literal["present", "auto"] = "present"
    # present: surface the setup (log + snapshot + suggested size) and let the
    #          human pull the trigger — the discretionary style, assisted.
    # auto:    the system fires the entry itself when a perfect setup passes.
    conviction: ConvictionConfig = ConvictionConfig()
    sizing: V2SizingConfig = V2SizingConfig()
    exits: V2ExitConfig = V2ExitConfig()
    instrument: V2InstrumentConfig = V2InstrumentConfig()
    fees: FeeModelConfig = FeeModelConfig()
    data: DataConfig = DataConfig(snapshot_path="state/v2_snapshot.json",
                                  trade_log_path="state/v2_trades.jsonl",
                                  decision_log_path="state/v2_decisions.jsonl")
    shadow_all_signals: bool = Field(True, description="Paper-follow every surfaced setup.")

    @model_validator(mode="after")
    def _live_requires_confirmation(self) -> "V2Config":
        if not self.paper_mode and os.environ.get("GODMODE_CONFIRM_LIVE") != "YES":
            raise ValueError("live v2 requires GODMODE_CONFIRM_LIVE=YES")
        return self


def load_v2_config(path: str | Path = "config/config.v2.yaml") -> V2Config:
    p = Path(path)
    raw = yaml.safe_load(p.read_text()) if p.exists() else {}
    return V2Config.model_validate(raw or {})
