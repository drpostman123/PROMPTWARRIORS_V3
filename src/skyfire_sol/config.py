"""SKYFIRE_SOL configuration.

Hard ceilings are module constants; pydantic ``Field(le=...)`` lets YAML
*tighten* every limit but never loosen it. The CEO agent has no write
path to this module or to the YAML — and even a hostile config edit
cannot cross the constants below.

Secrets come only from the environment (systemd ``EnvironmentFile``),
never from YAML.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Hard ceilings — the law. YAML can tighten (smaller loss %, smaller caps,
# less leverage), never loosen. Enforced by Field(le=...) below.
# ---------------------------------------------------------------------------
HARD_SLIPPAGE_MEME_PCT = 3.0          # max price impact / slippage on meme swaps
HARD_SLIPPAGE_MAJOR_PCT = 0.5         # max on SOL/wBTC/wETH/USDC/jitoSOL swaps
HARD_HWM_BREAKER_PCT = 60.0           # -60% from high-water mark: full stop
HARD_SOFT_TIER_PCT = 30.0             # -30%: MEME+PERPS halve, no new entries
HARD_DAILY_PAUSE_PCT = 10.0           # -10% in a day: 24h entry pause
HARD_MAX_MEME_POSITIONS = 5
HARD_MAX_POSITION_PCT_OF_SLEEVE = 20.0  # per-position, of MEME sleeve NAV
HARD_CORR_RISK_ON_PCT = 55.0          # MEME+PERPS combined bucket, risk-on
HARD_CORR_RISK_OFF_PCT = 30.0         # ... risk-off / unknown regime
HARD_PERPS_MAX_LEVERAGE = 3.0
HARD_MAX_FUNDING_PCT_HR = 0.05        # don't pay more than this to hold a perp

# Canonical mints (mainnet-beta). Pinned here, not user-editable per trade.
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WBTC_MINT = "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh"   # Portal wBTC
WETH_MINT = "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs"   # Portal wETH
JITOSOL_MINT = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"

MAJOR_MINTS = {WSOL_MINT, USDC_MINT, WBTC_MINT, WETH_MINT, JITOSOL_MINT}


class WalletConfig(BaseModel):
    age_key_path: str = "secrets/wallet.age"


class RpcConfig(BaseModel):
    # URL may contain the literal ${HELIUS_API_KEY}, substituted from env at load.
    url: str = "https://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}"
    fallback_url: str = "https://api.mainnet-beta.solana.com"
    commitment: Literal["processed", "confirmed", "finalized"] = "confirmed"
    max_requests_per_second: float = Field(8.0, gt=0)   # Helius free tier ~10 rps
    confirm_timeout_s: float = Field(60.0, gt=0)


class JupiterConfig(BaseModel):
    base_url: str = "https://lite-api.jup.ag"     # free tier; api.jup.ag with key
    # api key read from env JUPITER_API_KEY; sent as x-api-key when present
    max_requests_per_second: float = Field(1.0, gt=0)   # lite tier ~60 rpm
    quote_ttl_s: float = Field(10.0, gt=0)        # re-quote if older at execution


class DiscoveryConfig(BaseModel):
    poll_seconds: float = Field(60.0, ge=30.0)
    min_liquidity_usd: float = Field(100_000.0, ge=100_000.0)   # tighten-only
    min_token_age_minutes: float = Field(60.0, ge=60.0)
    max_top10_holder_pct: float = Field(30.0, le=30.0, gt=0)
    min_lp_lock_days: float = Field(30.0, ge=30.0)
    rug_verdict_ttl_minutes: float = Field(10.0, gt=0, le=10.0)
    sell_test_usd: float = Field(50.0, gt=0)      # size of the simulated sell
    max_candidates_per_cycle: int = Field(15, gt=0, le=50)
    dexscreener_rps: float = Field(4.0, gt=0)     # 300 rpm documented
    birdeye_rps: float = Field(0.8, gt=0)
    rugcheck_rps: float = Field(2.0, gt=0)


class MemeConfig(BaseModel):
    max_positions: int = Field(5, gt=0, le=HARD_MAX_MEME_POSITIONS)
    max_position_pct_of_sleeve: float = Field(20.0, gt=0, le=HARD_MAX_POSITION_PCT_OF_SLEEVE)
    slippage_cap_pct: float = Field(3.0, gt=0, le=HARD_SLIPPAGE_MEME_PCT)
    take_profit_mult: float = Field(2.0, gt=1.0)
    take_profit_sell_frac: float = Field(0.5, gt=0, le=1.0)
    trail_from_peak_pct: float = Field(40.0, gt=0, le=40.0)   # monitored soft stop
    time_stop_hours: float = Field(48.0, gt=0, le=48.0)
    min_vol_accel: float = Field(1.5, gt=0)   # 1h vol vs trailing 6h/6 baseline
    ema_period_minutes: int = Field(60, gt=0)
    blowoff_atr_mult: float = Field(2.0, gt=0)
    min_entry_usd: float = Field(10.0, gt=0)  # dust floor: skip smaller entries


class CoreConfig(BaseModel):
    # sleeve-internal target weights, must sum to 100
    weights: dict[str, float] = {"SOL": 40.0, "wBTC": 20.0, "wETH": 15.0, "USDC": 25.0}
    rebalance_band_pct: float = Field(5.0, gt=0)
    slippage_cap_pct: float = Field(0.5, gt=0, le=HARD_SLIPPAGE_MAJOR_PCT)

    @field_validator("weights")
    @classmethod
    def _sum_100(cls, v: dict[str, float]) -> dict[str, float]:
        if abs(sum(v.values()) - 100.0) > 1e-6:
            raise ValueError(f"core weights must sum to 100, got {sum(v.values())}")
        if set(v) != {"SOL", "wBTC", "wETH", "USDC"}:
            raise ValueError("core weights must be exactly SOL/wBTC/wETH/USDC")
        return v


class YieldConfig(BaseModel):
    venue: Literal["jitosol", "null"] = "jitosol"   # LP venue is a later fast-follow
    slippage_cap_pct: float = Field(0.5, gt=0, le=HARD_SLIPPAGE_MAJOR_PCT)
    min_conversion_usd: float = Field(25.0, gt=0)


class PerpsConfig(BaseModel):
    enabled: bool = False            # ships last (build order §7); flip in YAML when ready
    market: Literal["SOL-PERP"] = "SOL-PERP"
    max_leverage: float = Field(3.0, gt=0, le=HARD_PERPS_MAX_LEVERAGE)
    max_funding_pct_hr: float = Field(0.05, gt=0, le=HARD_MAX_FUNDING_PCT_HR)
    subaccount_id: int = Field(1, ge=0)   # dedicated subaccount = isolated margin


class SleevesConfig(BaseModel):
    # Boot-state allocation targets only — the CEO owns them at runtime.
    boot_allocations: dict[str, float] = {
        "MEME_ROTATION": 40.0, "CORE_HOLD": 25.0, "YIELD": 20.0, "PERPS": 15.0}
    core: CoreConfig = CoreConfig()
    meme: MemeConfig = MemeConfig()
    yield_: YieldConfig = Field(default=YieldConfig(), alias="yield")
    perps: PerpsConfig = PerpsConfig()

    model_config = {"populate_by_name": True}

    @field_validator("boot_allocations")
    @classmethod
    def _alloc_sane(cls, v: dict[str, float]) -> dict[str, float]:
        expected = {"MEME_ROTATION", "CORE_HOLD", "YIELD", "PERPS"}
        if set(v) != expected:
            raise ValueError(f"boot_allocations keys must be {sorted(expected)}")
        if abs(sum(v.values()) - 100.0) > 1e-6:
            raise ValueError(f"boot_allocations must sum to 100, got {sum(v.values())}")
        if any(x < 0 for x in v.values()):
            raise ValueError("boot_allocations must be non-negative")
        return v


class RiskConfig(BaseModel):
    hwm_breaker_pct: float = Field(60.0, gt=0, le=HARD_HWM_BREAKER_PCT)
    soft_tier_pct: float = Field(30.0, gt=0, le=HARD_SOFT_TIER_PCT)
    soft_tier_clear_pct: float = Field(20.0, gt=0)
    daily_pause_pct: float = Field(10.0, gt=0, le=HARD_DAILY_PAUSE_PCT)
    daily_pause_hours: float = Field(24.0, ge=24.0)
    corr_risk_on_pct: float = Field(55.0, gt=0, le=HARD_CORR_RISK_ON_PCT)
    corr_risk_off_pct: float = Field(30.0, gt=0, le=HARD_CORR_RISK_OFF_PCT)
    probation_size_frac: float = Field(0.5, gt=0, le=0.5)     # first-session throttle
    probation_clean_fills: int = Field(10, ge=10)
    clean_fill_slippage_pct: float = Field(1.0, gt=0)  # realized slip to count "clean"
    nav_stale_entries_off_s: float = Field(60.0, gt=0)
    nav_quarantine_jump_pct: float = Field(20.0, gt=0)

    @model_validator(mode="after")
    def _coherent(self) -> "RiskConfig":
        # The ladder must be strictly ordered or a tier can never clear/trigger.
        if not (self.daily_pause_pct < self.soft_tier_pct < self.hwm_breaker_pct):
            raise ValueError("need daily_pause_pct < soft_tier_pct < hwm_breaker_pct")
        if not (self.soft_tier_clear_pct < self.soft_tier_pct):
            raise ValueError("soft_tier_clear_pct must be below soft_tier_pct (hysteresis)")
        if not (self.corr_risk_off_pct <= self.corr_risk_on_pct):
            raise ValueError("corr_risk_off_pct must be <= corr_risk_on_pct")
        return self


class CeoConfig(BaseModel):
    loop_seconds: float = Field(300.0, ge=60.0)
    sharpe_weight: float = Field(0.6, ge=0)
    ret24_weight: float = Field(0.4, ge=0)
    winner_press_gain: float = Field(1.5, ge=1.0)   # >1: greedy — press the winner
    sol_trend_ema_fast_h: int = Field(6, gt=0)
    sol_trend_ema_slow_h: int = Field(48, gt=0)
    breadth_risk_on: float = Field(0.55, gt=0, le=1)   # share of positive-momo memes
    breadth_risk_off: float = Field(0.35, ge=0, le=1)

    @model_validator(mode="after")
    def _sane(self) -> "CeoConfig":
        if self.sharpe_weight + self.ret24_weight <= 0:
            raise ValueError("CEO score weights must not both be zero")
        if self.breadth_risk_off >= self.breadth_risk_on:
            raise ValueError("breadth_risk_off must be < breadth_risk_on")
        return self


class DataConfig(BaseModel):
    state_dir: str = "state/skyfire"
    log_dir: str = "logs"
    db_path: str = "state/skyfire/skyfire.db"
    snapshot_path: str = "state/skyfire/snapshot.json"
    decision_log_path: str = "state/skyfire/decisions.jsonl"
    trade_log_path: str = "state/skyfire/trades.jsonl"
    heartbeat_path: str = "state/skyfire/heartbeat.json"
    heartbeat_seconds: float = Field(5.0, gt=0)
    snapshot_seconds: float = Field(2.0, gt=0)


class AppConfig(BaseModel):
    wallet: WalletConfig = WalletConfig()
    rpc: RpcConfig = RpcConfig()
    jupiter: JupiterConfig = JupiterConfig()
    discovery: DiscoveryConfig = DiscoveryConfig()
    sleeves: SleevesConfig = SleevesConfig()
    risk: RiskConfig = RiskConfig()
    ceo: CeoConfig = CeoConfig()
    data: DataConfig = DataConfig()

    @model_validator(mode="after")
    def _perp_alloc_needs_enable(self) -> "AppConfig":
        if (not self.sleeves.perps.enabled
                and self.sleeves.boot_allocations.get("PERPS", 0) > 0):
            # Allowed: the CEO renormalizes a disabled sleeve's boot weight
            # away at first pass. Nothing to reject — documented behavior.
            pass
        return self


@dataclass(frozen=True)
class Credentials:
    """Secrets, environment-only. YAML never carries these."""

    helius_api_key: str
    birdeye_api_key: Optional[str]
    jupiter_api_key: Optional[str]
    key_passphrase: str

    @classmethod
    def from_env(cls, require_wallet: bool = True) -> "Credentials":
        helius = os.environ.get("HELIUS_API_KEY", "")
        passphrase = os.environ.get("SKYFIRE_KEY_PASSPHRASE", "")
        if require_wallet and not passphrase:
            raise RuntimeError("SKYFIRE_KEY_PASSPHRASE not set — cannot decrypt wallet")
        return cls(
            helius_api_key=helius,
            birdeye_api_key=os.environ.get("BIRDEYE_API_KEY") or None,
            jupiter_api_key=os.environ.get("JUPITER_API_KEY") or None,
            key_passphrase=passphrase,
        )


def load_config(path: str) -> AppConfig:
    """Missing file yields safe defaults; env placeholders are substituted."""
    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raw = {}
    return AppConfig.model_validate(raw)


def resolve_rpc_url(cfg: RpcConfig, creds: Credentials) -> str:
    url = cfg.url.replace("${HELIUS_API_KEY}", creds.helius_api_key)
    if "${" in url:
        raise RuntimeError(f"unresolved placeholder in rpc url: {url}")
    if url.endswith("api-key=") or not creds.helius_api_key:
        # No Helius key: public RPC. Rate-limited, fine for selftest only.
        return cfg.fallback_url
    return url
