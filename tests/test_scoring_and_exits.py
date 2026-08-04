from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest

from godmode0dte.config import ExitConfig, SignalConfig
from godmode0dte.data.calendar import EventVerdict
from godmode0dte.models import (
    Bar, Direction, Quote, Regime, RegimeState, VolRegime,
)
from godmode0dte.scoring.engine import ScoreEngine, ScoringInputs

UTC = timezone.utc


def make_bars(n: int = 120) -> list[Bar]:
    """Orderly uptrend with pullbacks — RSI stays in-band, EMAs aligned."""
    bars = []
    px = 560.0
    t0 = datetime(2026, 8, 4, 13, 30, tzinfo=UTC)
    for i in range(n):
        o = px
        px += 0.05 if i % 10 < 7 else -0.07   # pullbacks at 1m AND 5m scale
        bars.append(Bar(ts=t0 + timedelta(minutes=i), open=o, high=max(o, px) + 0.05,
                        low=min(o, px) - 0.05, close=px, volume=10_000))
    return bars


def leg_quote(mid: float, spread: float = 0.04) -> Quote:
    return Quote(symbol="X", bid=mid - spread / 2, ask=mid + spread / 2,
                 bid_size=50, ask_size=50, ts=datetime.now(UTC))


def make_inputs(**over) -> ScoringInputs:
    base = dict(
        bars_1m=make_bars(),
        direction=Direction.LONG,
        breakout_evidence={"clv": 1.0},
        or_width_ok=True,
        or_width_reason="ok",
        rel_volume=2.0,
        regime=RegimeState(Regime.TREND_UP, VolRegime.NORMAL, confidence=0.95),
        macro_points=10.0,
        macro_detail="fully aligned",
        event=EventVerdict(False, "clear", points=5),
        vix=17.0,
        long_leg_quote=leg_quote(2.0, spread=0.01),
        short_leg_quote=leg_quote(1.0, spread=0.01),
        max_leg_spread_pct=6.0,
        min_open_interest_ok=True,
        now=datetime(2026, 8, 4, 14, 5, tzinfo=UTC),  # a Tuesday
    )
    base.update(over)
    return ScoringInputs(**base)


def test_perfect_setup_clears_93():
    score = ScoreEngine(SignalConfig()).score(make_inputs())
    assert score.tradeable
    assert score.total >= 93
    assert abs(sum(c.max_points for c in score.components) - 100) < 1e-9


def test_merely_good_setup_stays_below_93():
    """Selectivity: a good-but-imperfect setup must NOT clear the bar."""
    score = ScoreEngine(SignalConfig()).score(make_inputs(
        breakout_evidence={"clv": 0.8},
        rel_volume=1.4,
        regime=RegimeState(Regime.TREND_UP, VolRegime.NORMAL, confidence=0.70),
        macro_points=5.0,
        long_leg_quote=leg_quote(2.0, spread=0.06),
        short_leg_quote=leg_quote(1.0, spread=0.06),
    ))
    assert score.tradeable          # no gates broken...
    assert score.total < 93         # ...but not high-conviction either


def test_event_blackout_is_hard_gate():
    score = ScoreEngine(SignalConfig()).score(
        make_inputs(event=EventVerdict(True, "CPI blackout", points=0))
    )
    assert not score.tradeable and score.total == 0.0


def test_counter_regime_signal_gated():
    score = ScoreEngine(SignalConfig()).score(
        make_inputs(regime=RegimeState(Regime.TREND_DOWN, VolRegime.NORMAL, confidence=0.9))
    )
    assert not score.tradeable


def test_extreme_vol_gated():
    score = ScoreEngine(SignalConfig()).score(
        make_inputs(regime=RegimeState(Regime.TREND_UP, VolRegime.EXTREME, confidence=0.9),
                    vix=40.0)
    )
    assert not score.tradeable


def test_wide_leg_spread_zeroes_microstructure():
    score = ScoreEngine(SignalConfig()).score(
        make_inputs(long_leg_quote=leg_quote(2.0, spread=0.5))
    )
    micro = next(c for c in score.components if c.name == "microstructure")
    assert micro.points == 0


def test_score_breakdown_is_transparent():
    score = ScoreEngine(SignalConfig()).score(make_inputs())
    names = {c.name for c in score.components}
    assert names == {
        "opening_range", "breakout_confirmation", "mtf_alignment", "regime",
        "macro_cluster", "event_sentiment", "dow_vix_preference", "microstructure",
    }
    assert score.total == round(sum(c.points for c in score.components), 2)


def test_profit_target_capped_by_width():
    """P4a: target = min(1.65 x debit, 0.80 x width)."""
    cfg = ExitConfig()
    # Cheap debit: 1.65x binds. 0.60 debit on 2-wide -> target 0.99 < 1.60.
    assert min(0.60 * cfg.profit_target_mult, 2.0 * cfg.profit_target_width_frac) == pytest.approx(0.99)
    # Rich debit: width cap binds. 1.05 debit on 2-wide -> 1.7325 > 1.60 -> 1.60.
    assert min(1.05 * cfg.profit_target_mult, 2.0 * cfg.profit_target_width_frac) == pytest.approx(1.60)
