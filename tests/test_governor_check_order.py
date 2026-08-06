"""Governor check ORDER guarantees (the docstring's 1..10 sequence).

The suite proves individual checks work; these prove earlier checks
pre-empt later ones, and that the score threshold is inclusive at 93.0.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from godmode0dte.models import Rejection
from godmode0dte.risk.circuit_breaker import CircuitBreaker
from godmode0dte.risk.governor import ApprovedTrade, RiskGovernor
from tests.conftest import make_intent
from tests.test_risk_governor import entry_window_now

UTC = timezone.utc


def make_gov(cfg, equity: float) -> RiskGovernor:
    cfg.risk.min_equity = 1_500.0   # target the sizing layer, not the equity floor
    gov = RiskGovernor(cfg, CircuitBreaker(cfg.risk, date(2026, 8, 4)))
    gov.update_equity(equity, datetime.now(UTC))
    return gov


def test_score_exactly_at_min_passes(cfg, governor):
    """min_score is 93.0 and the gate is `total < min` — 93.0 EXACTLY trades."""
    entry_window_now(cfg)
    r93 = governor.evaluate(make_intent(score=93.0, debit=1.0, contracts=4))
    assert isinstance(r93, ApprovedTrade)
    r_below = governor.evaluate(make_intent(score=92.99, debit=1.0, contracts=4))
    assert isinstance(r_below, Rejection) and r_below.reason == "score_below_min"


def test_breaker_check_precedes_score_check(cfg, governor):
    entry_window_now(cfg)
    governor.breaker.trip("manual")
    r = governor.evaluate(make_intent(score=10.0))     # would fail score too
    assert isinstance(r, Rejection) and r.reason == "circuit_breaker"


def test_burst_protection_fires_before_sizing(cfg):
    """Check 7 (burst) must pre-empt check 9 (sizing).

    At $3k equity a $1.00-debit intent is unsizeable (control test below
    proves it rejects `size_zero` on its own), but sent inside the spacing
    window the rejection must be `burst_protection` — sizing never runs.
    """
    entry_window_now(cfg)
    cfg.risk.min_intent_spacing_sec = 60.0
    gov = make_gov(cfg, 2_000.0)   # $92.60 one-lot > $80 = 4% cap: truly unsizeable
    first = gov.evaluate(make_intent(score=99.0, debit=0.20, contracts=10))
    assert isinstance(first, ApprovedTrade)
    second = gov.evaluate(make_intent(score=99.0, debit=1.0, contracts=10))
    assert isinstance(second, Rejection)
    assert second.reason == "burst_protection"


def test_control_unsizeable_intent_rejects_size_zero_without_burst(cfg):
    """Control for the test above: same intent, no recent approval ->
    the rejection really is sizing's (one contract at cap 0.90 = $90 >
    $60 = 2% of $3k)."""
    entry_window_now(cfg)                              # spacing stays 0 (conftest)
    gov = make_gov(cfg, 2_000.0)   # $92.60 one-lot > $80 = 4% cap: truly unsizeable
    r = gov.evaluate(make_intent(score=99.0, debit=1.0, contracts=10))
    assert isinstance(r, Rejection) and r.reason == "size_zero"
