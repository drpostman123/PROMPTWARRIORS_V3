"""Exit priority engine behavior (spec §7): pre-emption and confirmations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from godmode0dte.execution.exits import ExitEngine
from godmode0dte.models import Position
from tests.conftest import make_intent
from tests.test_risk_governor import entry_window_now

UTC = timezone.utc
ET = ZoneInfo("America/New_York")


# Fixed timeline: "now" is 13:00 ET (17:00 UTC, EDT) on 2026-08-04.
NOW = datetime(2026, 8, 4, 17, 0, tzinfo=UTC)


def open_position(governor, cfg, debit=1.0, contracts=4, mark=None, held_min=5) -> Position:
    entry_window_now(cfg)
    approved = governor.evaluate(make_intent(score=99.0, debit=debit, contracts=contracts))
    pos = Position(
        trade_id=approved.trade_id, vertical=approved.vertical, entry_debit=debit,
        entry_ts=NOW - timedelta(minutes=held_min),
        score_at_entry=99.0, or_mid=560.0,
        current_value=mark if mark is not None else debit,
        mark_ts=NOW,     # freshly marked — the never-marked escalation is tested separately
    )
    governor.register_position(pos)
    return pos


def mid_session_now() -> datetime:
    return NOW


def test_hard_stop_requires_two_consecutive_marks(cfg, governor):
    pos = open_position(governor, cfg, debit=1.0, mark=0.45)
    engine = ExitEngine(cfg.exits, governor)
    now = mid_session_now()
    assert engine.evaluate(now, underlying_price=561.0) == []          # first mark: armed
    decisions = engine.evaluate(now, underlying_price=561.0)           # second mark: fire
    assert [d.reason for d in decisions] == ["hard_stop"]
    assert decisions[0].urgency == "urgent"
    assert decisions[0].trade_id == pos.trade_id


def test_hard_stop_counter_resets_on_recovery(cfg, governor):
    pos = open_position(governor, cfg, debit=1.0, mark=0.45)
    engine = ExitEngine(cfg.exits, governor)
    now = mid_session_now()
    assert engine.evaluate(now, underlying_price=561.0) == []
    governor.update_position(Position(**{**pos.__dict__, "current_value": 0.9}))
    assert engine.evaluate(now, underlying_price=561.0) == []          # recovered: reset
    governor.update_position(Position(**{**pos.__dict__, "current_value": 0.45}))
    assert engine.evaluate(now, underlying_price=561.0) == []          # armed again, not fired


def test_profit_target_fires_at_165pct(cfg, governor):
    open_position(governor, cfg, debit=1.0, mark=1.66)
    decisions = ExitEngine(cfg.exits, governor).evaluate(mid_session_now(), 565.0)
    assert [d.reason for d in decisions] == ["profit_target"]


def test_structure_stop_needs_low_pnl(cfg, governor):
    # Price back through the OR trigger (560) but position up 40% -> no exit.
    open_position(governor, cfg, debit=1.0, mark=1.40)
    assert ExitEngine(cfg.exits, governor).evaluate(mid_session_now(), 559.0) == []
    # Same break with flat P&L -> structure stop.
    gov2_positions = governor.open_positions
    governor.update_position(Position(**{**gov2_positions[0].__dict__, "current_value": 1.02}))
    decisions = ExitEngine(cfg.exits, governor).evaluate(mid_session_now(), 559.0)
    assert [d.reason for d in decisions] == ["structure_stop"]


def test_time_stop_after_90min_stale_hold(cfg, governor):
    open_position(governor, cfg, debit=1.0, mark=1.05, held_min=95)
    decisions = ExitEngine(cfg.exits, governor).evaluate(mid_session_now(), 561.0)
    assert [d.reason for d in decisions] == ["time_stop"]


def test_breaker_flatten_preempts_profit(cfg, governor):
    open_position(governor, cfg, debit=1.0, mark=1.80)   # up 80%
    governor.kill("test")
    decisions = ExitEngine(cfg.exits, governor).evaluate(mid_session_now(), 565.0)
    assert [d.reason for d in decisions] == ["circuit_breaker"]        # P0 beats P4a
    assert decisions[0].urgency == "urgent"
