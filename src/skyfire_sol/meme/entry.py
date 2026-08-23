"""Meme entry signal — pure momentum scoring over TokenFacts.

Components (spec §4.3):
- 1h volume acceleration vs the trailing 6h baseline (6h/6 per-hour avg)
- net holder growth positive
- price above short EMA (approximated by positive 1h price change when no
  candle history exists yet — the scanner upgrades this once it has marks)
- blowoff veto: a single candle > 2x ATR disqualifies (chasing tops)

Score candidates, enter top-N only. Pure functions — trivially testable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from skyfire_sol.config import MemeConfig
from skyfire_sol.models import EntrySignal, TokenFacts


def vol_acceleration(f: TokenFacts) -> Optional[float]:
    if f.volume_1h_usd is None or f.volume_6h_usd is None or f.volume_6h_usd <= 0:
        return None
    baseline_per_h = f.volume_6h_usd / 6.0
    if baseline_per_h <= 0:
        return None
    return f.volume_1h_usd / baseline_per_h


def holder_growth(f: TokenFacts) -> Optional[float]:
    if f.holder_count is None or f.holder_count_prev is None or f.holder_count_prev <= 0:
        return None
    return (f.holder_count - f.holder_count_prev) / f.holder_count_prev


def is_blowoff(price_change_1h_pct: Optional[float], atr_pct: Optional[float],
               atr_mult: float) -> bool:
    """True when the last hour's move exceeds atr_mult x ATR — do not chase.
    Unknown ATR (no history yet): treat a >50% single-hour candle as blowoff."""
    if price_change_1h_pct is None:
        return False
    if atr_pct is None or atr_pct <= 0:
        return abs(price_change_1h_pct) > 50.0
    return abs(price_change_1h_pct) > atr_mult * atr_pct


def score(f: TokenFacts, cfg: MemeConfig, ema_price_usd: Optional[float] = None,
          atr_pct: Optional[float] = None, policy=None) -> Optional[EntrySignal]:
    """None = not a candidate (missing data or a veto). Higher score = better.

    ``policy`` (learning.policy.Policy) overrides the tunable alpha
    knobs — threshold and score weights — inside its hard bounds; the
    vetoes (holder shrink, below-EMA, blowoff) are not tunable."""
    if f.price_usd is None:
        return None
    min_accel = policy.min_vol_accel if policy is not None else cfg.min_vol_accel
    accel = vol_acceleration(f)
    if accel is None or accel < min_accel:
        return None
    growth = holder_growth(f)
    if growth is not None and growth <= 0:
        return None                      # net holder growth must be positive
    growth = growth if growth is not None else 0.0   # unknown: neutral, not fatal
    if ema_price_usd is not None:
        above_ema = f.price_usd > ema_price_usd
    else:
        # No mark history yet: proxy trend with 1h price change sign.
        above_ema = (f.price_change_1h_pct or 0.0) > 0.0
    if not above_ema:
        return None
    blow = is_blowoff(f.price_change_1h_pct, atr_pct, cfg.blowoff_atr_mult)
    if blow:
        return None
    liq_bonus = min((f.liquidity_usd or 0.0) / 1_000_000.0, 1.0)
    w_accel = policy.w_accel if policy is not None else 10.0
    w_growth = policy.w_growth if policy is not None else 100.0
    w_liq = policy.w_liq if policy is not None else 5.0
    s = accel * w_accel + growth * w_growth + liq_bonus * w_liq
    return EntrySignal(
        mint=f.mint, symbol=f.symbol, score=round(s, 3), vol_accel=accel,
        holder_growth=growth, above_ema=above_ema, blowoff=blow,
        price_usd=f.price_usd, ts=datetime.now(timezone.utc))


def pick_top(signals: list[EntrySignal], open_count: int, cfg: MemeConfig) -> list[EntrySignal]:
    slots = max(0, cfg.max_positions - open_count)
    return sorted(signals, key=lambda s: s.score, reverse=True)[:slots]
