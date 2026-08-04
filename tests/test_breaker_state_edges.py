"""Circuit-breaker state-machine edges beyond the happy path: double trip,
flat confirmation without a trip, and the lockout+baseline same-day duo."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from godmode0dte.config import RiskConfig
from godmode0dte.models import Position
from godmode0dte.risk.circuit_breaker import BreakerState, CircuitBreaker
from godmode0dte.risk.governor import ApprovedTrade
from tests.conftest import make_intent
from tests.test_risk_governor import entry_window_now

UTC = timezone.utc


def make(tmp_path, day=date(2026, 8, 4)) -> CircuitBreaker:
    cfg = RiskConfig(lockout_file=str(tmp_path / "lockout.json"))
    return CircuitBreaker(cfg, day)


def test_trip_while_tripped_keeps_first_reason(tmp_path):
    b = make(tmp_path)
    b.set_starting_equity(100_000)
    b.trip("first")
    b.trip("second")                                    # must be a no-op
    assert b.state is BreakerState.TRIPPED
    assert b.trip_reason == "first"
    payload = json.loads((tmp_path / "lockout.json").read_text())
    assert payload["reason"] == "first"                 # disk agrees


def test_confirm_flat_while_armed_does_not_lock(tmp_path):
    b = make(tmp_path)
    b.set_starting_equity(100_000)
    b.confirm_flat()                                    # flat but never tripped
    assert b.state is BreakerState.ARMED and b.allows_entries


def test_confirm_flat_is_idempotent_once_locked(tmp_path):
    b = make(tmp_path)
    b.set_starting_equity(100_000)
    b.trip("kill")
    b.confirm_flat()
    b.confirm_flat()
    assert b.state is BreakerState.LOCKED


def test_lockout_and_baseline_restore_together_same_day(tmp_path):
    b1 = make(tmp_path)
    b1.set_starting_equity(100_000)
    assert b1.check(93_000) is BreakerState.TRIPPED     # -7%
    b1.confirm_flat()
    # Same-day restart: LOCKED from lockout.json AND baseline from
    # baseline.json — a recovered equity print cannot re-arm or re-anchor.
    b2 = make(tmp_path)
    assert b2.state is BreakerState.LOCKED
    assert b2.starting_equity == 100_000
    assert b2.check(120_000) is BreakerState.LOCKED
    b2.set_starting_equity(93_000)                      # equity feed must not re-anchor
    assert b2.starting_equity == 100_000
    # Next day: both artifacts expire.
    b3 = make(tmp_path, day=date(2026, 8, 5))
    assert b3.state is BreakerState.ARMED and b3.starting_equity is None


def test_governor_flat_confirmation_keeps_armed_breaker_open(cfg, governor):
    """update_position() calls confirm_flat() whenever the book is flat —
    an ordinary profitable close while ARMED must not lock the session."""
    entry_window_now(cfg)
    now = datetime.now(UTC)
    r = governor.evaluate(make_intent(score=99.0, debit=1.0, contracts=2))
    assert isinstance(r, ApprovedTrade)
    closed = Position(trade_id=r.trade_id, vertical=r.vertical, entry_debit=1.0,
                      entry_ts=now, score_at_entry=99.0, or_mid=560.0,
                      current_value=1.2, exit_ts=now, exit_value=1.2,
                      exit_reason="profit_target")
    governor.update_position(closed)                    # book goes flat, ARMED
    assert governor.breaker.state is BreakerState.ARMED
    r2 = governor.evaluate(make_intent(score=99.0, debit=1.0, contracts=2))
    assert isinstance(r2, ApprovedTrade)                # trading continues
