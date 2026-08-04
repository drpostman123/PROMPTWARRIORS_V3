"""Regression tests for the round-2 audit fixes (B-series) and upgrades (U-series)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from godmode0dte.config import RiskConfig, SignalConfig
from godmode0dte.execution.broker import snap_tick
from godmode0dte.execution.exits import ExitEngine
from godmode0dte.features.opening_range import OpeningRangeTracker
from godmode0dte.models import Bar, Direction, Position, Regime
from godmode0dte.regime.rules import RuleBasedRegime
from godmode0dte.risk.circuit_breaker import BreakerState, CircuitBreaker
from godmode0dte.risk.governor import ApprovedTrade
from tests.conftest import make_intent
from tests.test_exit_engine import NOW, open_position

UTC = timezone.utc


def bar(h: int, m: int, o: float, hi: float, lo: float, c: float, vol: float = 1000) -> Bar:
    # 13:30 UTC == 09:30 ET (EDT)
    return Bar(ts=datetime(2026, 8, 4, h, m, tzinfo=UTC), open=o, high=hi, low=lo,
               close=c, volume=vol)


def or_tracker_with_range() -> OpeningRangeTracker:
    t = OpeningRangeTracker(SignalConfig())
    for m in range(30, 35):
        t.add_bar(bar(13, m, 560.0, 561.0, 559.0, 560.5))
    t.add_bar(bar(13, 36, 560.5, 560.8, 560.2, 560.6))     # completes the OR
    return t


# ---- U4e: OR volume dedupe -------------------------------------------------

def test_or_add_bar_is_idempotent():
    t = OpeningRangeTracker(SignalConfig())
    b = bar(13, 31, 560, 561, 559, 560.5, vol=5000)
    for _ in range(180):                                    # runtime re-feeds every tick
        t.add_bar(b)
    assert t.range.volume == 5000


# ---- U4a: chase gate -------------------------------------------------------

def test_chase_gate_refuses_extended_breakout():
    t = or_tracker_with_range()                             # OR: 559-561, width 2
    chase = bar(13, 50, 561.5, 562.6, 561.4, 562.5)         # 1.5 past edge = 75% of width
    direction, ev = t.classify_breakout(chase, rel_volume=3.0)
    assert direction is None and ev["chase"] > 0.5
    clean = bar(13, 50, 561.0, 561.6, 560.9, 561.55)        # ~0.35 past edge + buffer
    direction, ev = t.classify_breakout(clean, rel_volume=3.0)
    assert direction is Direction.LONG


# ---- U4b: breakout freshness latch -----------------------------------------

def test_breakout_age_measured_from_first_confirm():
    t = or_tracker_with_range()
    first = bar(13, 50, 561.0, 561.6, 560.9, 561.55)
    assert t.classify_breakout(first, rel_volume=3.0)[0] is Direction.LONG
    later = first.ts + timedelta(minutes=20)
    assert t.breakout_age_min(Direction.LONG, later) == pytest.approx(20.0)


# ---- B6: baseline persistence ----------------------------------------------

def test_daily_baseline_survives_restart(tmp_path):
    cfg = RiskConfig(lockout_file=str(tmp_path / "lockout.json"))
    b1 = CircuitBreaker(cfg, date(2026, 8, 4))
    b1.set_starting_equity(100_000)
    b1.check(95_000)                                        # -5%: armed
    # Restart same day: baseline must restore, NOT re-anchor at 95k.
    b2 = CircuitBreaker(cfg, date(2026, 8, 4))
    assert b2.starting_equity == 100_000
    assert b2.check(93_999) is BreakerState.TRIPPED          # -6.001% vs the ORIGINAL baseline
    # Next day: fresh baseline.
    b3 = CircuitBreaker(cfg, date(2026, 8, 5))
    assert b3.starting_equity is None


# ---- B7: zero marks fire; frozen marks don't count -------------------------

def test_hard_stop_fires_on_zero_mark(cfg, governor):
    pos = open_position(governor, cfg, debit=1.0, mark=0.0)
    engine = ExitEngine(cfg.exits, governor)
    assert engine.evaluate(NOW, 561.0) == []                 # armed
    assert [d.reason for d in engine.evaluate(NOW, 561.0)] == ["hard_stop"]


def test_frozen_mark_does_not_confirm_stop_and_escalates(cfg, governor):
    pos = open_position(governor, cfg, debit=1.0, mark=0.40)
    stale = NOW - timedelta(seconds=60)
    governor.update_position(Position(**{**pos.__dict__, "mark_ts": stale}))
    engine = ExitEngine(cfg.exits, governor, mark_staleness_sec=15.0)
    d = engine.evaluate(NOW, 561.0)
    assert d == [] or all(x.reason != "hard_stop" for x in d)
    very_stale = NOW - timedelta(seconds=200)                # > 10x staleness bound
    governor.update_position(Position(**{**pos.__dict__, "mark_ts": very_stale}))
    reasons = [x.reason for x in engine.evaluate(NOW, 561.0)]
    assert reasons == ["stale_mark"]


# ---- U1: sizing at the worst permitted fill --------------------------------

def test_sizing_uses_cap_price_not_mid(cfg, governor):
    from tests.test_risk_governor import entry_window_now
    entry_window_now(cfg)
    cfg.risk.sizing_ladder = {93: 1.0}                       # Phase C worst case
    r = governor.evaluate(make_intent(score=99.0, debit=1.0, contracts=1000))
    assert isinstance(r, ApprovedTrade)
    # cap = min(1.10, 0.45*2.0) = 0.90; risk accounted at cap, and even a
    # worst-case fill at cap stays within 4%.
    assert r.cap_price == pytest.approx(0.90)
    assert r.risk_dollars == pytest.approx(r.vertical.contracts * 0.90 * 100)
    assert r.vertical.contracts * r.cap_price * 100 <= 100_000 * 0.04 + 1e-6


# ---- U3: regime rules ------------------------------------------------------

def _trend_bars(n: int, step: float) -> list[Bar]:
    out, px = [], 560.0
    for i in range(n):
        o = px
        px += step
        out.append(Bar(ts=datetime(2026, 8, 4, 13, 30, tzinfo=UTC) + timedelta(minutes=5 * i),
                       open=o, high=max(o, px) + 0.1, low=min(o, px) - 0.1, close=px,
                       volume=1000))
    return out


def test_regime_unknown_until_four_bars_then_confident():
    model = RuleBasedRegime()
    assert model.classify(_trend_bars(3, 0.5), vix=17.0).regime is Regime.UNKNOWN
    state = model.classify(_trend_bars(12, 0.5), vix=17.0)
    assert state.regime is Regime.TREND_UP
    assert state.confidence == pytest.approx(0.85)


def test_regime_range_when_flat():
    state = RuleBasedRegime().classify(_trend_bars(12, 0.0001), vix=17.0)
    assert state.regime is Regime.RANGE


# ---- U5b: tick snapping ----------------------------------------------------

def test_snap_tick_directions():
    assert snap_tick(1.234, "SPY", round_up=False) == 1.23   # entries: never pay up
    assert snap_tick(1.231, "SPY", round_up=True) == 1.24    # exit credits: never give away
    assert snap_tick(2.47, "SPX", round_up=False) == 2.45    # SPX 0.05 tick under $3
    assert snap_tick(4.32, "SPX", round_up=True) == 4.40     # SPX 0.10 tick above $3
