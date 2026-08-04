"""Boot-time position adoption (audit B5) exercised through a stubbed
broker: leg pairing, orphans, quantity mismatches, non-option symbols,
and the paper-mode no-op. No network — GodModeApp is built offline and
only _reconcile() is driven."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from godmode0dte.app import GodModeApp
from godmode0dte.models import Direction

UTC = timezone.utc


class StubLeg:
    def __init__(self, symbol: str, direction: str, qty: float, price: float) -> None:
        self.symbol = symbol
        self.quantity_direction = direction
        self.quantity = qty
        self.average_open_price = price


class StubBroker:
    def __init__(self, legs: list) -> None:
        self._legs = legs

    async def positions(self) -> list:
        return self._legs


def occ(strike: float, cp: str = "C") -> str:
    return f"SPY   260804{cp}{int(round(strike * 1000)):08d}"


@pytest.fixture
def app(cfg, tmp_path) -> GodModeApp:
    cfg.data.snapshot_path = str(tmp_path / "snapshot.json")
    cfg.data.trade_log_path = str(tmp_path / "trades.jsonl")
    cfg.data.decision_log_path = str(tmp_path / "decisions.jsonl")
    a = GodModeApp(cfg)
    # Assignment bypasses the GODMODE_CONFIRM_LIVE validator on purpose:
    # _reconcile() early-returns in paper mode and the live path is the test target.
    a.cfg.paper_mode = False
    a.governor.update_equity(100_000.0, datetime.now(UTC))
    return a


def test_adopts_paired_call_vertical(app):
    app.broker = StubBroker([StubLeg(occ(560), "Long", 3, 0.95),
                             StubLeg(occ(562), "Short", -3, 0.25)])
    asyncio.run(app._reconcile())
    assert len(app.governor.open_positions) == 1
    pos = app.governor.open_positions[0]
    assert (pos.vertical.long_strike, pos.vertical.short_strike,
            pos.vertical.contracts) == (560.0, 562.0, 3)
    assert pos.vertical.direction is Direction.LONG
    assert pos.entry_debit == pytest.approx(0.70)       # 0.95 paid - 0.25 received
    assert app.governor.breaker.allows_entries          # clean pairing: no kill


def test_adopts_paired_put_vertical(app):
    app.broker = StubBroker([StubLeg(occ(560, "P"), "Long", 2, 0.80),
                             StubLeg(occ(558, "P"), "Short", -2, 0.30)])
    asyncio.run(app._reconcile())
    pos = app.governor.open_positions[0]
    assert pos.vertical.direction is Direction.SHORT
    assert (pos.vertical.long_strike, pos.vertical.short_strike) == (560.0, 558.0)
    assert app.governor.breaker.allows_entries


def test_orphan_single_leg_engages_kill(app):
    app.broker = StubBroker([StubLeg(occ(560), "Long", 2, 0.95)])
    asyncio.run(app._reconcile())
    assert app.governor.open_positions == []
    assert not app.governor.breaker.allows_entries      # kill trips the breaker


def test_quantity_mismatch_is_not_adopted(app):
    app.broker = StubBroker([StubLeg(occ(560), "Long", 2, 0.95),
                             StubLeg(occ(562), "Short", -1, 0.25)])
    asyncio.run(app._reconcile())
    assert app.governor.open_positions == []
    assert not app.governor.breaker.allows_entries


def test_non_option_symbol_is_skipped_not_killed(app):
    """Audit R3 #3b: one SPY share must not brick the day with a persisted
    lockout — non-option rows are skipped, only unpairable OPTION legs kill."""
    app.broker = StubBroker([StubLeg("SPY", "Long", 100, 560.0)])
    asyncio.run(app._reconcile())
    assert app.governor.open_positions == []
    assert app.governor.breaker.allows_entries


def test_flat_and_other_underlying_rows_skipped(app):
    app.broker = StubBroker([
        StubLeg(occ(560), "Zero", 0, 0.95),                       # closed today
        StubLeg("QQQ   260804C00480000", "Long", 1, 0.50),        # other underlying
    ])
    asyncio.run(app._reconcile())
    assert app.governor.open_positions == []
    assert app.governor.breaker.allows_entries


def test_pair_plus_orphan_adopts_and_kills(app):
    """Kill blocks NEW entries only — the pair must still be adopted so the
    exit engine keeps managing it (the whole point of audit B5)."""
    app.broker = StubBroker([StubLeg(occ(560), "Long", 1, 0.95),
                             StubLeg(occ(562), "Short", -1, 0.25),
                             StubLeg(occ(555, "P"), "Long", 1, 0.50)])
    asyncio.run(app._reconcile())
    assert len(app.governor.open_positions) == 1
    assert not app.governor.breaker.allows_entries


def test_paper_mode_skips_reconcile(app):
    app.cfg.paper_mode = True
    app.broker = StubBroker([StubLeg(occ(560), "Long", 2, 0.95)])
    asyncio.run(app._reconcile())
    assert app.governor.open_positions == []
    assert app.governor.breaker.allows_entries
