"""Order-book imbalance layer: math, thinning, gates, and backtest fit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from godmode0dte.config import SignalConfig
from godmode0dte.features.orderbook import BookPulse
from godmode0dte.models import Direction, Regime, RegimeState, VolRegime
from godmode0dte.scoring.engine import ScoreEngine
from tests.test_scoring_and_exits import make_inputs

UTC = timezone.utc
T0 = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)


def fed_pulse(pairs: list[tuple[float, float]]) -> BookPulse:
    p = BookPulse(ewma_alpha=0.5)
    for k, (b, a) in enumerate(pairs):
        p.update(b, a, T0 + timedelta(seconds=k))
    return p


def test_imbalance_sign_and_bounds():
    p = fed_pulse([(300, 100)] * 40)      # buyers stacked 3:1
    s = p.state()
    assert s.imbalance_raw == 0.5         # (300-100)/400
    assert 0 < s.imbalance <= 1
    p2 = fed_pulse([(100, 300)] * 40)
    assert p2.state().imbalance < 0       # the floor is fake


def test_thinning_detected_when_depth_collapses():
    pairs = [(200, 200)] * 60 + [(40, 40)] * 1   # depth 400 -> 80 (< 35% of median)
    s = fed_pulse(pairs).state()
    assert s.thinning
    assert not fed_pulse([(200, 200)] * 60).state().thinning


def test_conflicts_direction():
    p = fed_pulse([(100, 300)] * 40)      # sellers stacked
    assert p.conflicts(Direction.LONG, threshold=0.30)
    assert not p.conflicts(Direction.SHORT, threshold=0.30)


def test_book_conflict_gates_the_score():
    pulse = fed_pulse([(100, 300)] * 40)  # I = -0.5 against a long
    score = ScoreEngine(SignalConfig()).score(make_inputs(book=pulse.state()))
    assert not score.tradeable
    assert any("book stacked against" in g for g in score.hard_gate_failures)


def test_thinning_gates_the_score():
    pulse = fed_pulse([(220, 180)] * 60 + [(35, 30)] * 1)   # supportive I, vanishing depth
    score = ScoreEngine(SignalConfig()).score(make_inputs(book=pulse.state()))
    assert not score.tradeable
    assert any("liquidity thinning" in g for g in score.hard_gate_failures)


def test_supportive_book_carries_zero_points():
    """Governance: the book can veto but never add points (until calibrated)."""
    pulse = fed_pulse([(300, 100)] * 40)  # strongly supportive of the long
    with_book = ScoreEngine(SignalConfig()).score(make_inputs(book=pulse.state()))
    without = ScoreEngine(SignalConfig()).score(make_inputs(book=None))
    assert with_book.total == without.total


def test_logistic_slope_recovers_planted_edge():
    import numpy as np
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from backtest_imbalance import logistic_slope

    rng = np.random.default_rng(7)
    rows = []
    for _ in range(600):
        i = float(rng.uniform(-0.6, 0.6))
        up = rng.random() < 1 / (1 + np.exp(-(0.0 + 3.0 * i)))   # planted b = 3
        rows.append((i, 0.1 if up else -0.1, False))
    # independent draws: horizon 1 at 1-min spacing means L=0, so the HAC
    # sandwich reduces to Huber-White, which should sit close to the naive
    # model-based SE when the model is correctly specified
    b, z_naive, z_hac, n_eff = logistic_slope(rows, horizon_min=1)
    assert b > 1.5 and z_hac > 3.0
    assert abs(z_hac - z_naive) / z_naive < 0.10 and n_eff == 600
    # overlapping horizon (L=4) on the same iid rows: HAC z stays finite and
    # n_eff shrinks by the overlap factor
    _, _, z5, n_eff5 = logistic_slope(rows, horizon_min=5)
    assert n_eff5 == 120
