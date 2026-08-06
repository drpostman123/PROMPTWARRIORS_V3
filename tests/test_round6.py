"""Round 6: shadow outcome book and the survival-first drawdown rule."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone

import pytest

from godmode0dte.config import AppConfig, RiskConfig
from godmode0dte.models import Direction, Quote, VerticalSpec
from godmode0dte.monitoring.shadow import ShadowBook
from godmode0dte.risk.circuit_breaker import BreakerState, CircuitBreaker

UTC = timezone.utc
NOW = datetime(2026, 8, 4, 17, 0, tzinfo=UTC)


def vertical(direction=Direction.LONG) -> VerticalSpec:
    return VerticalSpec(underlying="SPY", direction=direction, expiration="2026-08-04",
                        long_strike=560.0, short_strike=562.0, width=2.0,
                        debit=0.75, contracts=1, long_symbol="L", short_symbol="S")


def q(mid: float) -> Quote:
    return Quote(symbol="X", bid=mid - 0.01, ask=mid + 0.01, bid_size=50, ask_size=50, ts=NOW)


def make_book(cfg) -> tuple[ShadowBook, list]:
    rows: list = []
    return ShadowBook(cfg, rows.append), rows


# ---- shadow book -----------------------------------------------------------

def test_shadow_opens_near_miss_and_exits_at_target(cfg):
    book, rows = make_book(cfg)
    book.maybe_open(88.0, vertical(), 0.75, NOW, "below_threshold")
    assert book.open_count == 1
    # Mark the spread at 1.30: >= min(1.65 x 0.75, 0.8 x 2) = 1.2375 -> target.
    book.mark_and_exit(NOW + timedelta(minutes=5), time(13, 0),
                       lambda s: q(1.90) if s == "L" else q(0.60))
    assert book.open_count == 0
    (row,) = rows
    assert row["kind"] == "shadow_exit" and row["reason"] == "profit_target"
    assert row["score"] == 88.0 and row["win"] is True
    assert row["fill_basis"] == "mid_optimistic"


def test_shadow_hard_stop_needs_two_marks_and_charges_fees(cfg):
    book, rows = make_book(cfg)
    book.maybe_open(90.0, vertical(), 0.75, NOW, "daily_trade_cap")
    getter = lambda s: q(0.60) if s == "L" else q(0.30)   # mark 0.30 <= 0.375 stop
    book.mark_and_exit(NOW + timedelta(minutes=1), time(13, 0), getter)
    assert book.open_count == 1                            # first mark arms
    book.mark_and_exit(NOW + timedelta(minutes=2), time(13, 0), getter)
    (row,) = rows
    assert row["reason"] == "hard_stop" and row["win"] is False
    # pnl includes friction: (0.30 - 0.75)*100 - friction
    assert row["pnl_net_per_contract"] == pytest.approx(
        -45.0 - cfg.execution.friction_per_contract)


def test_shadow_respects_score_floor_capacity_and_one_per_side(cfg):
    cfg.signal.shadow_max_open = 2
    book, _ = make_book(cfg)
    book.maybe_open(80.0, vertical(), 0.75, NOW, "below_threshold")    # under floor
    assert book.open_count == 0
    book.maybe_open(90.0, vertical(Direction.LONG), 0.75, NOW, "x")
    book.maybe_open(91.0, vertical(Direction.LONG), 0.75, NOW, "x")    # same side: no
    assert book.open_count == 1
    book.maybe_open(91.0, vertical(Direction.SHORT), 0.75, NOW, "x")
    assert book.open_count == 2


def test_shadow_disabled_by_config(cfg):
    cfg.signal.shadow_tracking = False
    book, _ = make_book(cfg)
    book.maybe_open(95.0, vertical(), 0.75, NOW, "x")
    assert book.open_count == 0


# ---- survival rule ---------------------------------------------------------

def make_breaker(tmp_path, day=date(2026, 8, 4)) -> CircuitBreaker:
    cfg = RiskConfig(lockout_file=str(tmp_path / "lockout.json"),
                     day_trade_file=str(tmp_path / "dt.json"))
    return CircuitBreaker(cfg, day)


def test_survival_trips_at_20pct_from_peak_not_daily(tmp_path):
    b = make_breaker(tmp_path)
    b.set_starting_equity(10_000)
    b.check(12_000)                                   # new peak
    assert b.state is BreakerState.ARMED
    # 12,000 -> 9,700: only -3% on the DAY (10k start) but -19.2% from peak.
    assert b.check(9_700) is BreakerState.ARMED
    # -20% from peak trips even though the daily limit never fired.
    assert b.check(9_600) is BreakerState.TRIPPED
    assert "SURVIVAL" in b.trip_reason


def test_survival_lock_survives_new_sessions_until_acknowledged(tmp_path, monkeypatch):
    b = make_breaker(tmp_path)
    b.set_starting_equity(10_000)
    b.check(12_000)
    b.check(9_500)                                    # trips survival
    # NEXT DAY: an ordinary lockout would have expired — survival must not.
    monkeypatch.delenv("GODMODE_ACK_DRAWDOWN", raising=False)
    b2 = make_breaker(tmp_path, day=date(2026, 8, 5))
    assert b2.state is BreakerState.LOCKED
    assert "survival lock" in b2.trip_reason
    # Operator acknowledges after review: re-arms, keeps the peak.
    monkeypatch.setenv("GODMODE_ACK_DRAWDOWN", "YES")
    b3 = make_breaker(tmp_path, day=date(2026, 8, 6))
    assert b3.state is BreakerState.ARMED
    payload = json.loads((tmp_path / "peak.json").read_text())
    assert payload["peak"] == 12_000 and not payload.get("survival_tripped")
