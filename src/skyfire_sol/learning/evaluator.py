"""Pure reward evaluation over the grounded data.

Entry policy: replay every resolved phantom candidate (rug-clean stages
only) under a candidate parameter set — who would have been selected,
and what did those tokens actually do at the 6h horizon? Rewards are
winsorized so one 100x outlier cannot hijack the policy, and dead
tokens count as -100% (the honest cost of illiquidity).

Press gain: replay every historical risk-on allocation — given the
winner the CEO pressed and what each sleeve actually returned over the
following 24h, what concentration gain would have maximized portfolio
return?

Pure functions, no I/O — trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Optional

REWARD_FLOOR_PCT = -100.0
REWARD_CAP_PCT = 300.0
MIN_SELECTED = 30           # a reward over fewer picks is noise, not signal


@dataclass(frozen=True)
class PhantomSample:
    ts: str
    vol_accel: Optional[float]
    holder_growth: Optional[float]
    liquidity_usd: float
    price_change_1h_pct: float
    fwd_pct: float              # resolved forward return (dead -> -100)


def sample_from_row(features: dict, fwd: Optional[float],
                    dead: int, ts: str) -> Optional[PhantomSample]:
    if fwd is None and not dead:
        return None
    v1, v6 = features.get("volume_1h_usd"), features.get("volume_6h_usd")
    accel = None
    if v1 is not None and v6 and v6 > 0:
        accel = v1 / (v6 / 6.0)
    growth = None
    h, hp = features.get("holder_count"), features.get("holder_count_prev")
    if h is not None and hp:
        growth = (h - hp) / hp
    fwd_pct = REWARD_FLOOR_PCT if dead else float(fwd)
    return PhantomSample(
        ts=ts, vol_accel=accel, holder_growth=growth,
        liquidity_usd=float(features.get("liquidity_usd") or 0.0),
        price_change_1h_pct=float(features.get("price_change_1h_pct") or 0.0),
        fwd_pct=min(max(fwd_pct, REWARD_FLOOR_PCT), REWARD_CAP_PCT))


def _passes(s: PhantomSample, min_vol_accel: float) -> bool:
    """Mirror of meme.entry.score's pass gates over replayable features."""
    if s.vol_accel is None or s.vol_accel < min_vol_accel:
        return False
    if s.holder_growth is not None and s.holder_growth <= 0:
        return False
    if s.price_change_1h_pct <= 0:
        return False
    return True


def _score(s: PhantomSample, w_accel: float, w_growth: float, w_liq: float) -> float:
    growth = s.holder_growth if s.holder_growth is not None else 0.0
    liq_bonus = min(s.liquidity_usd / 1_000_000.0, 1.0)
    return (s.vol_accel or 0.0) * w_accel + growth * w_growth + liq_bonus * w_liq


def entry_reward(samples: list[PhantomSample], min_vol_accel: float,
                 w_accel: float, w_growth: float, w_liq: float,
                 top_n: int = 5) -> Optional[float]:
    """Replay live selection mechanics: candidates arrive in hourly
    batches, and each batch takes at most top-N passing candidates by
    score — exactly the slot scarcity the rotation sleeve faces. Reward
    = mean forward return of everything that would have been taken.
    Both the gate (who passes) and the ranking (who wins scarce slots)
    move the reward. None = not enough evidence."""
    batches: dict[str, list[PhantomSample]] = {}
    for s in samples:
        batches.setdefault(s.ts[:13], []).append(s)     # hour bucket
    taken: list[PhantomSample] = []
    for batch in batches.values():
        passing = [s for s in batch if _passes(s, min_vol_accel)]
        passing.sort(key=lambda s: _score(s, w_accel, w_growth, w_liq),
                     reverse=True)
        taken.extend(passing[:top_n])
    if len(taken) < MIN_SELECTED:
        return None
    return mean(s.fwd_pct for s in taken)


@dataclass(frozen=True)
class PressEvent:
    ts: str
    winner: str
    boot: dict[str, float]              # boot allocation percentages
    next24_returns: dict[str, float]    # sleeve -> realized 24h return, pct


def press_reward(events: list[PressEvent], gain: float) -> Optional[float]:
    """Mean portfolio 24h return had every risk-on decision pressed the
    winner by ``gain`` (boot weights, winner multiplied, renormalized)."""
    if not events:
        return None
    outcomes = []
    for e in events:
        w = dict(e.boot)
        w[e.winner] = w.get(e.winner, 0.0) * gain
        total = sum(w.values()) or 1.0
        outcomes.append(sum(
            w[s] / total * e.next24_returns.get(s, 0.0) for s in w))
    return mean(outcomes)
