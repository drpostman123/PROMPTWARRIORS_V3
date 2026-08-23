"""Breakers: HWM trip + persistence, fail-safe restore, soft-tier
hysteresis, daily pause with no mid-day re-anchor, NAV quarantine,
probation persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from skyfire_sol.config import RiskConfig
from skyfire_sol.safety.breakers import BreakerState, PortfolioBreaker
from skyfire_sol.safety.probation import Probation

CFG = RiskConfig()
T0 = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def breaker(tmp_path) -> PortfolioBreaker:
    return PortfolioBreaker(CFG, str(tmp_path))


def feed(b: PortfolioBreaker, nav: float, now=None) -> None:
    sane = b.accept_nav(nav)
    assert sane is not None
    b.on_nav(sane, now or T0)


def test_hwm_trip_at_60pct(tmp_path):
    b = breaker(tmp_path)
    feed(b, 1000.0)
    assert b.state is BreakerState.ARMED
    # walk down gradually so the quarantine accepts each reading
    for nav in (850, 700, 590, 500, 440, 400):
        b.on_nav(float(nav), T0)
    assert b.state is BreakerState.TRIPPED
    b.confirm_flat()
    assert b.state is BreakerState.LOCKED


def test_trip_persists_and_restores_locked(tmp_path, monkeypatch):
    b = breaker(tmp_path)
    feed(b, 1000.0)
    b.trip("test")
    monkeypatch.delenv("SKYFIRE_ACK_DRAWDOWN", raising=False)
    b2 = breaker(tmp_path)
    assert b2.state is BreakerState.LOCKED


def test_ack_env_rearms(tmp_path, monkeypatch):
    b = breaker(tmp_path)
    feed(b, 1000.0)
    b.trip("test")
    monkeypatch.setenv("SKYFIRE_ACK_DRAWDOWN", "YES")
    b2 = breaker(tmp_path)
    assert b2.state is BreakerState.ARMED
    monkeypatch.delenv("SKYFIRE_ACK_DRAWDOWN", raising=False)
    assert breaker(tmp_path).state is BreakerState.ARMED   # ack persisted


def test_unreadable_hwm_fails_locked(tmp_path):
    (tmp_path / "hwm.json").write_text("{corrupt")
    assert breaker(tmp_path).state is BreakerState.LOCKED


def test_soft_tier_hysteresis(tmp_path):
    # generous defaults: tier at -40%, clears at -30%
    b = breaker(tmp_path)
    feed(b, 1000.0)
    for nav in (850, 720, 690, 640, 590):
        b.on_nav(float(nav), T0)
    assert b.soft_tier_active                       # -41%
    b.on_nav(650.0, T0)                             # -35%: still latched
    assert b.soft_tier_active
    b.on_nav(710.0, T0)                             # -29%: clears at -30
    assert not b.soft_tier_active
    b2 = breaker(tmp_path)                          # persisted either way
    assert not b2.soft_tier_active


def test_daily_pause_and_no_reanchor(tmp_path):
    # generous default: daily pause at -15%
    b = breaker(tmp_path)
    feed(b, 1000.0, T0)
    b.on_nav(880.0, T0 + timedelta(hours=1))        # -12%: no pause yet
    assert b.pause_until is None
    b.on_nav(840.0, T0 + timedelta(hours=1))        # -16% intraday
    assert b.pause_until is not None
    ok, why = b.entries_allowed(T0 + timedelta(hours=2))
    assert not ok and why == "daily_pause"
    # restart mid-day: baseline restored from disk, not re-anchored at 880
    b2 = breaker(tmp_path)
    assert b2._daily_baseline == 1000.0
    assert b2.pause_until is not None               # pause survives restart
    # next UTC day: pause expiry + fresh baseline
    next_day = T0 + timedelta(days=1, hours=1)
    ok, _ = b2.entries_allowed(next_day)
    assert ok
    b2.on_nav(880.0, next_day)
    assert b2._daily_baseline == 880.0


def test_nav_quarantine_needs_3_confirmations(tmp_path):
    b = breaker(tmp_path)
    assert b.accept_nav(1000.0) == 1000.0
    assert b.accept_nav(400.0) is None              # -60% jump: suspect
    assert b.accept_nav(410.0) is None
    assert b.accept_nav(405.0) == 405.0             # 3rd consistent: accepted
    assert b.accept_nav(0.0) is None                # non-positive never sane


def test_probation_is_manual_button_only(tmp_path):
    p = Probation(CFG, str(tmp_path))
    assert p.active and p.size_mult == 0.5
    for _ in range(25):
        p.record_fill(0.2)                          # clean fills NEVER auto-lift
    assert p.active and p.size_mult == 0.5
    assert p.clean_fills == 25 and p.ready          # readiness signal only
    p.record_fill(5.0)                              # dirty: logged, not counted
    assert p.clean_fills == 25
    # the operator pushes the button
    (tmp_path / "FULL_SIZE").write_text("go")
    assert not p.active and p.size_mult == 1.0
    assert not Probation(CFG, str(tmp_path)).active  # button survives restart
    # deleting the button re-engages probation instantly
    (tmp_path / "FULL_SIZE").unlink()
    assert p.active and p.size_mult == 0.5
    # counter persistence survives restart; unreadable counter still safe
    assert Probation(CFG, str(tmp_path)).clean_fills == 25
    (tmp_path / "probation.json").write_text("{bad")
    p3 = Probation(CFG, str(tmp_path))
    assert p3.active and p3.clean_fills == 0
