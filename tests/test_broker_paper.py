"""Paper broker exercises the same order path/signatures as live."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from godmode0dte.config import ExecutionConfig
from godmode0dte.execution.broker import PaperBroker
from godmode0dte.models import Quote
from tests.conftest import make_intent
from godmode0dte.risk.governor import RiskGovernor
from godmode0dte.risk.circuit_breaker import CircuitBreaker


def q(mid: float, spread: float = 0.02) -> Quote:
    return Quote(symbol="X", bid=mid - spread / 2, ask=mid + spread / 2,
                 bid_size=50, ask_size=50, ts=datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_paper_round_trip_updates_equity(cfg, governor: RiskGovernor):
    from tests.test_risk_governor import entry_window_now
    entry_window_now(cfg)
    broker = PaperBroker(ExecutionConfig(), starting_equity=100_000)

    # Debit 0.80 on a 2-wide = 40% of width — inside the 0.30-0.42 acceptance
    # band; a 50%-of-width debit is one the system itself would refuse, and
    # paper now enforces the same governor cap_price as live.
    approved = governor.evaluate(make_intent(score=99.0, debit=0.80, contracts=10))
    fill = await broker.open_position(approved, q(1.8), q(1.0))
    assert fill is not None
    assert fill.price <= approved.cap_price

    # Exit at a higher value -> equity increases by the P&L.
    exit_fill = await broker.close_position(approved.trade_id, approved.vertical,
                                            q(2.4), q(1.2), urgency="normal")
    assert exit_fill is not None
    pnl = (exit_fill.price - fill.price) * approved.vertical.contracts * 100
    fees = ExecutionConfig().friction_per_contract * approved.vertical.contracts
    # Paper equity is net of friction — fee-blind paper would flatter the edge.
    assert await broker.equity() == pytest.approx(100_000 + pnl - fees)
