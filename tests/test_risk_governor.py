"""The hard constraints must hold no matter what the signal engine sends."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from godmode0dte.models import Position, Rejection
from godmode0dte.risk.governor import ApprovedTrade, RiskGovernor, _APPROVAL_TOKEN
from tests.conftest import make_intent

UTC = timezone.utc


def entry_window_now(cfg):
    """Tests run at arbitrary wall-clock times; widen the window."""
    from datetime import time
    cfg.signal.entry_window_start = time(0, 0)
    cfg.signal.entry_window_end = time(23, 59, 59)


def test_forged_approval_raises(governor):
    with pytest.raises(PermissionError):
        ApprovedTrade(trade_id="hax", vertical=make_intent().vertical,
                      risk_dollars=1.0, score=99.0, approved_ts=datetime.now(UTC))


def test_per_trade_risk_never_exceeds_4pct(cfg, governor):
    entry_window_now(cfg)
    # Intent asks for 100 contracts x $1 debit = $10k risk on $100k equity (10%).
    result = governor.evaluate(make_intent(score=99.0, debit=1.0, contracts=100))
    assert isinstance(result, ApprovedTrade)
    assert result.risk_dollars <= 100_000 * 0.04 + 1e-6
    assert result.vertical.contracts <= 40


def test_score_below_min_rejected(cfg, governor):
    entry_window_now(cfg)
    result = governor.evaluate(make_intent(score=92.9))
    assert isinstance(result, Rejection) and result.reason == "score_below_min"


def test_phase_a_ladder_is_flat_2pct(cfg, governor):
    """Launch ladder: 2% at every score (Phase B/C unlock via calibration)."""
    entry_window_now(cfg)
    r93 = governor.evaluate(make_intent(score=93.5, debit=1.0, contracts=1000))
    r97 = governor.evaluate(make_intent(score=97.5, debit=1.0, contracts=1000))
    assert isinstance(r93, ApprovedTrade) and isinstance(r97, ApprovedTrade)
    assert r93.risk_dollars == pytest.approx(100_000 * 0.02, rel=0.03)
    assert r97.risk_dollars == pytest.approx(100_000 * 0.02, rel=0.03)


def test_phase_c_ladder_scales_but_stays_capped(cfg, governor):
    entry_window_now(cfg)
    cfg.risk.sizing_ladder = {93: 0.5, 97: 1.0}   # Phase C
    r97 = governor.evaluate(make_intent(score=99.0, debit=1.0, contracts=1000))
    assert isinstance(r97, ApprovedTrade)
    assert r97.risk_dollars == pytest.approx(100_000 * 0.04, rel=0.03)
    assert r97.risk_dollars <= 100_000 * 0.04 + 1e-6


def test_heat_cap_resizes_then_rejects(cfg, governor):
    entry_window_now(cfg)
    first = governor.evaluate(make_intent(score=99.0, debit=1.0, contracts=1000))
    assert isinstance(first, ApprovedTrade)
    governor.register_position(Position(
        trade_id=first.trade_id, vertical=first.vertical, entry_debit=1.0,
        entry_ts=datetime.now(UTC), score_at_entry=99.0, or_mid=560.0, current_value=1.0,
    ))
    # Second full-size approval must be trimmed to keep total <= 7%.
    second = governor.evaluate(make_intent(score=99.0, debit=1.0, contracts=1000))
    assert isinstance(second, ApprovedTrade)
    total = first.risk_dollars + second.risk_dollars
    assert total <= 100_000 * 0.07 + 1e-6


def test_concurrency_cap_two_positions(cfg, governor):
    entry_window_now(cfg)
    for _ in range(2):
        r = governor.evaluate(make_intent(score=99.0, debit=0.5, contracts=4))
        assert isinstance(r, ApprovedTrade)
        governor.register_position(Position(
            trade_id=r.trade_id, vertical=r.vertical, entry_debit=0.5,
            entry_ts=datetime.now(UTC), score_at_entry=99.0, or_mid=560.0, current_value=0.5,
        ))
    third = governor.evaluate(make_intent(score=99.0, debit=0.5, contracts=4))
    assert isinstance(third, Rejection) and third.reason == "max_concurrent"


def test_daily_loss_trips_breaker_and_blocks(cfg, governor):
    entry_window_now(cfg)
    governor.update_equity(93_999.0, datetime.now(UTC))   # -6.001%
    assert not governor.breaker.allows_entries
    result = governor.evaluate(make_intent(score=99.0))
    assert isinstance(result, Rejection) and result.reason == "circuit_breaker"


def test_stale_quotes_rejected(cfg, governor):
    entry_window_now(cfg)
    intent = make_intent(score=99.0)
    object.__setattr__(intent, "quote_ts", datetime.now(UTC) - timedelta(seconds=60))
    result = governor.evaluate(intent)
    assert isinstance(result, Rejection) and result.reason == "stale_quotes"


def test_hard_gate_failure_rejected(cfg, governor):
    entry_window_now(cfg)
    intent = make_intent(score=95.0)
    object.__setattr__(intent.score, "hard_gate_failures", ("event blackout",))
    result = governor.evaluate(intent)
    assert isinstance(result, Rejection) and result.reason == "hard_gate"
