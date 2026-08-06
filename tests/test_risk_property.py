"""Property-style invariant: across random equities, debits, fills, and
mark walks, every governor approval respects the 4% per-trade cap and the
7% heat cap AT APPROVAL TIME — marks may later inflate heat (that is P2's
job), but the governor must never approve into a breach."""

from __future__ import annotations

import random
from datetime import date, datetime, timezone

from godmode0dte.models import Position
from godmode0dte.risk.circuit_breaker import CircuitBreaker
from godmode0dte.risk.governor import ApprovedTrade, RiskGovernor
from tests.conftest import make_intent
from tests.test_risk_governor import entry_window_now

UTC = timezone.utc


def test_random_marks_never_breach_caps_after_approval(cfg, tmp_path):
    entry_window_now(cfg)
    rng = random.Random(1337)
    approvals = 0
    for trial in range(150):
        # Fresh lockout/baseline dir per trial so random equities never
        # look like a -6% drawdown against a previous trial's baseline.
        cfg.risk.lockout_file = str(tmp_path / f"trial{trial}" / "lockout.json")
        cfg.risk.day_trade_file = str(tmp_path / f"trial{trial}" / "day_trades.json")
        equity = rng.uniform(500.0, 500_000.0)
        gov = RiskGovernor(cfg, CircuitBreaker(cfg.risk, date(2026, 8, 4)))
        gov.update_equity(equity, datetime.now(UTC))
        for _ in range(4):
            intent = make_intent(score=rng.uniform(93.0, 100.0),
                                 debit=round(rng.uniform(0.30, 0.88), 2),
                                 contracts=rng.randint(1, 200))
            result = gov.evaluate(intent)
            if not isinstance(result, ApprovedTrade) and equity < 1_500:
                # Round 4: the dead zone must reject with a NAMED reason,
                # never approve — $1,500 is below every one-lot floor here.
                assert result.reason in ("size_zero", "equity_floor", "equity_stale",
                                         "worst_case_day", "heat_cap")
            if isinstance(result, ApprovedTrade):
                approvals += 1
                # Hard cap 1: per-trade risk (at the WORST permitted fill).
                assert result.risk_dollars <= equity * 0.04 + 1e-6
                # Hard cap 2: projected heat at approval time.
                assert gov.open_risk_dollars + result.risk_dollars <= equity * 0.07 + 1e-6
                fill = rng.uniform(0.5 * result.cap_price, result.cap_price)
                gov.register_position(Position(
                    trade_id=result.trade_id, vertical=result.vertical,
                    entry_debit=fill, entry_ts=datetime.now(UTC),
                    score_at_entry=result.score, or_mid=560.0, current_value=fill,
                ))
                # Actual fill <= cap_price, so realized heat also holds.
                assert gov.heat_pct <= 7.0 + 1e-6
            # Random mark walk: winners inflate their heat contribution
            # (max(entry, mark)); the NEXT approval must absorb that.
            for p in list(gov.open_positions):
                gov.update_position(Position(**{
                    **p.__dict__, "current_value": rng.uniform(0.0, 2.0)}))
    assert approvals >= 100        # the property must not pass vacuously
