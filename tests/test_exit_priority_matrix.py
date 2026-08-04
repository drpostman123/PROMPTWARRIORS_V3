"""Same-tick pre-emption matrix for the exit priority table (spec §7).

Existing tests fire one rule at a time; these put P0/P1/P2/P3/P4a in
conflict on the SAME tick and assert exactly who wins.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from godmode0dte.execution.exits import ExitEngine
from godmode0dte.models import Direction, Position, VerticalSpec
from tests.test_exit_engine import NOW

UTC = timezone.utc
FORCE_FLAT_NOW = datetime(2026, 8, 4, 19, 45, tzinfo=UTC)   # 15:45 ET, past 15:30


def reg(governor, trade_id: str, contracts: int, debit: float, mark: float,
        held_min: int = 5) -> Position:
    v = VerticalSpec(underlying="SPY", direction=Direction.LONG,
                     expiration="2026-08-04", long_strike=560.0, short_strike=562.0,
                     width=2.0, debit=debit, contracts=contracts,
                     long_symbol="L", short_symbol="S")
    pos = Position(trade_id=trade_id, vertical=v, entry_debit=debit,
                   entry_ts=NOW - timedelta(minutes=held_min), score_at_entry=99.0,
                   or_mid=560.0, current_value=mark, mark_ts=NOW)
    governor.register_position(pos)
    return pos


def test_p1_force_flat_preempts_p3_and_p4a(cfg, governor):
    reg(governor, "stopped", 2, 1.0, 0.40)     # P3 territory: mark <= 0.5 x debit
    reg(governor, "winner", 2, 1.0, 1.80)      # P4a territory: >= min(1.65d, 0.8w)
    decisions = ExitEngine(cfg.exits, governor).evaluate(FORCE_FLAT_NOW, 561.0)
    assert [d.reason for d in decisions] == ["force_flat", "force_flat"]
    assert {d.trade_id for d in decisions} == {"stopped", "winner"}
    assert all(d.urgency == "urgent" for d in decisions)


def test_p0_breaker_preempts_p1_force_flat(cfg, governor):
    reg(governor, "any", 2, 1.0, 1.0)
    governor.kill("test")
    decisions = ExitEngine(cfg.exits, governor).evaluate(FORCE_FLAT_NOW, 561.0)
    assert [d.reason for d in decisions] == ["circuit_breaker"]
    assert decisions[0].urgency == "urgent"


def test_p2_heat_claims_largest_position_p3_still_arms_for_others(cfg, governor):
    # "big": 40 x $1.80 = $7,200 risk (7.2% of 100k) AND in P4a territory
    # (mark 1.8 >= 0.8 x width). "small": at the P3 hard-stop level.
    reg(governor, "big", 40, 1.8, 1.8)
    reg(governor, "small", 4, 1.0, 0.40)
    engine = ExitEngine(cfg.exits, governor)
    first = engine.evaluate(NOW, underlying_price=561.0)
    # Tick 1: P2 claims "big" (pre-empting its own P4a); "small" only ARMS P3.
    assert [(d.trade_id, d.reason) for d in first] == [("big", "heat_breach")]
    assert first[0].urgency == "urgent"
    second = engine.evaluate(NOW, underlying_price=561.0)
    # Tick 2: P2 again for "big"; P3 confirms on the second consecutive mark.
    assert [(d.trade_id, d.reason) for d in second] == [
        ("big", "heat_breach"), ("small", "hard_stop")]
    assert second[1].urgency == "urgent"
