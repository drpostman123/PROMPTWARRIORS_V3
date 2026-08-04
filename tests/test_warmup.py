"""Indicator warmup for first-15-minute entries: prior-session/premarket
candles calibrate ATR/EMA/regime at the bell."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from godmode0dte.data.market_data import in_warmup_window, prior_session_date
from godmode0dte.models import Regime
from godmode0dte.regime.rules import RuleBasedRegime
from tests.test_audit_round2 import _trend_bars

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def test_prior_session_date_skips_weekends():
    assert prior_session_date(date(2026, 8, 4)) == date(2026, 8, 3)    # Tue -> Mon
    assert prior_session_date(date(2026, 8, 3)) == date(2026, 7, 31)   # Mon -> Fri
    assert prior_session_date(date(2026, 8, 8)) == date(2026, 8, 7)    # Sat -> Fri


def et(d: date, h: int, m: int) -> datetime:
    return datetime.combine(d, time(h, m), tzinfo=ET).astimezone(UTC)


def test_warmup_window_rth_and_premarket_only():
    today = date(2026, 8, 4)
    prior = date(2026, 8, 3)
    assert in_warmup_window(et(prior, 10, 0), today, ET)        # prior RTH
    assert not in_warmup_window(et(prior, 18, 0), today, ET)    # prior after-hours
    assert in_warmup_window(et(today, 8, 0), today, ET)         # today premarket
    assert not in_warmup_window(et(today, 9, 30), today, ET)    # today session: not warmup
    assert not in_warmup_window(et(today, 3, 0), today, ET)     # overnight


def test_warm_context_arms_regime_at_the_bell():
    """20 warm trending bars + a single session bar: regime is TREND at 09:35
    instead of UNKNOWN until ~10:45 — the whole point of the warmup."""
    warm = _trend_bars(20, 0.5)
    session = _trend_bars(21, 0.5)[-1:]              # one fresh bar continuing the trend
    state = RuleBasedRegime().classify(warm + session, vix=17.0)
    assert state.regime is Regime.TREND_UP
    assert state.confidence >= 0.85


def test_empty_warmup_degrades_to_unknown():
    session = _trend_bars(3, 0.5)                    # 09:45, no warmup available
    state = RuleBasedRegime().classify(session, vix=17.0)
    assert state.regime is Regime.UNKNOWN            # gate holds; entries wait