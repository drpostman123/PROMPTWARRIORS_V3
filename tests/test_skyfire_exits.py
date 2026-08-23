"""Meme exit engine: priority matrix as pure-function table tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from skyfire_sol.config import MemeConfig
from skyfire_sol.meme import exits
from skyfire_sol.models import Position, SleeveId, Urgency

CFG = MemeConfig()
NOW = datetime.now(timezone.utc)


def pos(**over) -> Position:
    base = dict(
        position_id="p1", sleeve=SleeveId.MEME_ROTATION, mint="M", symbol="T",
        quote_mint="USDC", qty_raw=10**9, decimals=6,
        entry_price_usd=1.0, entry_ts=NOW - timedelta(hours=1),
        peak_price_usd=1.0)
    base.update(over)
    return Position(**base)


def test_no_exit_in_normal_range():
    assert exits.evaluate(pos(), 1.5, NOW, locked=False, cfg=CFG) is None


def test_breaker_flatten_beats_everything():
    o = exits.evaluate(pos(peak_price_usd=10.0), 2.5, NOW, locked=True, cfg=CFG)
    assert o.reason == "breaker_flatten" and o.frac == 1.0
    assert o.urgency is Urgency.URGENT


def test_take_profit_2x_sells_half_once():
    o = exits.evaluate(pos(), 2.0, NOW, locked=False, cfg=CFG)
    assert o.reason == "take_profit_2x" and o.frac == 0.5
    # after scale-out, the same mark does not re-trigger the take
    o2 = exits.evaluate(pos(scaled_out=True, peak_price_usd=2.0), 2.0, NOW,
                        locked=False, cfg=CFG)
    assert o2 is None


def test_trailing_stop_40pct_from_peak():
    p = pos(scaled_out=True, peak_price_usd=3.0)
    assert exits.evaluate(p, 1.9, NOW, locked=False, cfg=CFG) is None   # -36.7%
    o = exits.evaluate(p, 1.8, NOW, locked=False, cfg=CFG)              # -40%
    assert o.reason == "trail_stop" and o.frac == 1.0
    assert o.urgency is Urgency.URGENT


def test_trail_uses_live_peak_not_stale_field():
    # mark above recorded peak: no trail even though field lags
    p = pos(peak_price_usd=1.0)
    assert exits.evaluate(p, 1.9, NOW, locked=False, cfg=CFG) is None


def test_time_stop_only_when_flat_or_negative():
    old = pos(entry_ts=NOW - timedelta(hours=49))
    o = exits.evaluate(old, 0.9, NOW, locked=False, cfg=CFG)
    assert o.reason == "time_stop" and o.frac == 1.0
    # a winner is never time-stopped
    winner = pos(entry_ts=NOW - timedelta(hours=49), peak_price_usd=1.4)
    assert exits.evaluate(winner, 1.3, NOW, locked=False, cfg=CFG) is None


def test_take_profit_outranks_time_stop():
    p = pos(entry_ts=NOW - timedelta(hours=50))
    o = exits.evaluate(p, 2.1, NOW, locked=False, cfg=CFG)
    assert o.reason == "take_profit_2x"


def test_zero_prices_are_inert():
    assert exits.evaluate(pos(entry_price_usd=0.0), 1.0, NOW, False, CFG) is None
    assert exits.evaluate(pos(), 0.0, NOW, False, CFG) is None
