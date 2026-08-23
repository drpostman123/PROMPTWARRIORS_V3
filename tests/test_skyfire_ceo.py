"""GreedyCeo: regime detection, greedy scoring, allocation shapes,
intent verdicts, entry-signal math."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from skyfire_sol.ceo.greedy import GreedyCeo
from skyfire_sol.config import AppConfig
from skyfire_sol.meme import entry as entry_engine
from skyfire_sol.models import (
    IntentKind,
    RegimeState,
    Side,
    SleeveId,
    SleevePerf,
    TradeIntent,
    TokenFacts,
    Urgency,
)

from test_skyfire_gate_order import snapshot  # reuse the snapshot builder

NOW = datetime.now(timezone.utc)


def ceo_with_trend(up: bool) -> GreedyCeo:
    ceo = GreedyCeo(AppConfig())
    base = 100.0
    for h in range(60):
        px = base + (h * 0.5 if up else -h * 0.5)
        ceo.on_sol_mark(px, NOW - timedelta(hours=60 - h))
    return ceo


def perf(**scores) -> list[SleevePerf]:
    return [SleevePerf(SleeveId(k), 1000.0, ret_24h_pct=v, sharpe_7d=v / 10,
                       warmup=False) for k, v in scores.items()]


def test_regime_risk_on_needs_trend_and_breadth():
    ceo = ceo_with_trend(up=True)
    assert ceo.read_regime(0.7, 1e6).state is RegimeState.RISK_ON
    assert ceo.read_regime(0.4, 1e6).state is RegimeState.CHOPPY
    assert ceo.read_regime(0.2, 1e6).state is RegimeState.RISK_OFF


def test_regime_downtrend_is_risk_off_regardless_of_breadth():
    ceo = ceo_with_trend(up=False)
    assert ceo.read_regime(0.9, 1e6).state is RegimeState.RISK_OFF


def test_regime_unknown_without_history():
    ceo = GreedyCeo(AppConfig())
    assert ceo.read_regime(0.9, 1e6).state is RegimeState.UNKNOWN


def test_rich_funding_demotes_risk_on():
    ceo = ceo_with_trend(up=True)
    r = ceo.read_regime(0.7, 1e6, funding_rate_pct_hr=0.09)
    assert r.state is RegimeState.CHOPPY


def test_allocate_risk_on_presses_winner():
    ceo = ceo_with_trend(up=True)
    regime = ceo.read_regime(0.7, 1e6)
    targets = ceo.allocate(
        perf(MEME_ROTATION=8.0, CORE_HOLD=0.5, YIELD=0.2, PERPS=1.0), regime)
    assert targets.regime is RegimeState.RISK_ON
    boot = AppConfig().sleeves.boot_allocations["MEME_ROTATION"]
    assert targets.targets["MEME_ROTATION"] > boot      # pressed above boot
    assert sum(targets.targets.values()) == pytest.approx(100.0)
    assert "pressing MEME_ROTATION" in targets.reasoning


def test_allocate_risk_off_collapses_to_core():
    ceo = ceo_with_trend(up=False)
    regime = ceo.read_regime(0.2, 1e6)
    targets = ceo.allocate(perf(MEME_ROTATION=8.0, CORE_HOLD=0.5,
                                YIELD=0.2, PERPS=1.0), regime)
    assert targets.targets["CORE_HOLD"] + targets.targets["YIELD"] > 80.0
    assert targets.targets["MEME_ROTATION"] <= 10.0


def test_sharpe_7d_math():
    assert GreedyCeo.sharpe_7d([]) is None
    assert GreedyCeo.sharpe_7d([100, 100, 100]) == 0.0
    up = GreedyCeo.sharpe_7d([100, 102, 104, 107])
    down = GreedyCeo.sharpe_7d([100, 98, 95, 93])
    assert up > 0 > down


def _intent(kind=IntentKind.ENTRY) -> TradeIntent:
    return TradeIntent("i1", SleeveId.MEME_ROTATION, kind, Side.BUY, "M",
                       "USDC", 100.0, Urgency.NORMAL, "t", NOW)


def test_judge_intent_by_regime():
    ceo = GreedyCeo(AppConfig())
    assert ceo.judge_intent(_intent(), snapshot(regime=RegimeState.RISK_ON)).size_mult == 1.0
    v = ceo.judge_intent(_intent(), snapshot(regime=RegimeState.CHOPPY))
    assert v.action == "resize" and v.size_mult == 0.75
    v = ceo.judge_intent(_intent(), snapshot(regime=RegimeState.RISK_OFF))
    assert v.action == "veto" and v.size_mult == 0.0
    v = ceo.judge_intent(_intent(), snapshot(regime=RegimeState.UNKNOWN))
    assert v.size_mult == 0.5
    # exits pass through untouched whatever the regime
    v = ceo.judge_intent(_intent(IntentKind.EXIT),
                         snapshot(regime=RegimeState.RISK_OFF))
    assert v.action == "approve" and v.size_mult == 1.0


# -- entry signal math ----------------------------------------------------

def _facts(**over) -> TokenFacts:
    base = dict(mint="M", symbol="T", price_usd=1.0, liquidity_usd=500_000.0,
                volume_1h_usd=60_000.0, volume_6h_usd=120_000.0,
                price_change_1h_pct=5.0, holder_count=110, holder_count_prev=100)
    base.update(over)
    return TokenFacts(**base)


def test_entry_signal_scores_and_vetoes():
    from skyfire_sol.config import MemeConfig
    cfg = MemeConfig()
    sig = entry_engine.score(_facts(), cfg)
    assert sig is not None and sig.vol_accel == pytest.approx(3.0)
    # weak acceleration fails
    assert entry_engine.score(_facts(volume_1h_usd=20_000.0), cfg) is None
    # shrinking holders fails
    assert entry_engine.score(_facts(holder_count=90), cfg) is None
    # negative 1h momo (below EMA proxy) fails
    assert entry_engine.score(_facts(price_change_1h_pct=-2.0), cfg) is None
    # blowoff veto with unknown ATR: >50% single candle
    assert entry_engine.score(_facts(price_change_1h_pct=80.0), cfg) is None
    # top-N picker respects open slots
    sigs = [entry_engine.score(_facts(mint=f"m{i}", volume_1h_usd=60_000 + i), cfg)
            for i in range(8)]
    top = entry_engine.pick_top([s for s in sigs if s], open_count=3, cfg=cfg)
    assert len(top) == 2                                # 5 max - 3 open
    assert top[0].score >= top[1].score
