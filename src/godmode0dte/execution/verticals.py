"""Debit vertical construction from a 0DTE option chain.

Rules:
  - Long leg: delta in [long_delta_min, long_delta_max] (slightly ITM/ATM).
  - Short leg: `width` strikes/points further OTM.
  - Debit must be within [min, max] % of width — below the floor the
    quoted price is fantasy; above the cap reward:risk is too poor.
  - Both legs must pass liquidity gates (spread, OI, quote freshness).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from godmode0dte.config import ExecutionConfig
from godmode0dte.models import Direction, Quote, VerticalSpec
from godmode0dte.monitoring.logging import get_logger

log = get_logger("verticals")


@dataclass(frozen=True)
class ChainOption:
    """Normalized option row (from tastytrade chain + greeks stream)."""

    symbol: str                 # OCC symbol
    streamer_symbol: str        # DXFeed symbol
    strike: float
    is_call: bool
    delta: Optional[float]
    open_interest: int


@dataclass(frozen=True)
class BuildResult:
    vertical: Optional[VerticalSpec]
    reason: str
    long_quote: Optional[Quote] = None
    short_quote: Optional[Quote] = None


def leg_liquidity_ok(q: Optional[Quote], cfg: ExecutionConfig, scale: float = 1.0) -> tuple[bool, str]:
    if q is None or q.bid <= 0 or q.ask <= 0:
        return False, "missing/one-sided quote"
    if q.spread > cfg.max_leg_spread_abs * scale:
        return False, f"abs spread {q.spread:.2f} > {cfg.max_leg_spread_abs * scale:.2f}"
    if q.mid > 0 and 100.0 * q.spread / q.mid > cfg.max_leg_spread_pct_of_mid:
        return False, f"spread {100.0 * q.spread / q.mid:.1f}% of mid > {cfg.max_leg_spread_pct_of_mid}%"
    return True, "ok"


def build_vertical(
    direction: Direction,
    underlying: str,
    chain: list[ChainOption],
    quotes: dict[str, Quote],
    cfg: ExecutionConfig,
    today: date,
) -> BuildResult:
    """Pick strikes and price a debit vertical; enforce debit/width bounds."""
    is_call = direction is Direction.LONG
    width = float(cfg.width_strikes_spy if underlying == "SPY" else cfg.width_points_spx)
    scale = 1.0 if underlying == "SPY" else 10.0

    candidates = [
        o for o in chain
        if o.is_call == is_call
        and o.delta is not None
        and cfg.long_delta_min <= abs(o.delta) <= cfg.long_delta_max
        and o.open_interest >= cfg.min_open_interest
    ]
    if not candidates:
        return BuildResult(None, "no long-leg candidate in delta band with sufficient OI")
    # Closest to the middle of the delta band.
    mid_delta = (cfg.long_delta_min + cfg.long_delta_max) / 2
    candidates.sort(key=lambda o: abs(abs(o.delta) - mid_delta))

    for long_opt in candidates[:4]:
        short_strike = long_opt.strike + width if is_call else long_opt.strike - width
        short_opt = next(
            (o for o in chain if o.is_call == is_call and abs(o.strike - short_strike) < 1e-6),
            None,
        )
        if short_opt is None or short_opt.open_interest < cfg.min_open_interest:
            continue
        lq = quotes.get(long_opt.streamer_symbol)
        sq = quotes.get(short_opt.streamer_symbol)
        ok_l, why_l = leg_liquidity_ok(lq, cfg, scale)
        ok_s, why_s = leg_liquidity_ok(sq, cfg, scale)
        if not ok_l or not ok_s:
            log.info("leg_liquidity_reject", long=why_l, short=why_s, strike=long_opt.strike)
            continue
        # Combined vertical spread gate (spec §6.3): the cost of crossing the
        # combo, not just each leg.
        combo_spread = (lq.ask - sq.bid) - (lq.bid - sq.ask)
        combo_max = cfg.max_combo_spread_spy if underlying == "SPY" else cfg.max_combo_spread_spx
        if combo_spread > combo_max:
            log.info("combo_spread_reject", combo_spread=round(combo_spread, 2),
                     limit=combo_max, strike=long_opt.strike)
            continue
        debit = round(lq.mid - sq.mid, 2)
        debit_pct = debit / width
        if debit_pct < cfg.min_debit_pct_of_width:
            return BuildResult(None, f"debit {debit_pct:.0%} of width below floor — quote likely fantasy",
                               lq, sq)
        if debit_pct > cfg.max_debit_pct_of_width:
            continue  # too expensive here; try the next long-leg candidate
        vertical = VerticalSpec(
            underlying=underlying,
            direction=direction,
            expiration=today.isoformat(),
            long_strike=long_opt.strike,
            short_strike=short_opt.strike,
            width=width,
            debit=debit,
            contracts=0,                       # governor sets the size
            long_symbol=long_opt.symbol,
            short_symbol=short_opt.symbol,
        )
        return BuildResult(vertical, "ok", lq, sq)
    return BuildResult(None, "no strike pair satisfied debit/width and liquidity gates")
