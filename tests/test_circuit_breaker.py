from __future__ import annotations

import json
from datetime import date

from godmode0dte.config import RiskConfig
from godmode0dte.risk.circuit_breaker import BreakerState, CircuitBreaker


def make(tmp_path, day=date(2026, 8, 4)) -> CircuitBreaker:
    cfg = RiskConfig(lockout_file=str(tmp_path / "lockout.json"))
    return CircuitBreaker(cfg, day)


def test_trips_at_6_percent(tmp_path):
    b = make(tmp_path)
    b.set_starting_equity(100_000)
    assert b.check(95_000) is BreakerState.ARMED     # -5%
    assert b.check(94_000) is BreakerState.TRIPPED   # -6%
    b.confirm_flat()
    assert b.state is BreakerState.LOCKED


def test_lockout_survives_restart(tmp_path):
    b = make(tmp_path)
    b.set_starting_equity(100_000)
    b.check(90_000)
    # New process, same day: must boot LOCKED.
    b2 = make(tmp_path)
    assert b2.state is BreakerState.LOCKED
    # Next day: lockout expires.
    b3 = make(tmp_path, day=date(2026, 8, 5))
    assert b3.state is BreakerState.ARMED


def test_corrupt_lockout_fails_safe(tmp_path):
    (tmp_path / "lockout.json").write_text("{not json")
    b = make(tmp_path)
    assert b.state is BreakerState.LOCKED


def test_starting_equity_set_once(tmp_path):
    b = make(tmp_path)
    b.set_starting_equity(100_000)
    b.set_starting_equity(50_000)   # later dips must not re-anchor the baseline
    assert b.starting_equity == 100_000


def test_manual_trip_is_irreversible(tmp_path):
    b = make(tmp_path)
    b.set_starting_equity(100_000)
    b.trip("kill switch")
    assert b.state is BreakerState.TRIPPED
    assert b.check(101_000) is BreakerState.TRIPPED   # recovery does not re-arm
