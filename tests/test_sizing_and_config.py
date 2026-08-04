from __future__ import annotations

import pytest
from pydantic import ValidationError

from godmode0dte.config import AppConfig, RiskConfig, ScoreWeights
from godmode0dte.risk.sizing import ladder_multiplier, size_trade


def test_config_cannot_loosen_hard_caps():
    with pytest.raises(ValidationError):
        RiskConfig(max_trade_risk_pct=5.0)
    with pytest.raises(ValidationError):
        RiskConfig(max_heat_pct=8.0)
    with pytest.raises(ValidationError):
        RiskConfig(max_concurrent_positions=3)
    with pytest.raises(ValidationError):
        RiskConfig(daily_loss_limit_pct=7.0)


def test_config_can_tighten():
    c = RiskConfig(max_trade_risk_pct=2.0, max_heat_pct=4.0,
                   max_concurrent_positions=1, daily_loss_limit_pct=3.0)
    assert c.max_trade_risk_pct == 2.0


def test_weights_must_sum_100():
    with pytest.raises(ValidationError):
        ScoreWeights(opening_range=50)


def test_live_mode_requires_env(monkeypatch):
    monkeypatch.delenv("GODMODE_CONFIRM_LIVE", raising=False)
    with pytest.raises(ValidationError):
        AppConfig(paper_mode=False)
    monkeypatch.setenv("GODMODE_CONFIRM_LIVE", "YES")
    assert AppConfig(paper_mode=False).paper_mode is False


def test_ladder_multiplier():
    ladder = {93: 0.5, 95: 0.75, 97: 1.0}
    assert ladder_multiplier(92.9, ladder) == 0.0
    assert ladder_multiplier(93.0, ladder) == 0.5
    assert ladder_multiplier(96.9, ladder) == 0.75
    assert ladder_multiplier(100.0, ladder) == 1.0


def test_size_trade_respects_cap():
    cfg = RiskConfig()
    contracts, risk = size_trade(score=100.0, equity=50_000, debit_per_share=1.25,
                                 contract_multiplier=100, cfg=cfg)
    assert risk <= 50_000 * 0.04
    assert contracts == int(50_000 * 0.04 // 125)


def test_size_trade_zero_when_too_small():
    cfg = RiskConfig()
    contracts, risk = size_trade(score=93.0, equity=1_000, debit_per_share=5.0,
                                 contract_multiplier=100, cfg=cfg)
    assert contracts == 0 and risk == 0.0
