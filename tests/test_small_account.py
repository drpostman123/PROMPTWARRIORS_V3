"""Small-account audit (round 4): the $2k-$25k operator reality.

Verifies the system is neither SILENTLY DEAD at low equity nor
ACCIDENTALLY OVER-EXPOSED by 1-contract granularity or fee drag.

Arithmetic anchors (SPY 2-wide, friction $2.60):
  debit 0.75 -> cap 0.825 -> per-contract risk $85.10 -> one-lot floor $2,127.50
  debit 0.85 -> cap 0.90 (0.45W binds) -> $92.60      -> one-lot floor $2,315
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from godmode0dte.config import AppConfig, RiskConfig
from godmode0dte.models import Position, Rejection
from godmode0dte.risk.circuit_breaker import CircuitBreaker
from godmode0dte.risk.day_trades import DayTradeBudget
from godmode0dte.risk.governor import ApprovedTrade, RiskGovernor
from godmode0dte.risk.sizing import size_trade
from tests.conftest import make_intent
from tests.test_risk_governor import entry_window_now

UTC = timezone.utc


def gov_at(cfg, equity: float) -> RiskGovernor:
    entry_window_now(cfg)
    g = RiskGovernor(cfg, CircuitBreaker(cfg.risk, date(2026, 8, 4)))
    g.update_equity(equity, datetime.now(UTC))
    return g


# ---- one-lot minimum under the hard cap ------------------------------------

def test_one_lot_floor_boundaries():
    cfg = RiskConfig(day_trade_file="/tmp/unused-dt.json")
    # $2,315 floor at the worst-fill cap ($0.90 + $2.60 = $92.60):
    assert size_trade(93.0, 2_316.0, 0.90, 100, cfg, 2.60)[0] == 1
    assert size_trade(93.0, 2_314.0, 0.90, 100, cfg, 2.60)[0] == 0
    # ladder target holds a lot on its own from $4,630:
    c, risk = size_trade(93.0, 4_630.0, 0.90, 100, cfg, 2.60)
    assert c == 1 and risk == pytest.approx(92.60)


def test_one_lot_overshoots_ladder_never_cap():
    cfg = RiskConfig(day_trade_file="/tmp/unused-dt.json")
    c, risk = size_trade(93.0, 3_000.0, 0.825, 100, cfg, 2.60)
    assert c == 1
    assert risk == pytest.approx(85.10)
    assert risk / 3_000.0 > 0.02          # overshoots the 2% ladder intent...
    assert risk <= 3_000.0 * 0.04         # ...never the 4% law


def test_one_lot_disabled_restores_strictness():
    cfg = RiskConfig(allow_one_lot_minimum=False, day_trade_file="/tmp/unused-dt.json")
    assert size_trade(93.0, 3_000.0, 0.825, 100, cfg, 2.60)[0] == 0


def test_governor_risk_includes_friction(cfg, governor):
    """Real cash loss (worst fill + fees) is what the 4% cap bounds."""
    entry_window_now(cfg)
    r = governor.evaluate(make_intent(score=99.0, debit=1.0, contracts=1000))
    assert isinstance(r, ApprovedTrade)
    per = r.cap_price * 100 + cfg.execution.friction_per_contract
    assert r.risk_dollars == pytest.approx(r.vertical.contracts * per)
    assert r.risk_dollars <= 100_000 * 0.04 + 1e-6


def test_3k_account_trades_one_lot(cfg):
    g = gov_at(cfg, 3_000.0)
    r = g.evaluate(make_intent(score=99.0, debit=0.75, contracts=10))
    assert isinstance(r, ApprovedTrade)
    assert r.vertical.contracts == 1
    assert r.risk_dollars <= 3_000.0 * 0.04


def test_2k_account_rejects_loudly_not_silently(cfg):
    cfg.risk.min_equity = 1_500.0     # bypass the floor to expose the size_zero detail
    g = gov_at(cfg, 2_000.0)
    r = g.evaluate(make_intent(score=99.0, debit=0.85, contracts=10))
    assert isinstance(r, Rejection) and r.reason == "size_zero"
    assert "need >=" in r.detail          # tells the operator the required equity


# ---- fee floor -------------------------------------------------------------

def test_fee_floor_rejects_junk_economics(cfg, governor):
    entry_window_now(cfg)
    cfg.execution.friction_per_contract = 10.0    # SPX-like fees on SPY economics
    r = governor.evaluate(make_intent(score=99.0, debit=0.60, contracts=10))
    assert isinstance(r, Rejection) and r.reason == "fee_floor"


def test_fee_floor_passes_all_legal_spy_builds(cfg, governor):
    entry_window_now(cfg)
    for debit in (0.60, 0.75, 0.84):              # the 0.30-0.42W acceptance band
        r = governor.evaluate(make_intent(score=99.0, debit=debit, contracts=2))
        assert isinstance(r, ApprovedTrade), f"debit {debit} wrongly gated"
        # register nothing: governor state must stay clean between checks
        g_positions = governor.open_positions
        assert g_positions == []


# ---- spec 2.3 gates --------------------------------------------------------

def test_daily_trade_cap(cfg, governor):
    entry_window_now(cfg)
    cfg.risk.daily_trade_cap = 3
    for _ in range(3):
        assert isinstance(
            governor.evaluate(make_intent(score=99.0, debit=0.75, contracts=1)),
            ApprovedTrade)
    r4 = governor.evaluate(make_intent(score=99.0, debit=0.75, contracts=1))
    assert isinstance(r4, Rejection) and r4.reason == "daily_trade_cap"


def test_soft_loss_tier_halves_then_locks(cfg):
    g = gov_at(cfg, 100_000.0)
    g.update_equity(95_500.0, datetime.now(UTC))        # -4.5% day
    r1 = g.evaluate(make_intent(score=99.0, debit=0.75, contracts=1000))
    assert isinstance(r1, ApprovedTrade)
    full = int(95_500 * 0.02 // 85.10)
    assert r1.vertical.contracts <= full // 2 + 1       # halved
    r2 = g.evaluate(make_intent(score=99.0, debit=0.75, contracts=1000))
    assert isinstance(r2, Rejection) and r2.reason == "soft_loss_lockout"


def test_worst_case_day_gate_makes_minus_6_unreachable(cfg):
    """Two sequential 1-lot 4% losses must NOT be able to stack to -8%."""
    cfg.risk.min_equity = 2_000.0
    g = gov_at(cfg, 2_400.0)                            # one lot at 0.90 = 3.86%
    r1 = g.evaluate(make_intent(score=99.0, debit=0.85, contracts=5))
    assert isinstance(r1, ApprovedTrade)
    g.register_position(Position(
        trade_id=r1.trade_id, vertical=r1.vertical, entry_debit=0.90,
        entry_ts=datetime.now(UTC), score_at_entry=99.0, or_mid=560.0,
        current_value=0.90))
    # Second lot: 3.86% held + 3.86% new = 7.7% potential day loss > 6% -> blocked.
    r2 = g.evaluate(make_intent(score=99.0, debit=0.85, contracts=5))
    assert isinstance(r2, Rejection)
    assert r2.reason in ("worst_case_day", "heat_cap")


def test_buying_power_binds_sizing(cfg, governor):
    entry_window_now(cfg)
    governor.update_buying_power(5_000.0)               # net-liq 100k, BP only 5k
    r = governor.evaluate(make_intent(score=99.0, debit=0.75, contracts=1000))
    assert isinstance(r, ApprovedTrade)
    assert r.risk_dollars <= 5_000.0 * 0.04 + 1e-6


# ---- day-trade budget (opt-in: broker still enforcing during phase-in) -----

def test_day_trade_budget_only_gates_margin_small(tmp_path):
    for account_type, expect in (("margin_large", True), ("cash", True), ("margin_small", False)):
        cfg = RiskConfig(account_type=account_type,
                         day_trade_file=str(tmp_path / f"{account_type}.json"))
        b = DayTradeBudget(cfg, date(2026, 8, 6))       # Thursday
        for _ in range(3):
            b.record()
        assert b.allows() is expect, account_type


def test_day_trade_budget_rolls_off_and_persists(tmp_path):
    cfg = RiskConfig(account_type="margin_small",
                     day_trade_file=str(tmp_path / "dt.json"))
    b = DayTradeBudget(cfg, date(2026, 8, 3))           # Monday: 3 trades
    for _ in range(3):
        b.record()
    assert not b.allows()
    # Restart same week (Thursday): window still contains Monday -> blocked.
    b2 = DayTradeBudget(cfg, date(2026, 8, 6))
    assert not b2.allows()
    # Restart the NEXT Monday: Aug 3 has rolled out of the 5-business-day window.
    cfg2 = RiskConfig(account_type="margin_small",
                      day_trade_file=str(tmp_path / "dt.json"))
    b3 = DayTradeBudget(cfg2, date(2026, 8, 10))
    assert b3.allows()


def test_unreadable_ledger_fails_safe(tmp_path):
    path = tmp_path / "dt.json"
    path.write_text("{corrupt")
    cfg = RiskConfig(account_type="margin_small", day_trade_file=str(path))
    assert not DayTradeBudget(cfg, date(2026, 8, 6)).allows()


# ---- config guards ---------------------------------------------------------

def test_spx_rejected_at_small_min_equity(monkeypatch):
    monkeypatch.setenv("GODMODE_CONFIRM_LIVE", "YES")
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"execution": {"underlying": "SPX"},
                                  "risk": {"min_equity": 3000}})
