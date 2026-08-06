from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from godmode0dte.config import AppConfig
from godmode0dte.models import Direction, ScoreComponent, SetupScore, TradeIntent, VerticalSpec
from godmode0dte.risk.circuit_breaker import CircuitBreaker
from godmode0dte.risk.governor import RiskGovernor


@pytest.fixture
def cfg(tmp_path) -> AppConfig:
    c = AppConfig()
    c.risk.lockout_file = str(tmp_path / "lockout.json")
    c.risk.day_trade_file = str(tmp_path / "day_trades.json")
    c.risk.account_type = "margin_large"    # legacy tests assume no day-trade budget
    c.risk.daily_trade_cap = 100            # legacy tests approve many times per session
    c.risk.min_intent_spacing_sec = 0
    return c


@pytest.fixture
def governor(cfg) -> RiskGovernor:
    gov = RiskGovernor(cfg, CircuitBreaker(cfg.risk, date(2026, 8, 4)))
    gov.update_equity(100_000.0, datetime.now(timezone.utc))
    return gov


def make_intent(score: float = 95.0, debit: float = 1.0, contracts: int = 100,
                direction: Direction = Direction.LONG) -> TradeIntent:
    now = datetime.now(timezone.utc)
    return TradeIntent(
        score=SetupScore(total=score, direction=direction,
                         components=(ScoreComponent("all", score, 100),), ts=now),
        vertical=VerticalSpec(
            underlying="SPY", direction=direction, expiration="2026-08-04",
            long_strike=560.0, short_strike=562.0, width=2.0, debit=debit,
            contracts=contracts, long_symbol="L", short_symbol="S",
        ),
        ts=now,
        quote_ts=now,
    )
