"""Meme exit engine — priority-ordered pure function (the V2ExitEngine
shape from godmode0dte_v2). evaluate() is a function of
(position, mark, now, locked) and returns at most one ExitOrder.

Priority:
  P0 breaker flatten (locked)          -> sell 100%, urgent
  P1 take-profit at 2x                 -> sell 50% once, normal (house money)
  P2 trailing stop -40% from peak      -> sell 100%, urgent (soft stop:
     memes gap; there is no on-chain stop-loss — this is the monitored exit)
  P3 time stop 48h flat/negative       -> sell 100%, normal
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from skyfire_sol.config import MemeConfig
from skyfire_sol.models import ExitOrder, Position, Urgency


def evaluate(pos: Position, mark_usd: float, now: datetime,
             locked: bool, cfg: MemeConfig) -> Optional[ExitOrder]:
    if locked:
        return ExitOrder(pos.position_id, 1.0, "breaker_flatten", Urgency.URGENT)

    if mark_usd <= 0 or pos.entry_price_usd <= 0:
        return None

    ratio = mark_usd / pos.entry_price_usd
    peak = max(pos.peak_price_usd, mark_usd)

    if not pos.scaled_out and ratio >= cfg.take_profit_mult:
        return ExitOrder(pos.position_id, cfg.take_profit_sell_frac,
                         "take_profit_2x", Urgency.NORMAL)

    if peak > 0 and (peak - mark_usd) / peak * 100.0 >= cfg.trail_from_peak_pct:
        return ExitOrder(pos.position_id, 1.0, "trail_stop", Urgency.URGENT)

    held_h = (now - pos.entry_ts).total_seconds() / 3600.0
    if held_h >= cfg.time_stop_hours and ratio <= 1.0:
        return ExitOrder(pos.position_id, 1.0, "time_stop", Urgency.NORMAL)

    return None
