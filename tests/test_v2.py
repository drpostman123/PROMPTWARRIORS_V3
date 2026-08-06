"""v2 (high-conviction aggressive) — the laws that keep aggressive survivable."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError

from godmode0dte.models import Direction
from godmode0dte_v2.config import V2Config, V2SizingConfig
from godmode0dte_v2.conviction import MetricSnapshot, PerfectSetupDetector
from godmode0dte_v2.executor import V2ExitEngine, V2Position
from godmode0dte_v2.risk import V2Risk

UTC = timezone.utc
NOW = datetime(2026, 8, 4, 17, 0, tzinfo=UTC)


def v2cfg(tmp_path, **sizing) -> V2Config:
    c = V2Config()
    c.sizing.lockout_file = str(tmp_path / "v2lock.json")
    for k, v in sizing.items():
        setattr(c.sizing, k, v)
    return c


def snap(**over) -> MetricSnapshot:
    base = dict(rsi_2m=90.0, rsi_5m=80.0, vwap_stretch_atr=3.0, day_move_pct=1.2,
                book_imbalance=-0.30, rel_volume=2.5, vix=18.0, minutes_since_open=45.0)
    base.update(over)
    return MetricSnapshot(**base)


# ---- config coherence ------------------------------------------------------

def test_per_trade_cannot_exceed_daily_limit():
    with pytest.raises(ValidationError):
        V2SizingConfig(conviction_risk_pct=30.0, daily_loss_limit_pct=25.0)


def test_ceilings_hold():
    with pytest.raises(ValidationError):
        V2SizingConfig(conviction_risk_pct=50.0, daily_loss_limit_pct=40.0)


# ---- conviction detector ---------------------------------------------------

def test_perfect_overbought_fires_puts(tmp_path):
    det = PerfectSetupDetector(v2cfg(tmp_path))
    sig = det.evaluate(snap(), NOW)
    assert sig is not None and sig.direction is Direction.SHORT and sig.perfect


def test_one_failed_condition_means_not_perfect(tmp_path):
    det = PerfectSetupDetector(v2cfg(tmp_path))
    assert det.evaluate(snap(rel_volume=1.5), NOW) is None       # volume climax missing


def test_missing_data_never_counts_toward_perfect(tmp_path):
    det = PerfectSetupDetector(v2cfg(tmp_path))
    assert det.evaluate(snap(book_imbalance=None), NOW) is None


def test_cooldown_silences_a_side(tmp_path):
    det = PerfectSetupDetector(v2cfg(tmp_path))
    assert det.evaluate(snap(), NOW) is not None
    assert det.evaluate(snap(), NOW + timedelta(minutes=5)) is None
    assert det.evaluate(snap(), NOW + timedelta(minutes=25)) is not None


# ---- sizing law ------------------------------------------------------------

def test_conviction_size_is_heavy_but_lawful(tmp_path):
    r = V2Risk(v2cfg(tmp_path), date(2026, 8, 4))
    r.update_equity(3_000.0)
    contracts, premium, detail = r.size_premium(option_mid=2.00, friction_per_contract=1.30)
    # 20% of 3000 = $600 budget; $201.30/contract -> 2 contracts, $402.60 = 13.4%
    assert contracts == 2
    assert premium <= 3_000.0 * 0.20
    assert "at risk" in detail


def test_size_clamped_to_remaining_daily_headroom(tmp_path):
    r = V2Risk(v2cfg(tmp_path), date(2026, 8, 4))
    r.update_equity(3_000.0)
    r.update_equity(2_500.0)          # -16.7% day; headroom = 25% - 16.7% = ~$250
    contracts, premium, _ = r.size_premium(2.00, 1.30)
    assert contracts == 1 and premium <= 251.0
    r.update_equity(2_260.0)          # -24.7%; headroom ~$10 -> nothing fits
    assert r.size_premium(2.00, 1.30)[0] == 0


def test_daily_limit_locks_and_persists(tmp_path):
    cfg = v2cfg(tmp_path)
    r = V2Risk(cfg, date(2026, 8, 4))
    r.update_equity(3_000.0)
    r.update_equity(2_200.0)          # -26.7% >= 25% -> lock
    assert r.locked
    r2 = V2Risk(cfg, date(2026, 8, 4))     # restart same day: still locked
    assert r2.locked
    r3 = V2Risk(cfg, date(2026, 8, 5))     # next day: fresh
    assert not r3.locked


def test_max_trades_per_day(tmp_path):
    r = V2Risk(v2cfg(tmp_path), date(2026, 8, 4))
    r.update_equity(10_000.0)
    r.record_trade(); r.record_trade()
    assert r.size_premium(2.00, 1.30)[0] == 0


# ---- exit engine: sell into strength ---------------------------------------

def pos(entry=2.00, n=4) -> V2Position:
    return V2Position(trade_id="t", symbol="S", streamer_symbol="S",
                      direction=Direction.SHORT, entry_premium=entry,
                      contracts_open=n, contracts_initial=n,
                      entry_ts=NOW, high_water=entry)


def test_scale_out_ladder_then_trail(tmp_path):
    e = V2ExitEngine(v2cfg(tmp_path))
    p = pos()
    d = e.evaluate(p, 4.10, NOW + timedelta(minutes=5), time(13, 0), locked=False)
    assert d.reason == "scale_out_2x" and d.contracts == 2          # 50% at 2x
    p.contracts_open -= d.contracts; p.scale_outs_done += 1
    d = e.evaluate(p, 8.20, NOW + timedelta(minutes=8), time(13, 0), locked=False)
    assert d.reason == "scale_out_4x" and d.contracts == 1          # 25% at 4x
    p.contracts_open -= d.contracts; p.scale_outs_done += 1
    # Runner: high-water 8.20, trail 40% -> exit at <= 4.92.
    assert e.evaluate(p, 6.00, NOW + timedelta(minutes=9), time(13, 0), False) is None
    d = e.evaluate(p, 4.80, NOW + timedelta(minutes=10), time(13, 0), False)
    assert d.reason == "runner_trail" and d.contracts == 1


def test_hard_stop_two_marks_and_lock_flatten_preempts(tmp_path):
    e = V2ExitEngine(v2cfg(tmp_path))
    p = pos()
    assert e.evaluate(p, 0.90, NOW, time(13, 0), locked=False) is None      # armed
    d = e.evaluate(p, 0.90, NOW, time(13, 0), locked=False)
    assert d.reason == "hard_stop" and d.contracts == 4 and d.urgent
    winner = pos()
    d = e.evaluate(winner, 9.99, NOW, time(13, 0), locked=True)
    assert d.reason == "lock_flatten" and d.urgent                          # lock beats a 5x winner


def test_max_hold_and_force_flat(tmp_path):
    e = V2ExitEngine(v2cfg(tmp_path))
    assert e.evaluate(pos(), 2.10, NOW + timedelta(minutes=61), time(13, 0),
                      False).reason == "max_hold"
    assert e.evaluate(pos(), 2.10, NOW, time(15, 31), False).reason == "force_flat"
