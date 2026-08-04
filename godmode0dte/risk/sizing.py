"""Equity-scaled position sizing. No fixed dollar risk anywhere.

risk_dollars = equity * min(ladder(score), 1.0) * max_trade_risk_pct
contracts    = floor(risk_dollars / (debit * 100))

The ladder only scales *within* the per-trade cap — a 100-score setup
still risks at most 4% of current equity.
"""

from __future__ import annotations

import math

from godmode0dte.config import RiskConfig


def ladder_multiplier(score: float, ladder: dict[int, float]) -> float:
    """Highest ladder band whose lower bound the score meets; 0 below all bands."""
    mult = 0.0
    for band, m in sorted(ladder.items()):
        if score >= band:
            mult = m
    return mult


def size_trade(
    score: float,
    equity: float,
    debit_per_share: float,
    contract_multiplier: int,
    cfg: RiskConfig,
) -> tuple[int, float]:
    """Return (contracts, risk_dollars) for a debit vertical.

    Risk per contract on a debit vertical is exactly the debit paid.
    Returns (0, 0.0) when even one contract would breach the per-trade cap.
    """
    if equity <= 0 or debit_per_share <= 0:
        return 0, 0.0
    mult = ladder_multiplier(score, cfg.sizing_ladder)
    if mult <= 0:
        return 0, 0.0
    cap_dollars = equity * (cfg.max_trade_risk_pct / 100.0)
    target_dollars = cap_dollars * mult
    per_contract = debit_per_share * contract_multiplier
    contracts = math.floor(target_dollars / per_contract)
    if contracts < 1:
        return 0, 0.0
    risk = contracts * per_contract
    # Belt and suspenders: never exceed the hard cap even on rounding bugs.
    assert risk <= cap_dollars + 1e-6, "sizing exceeded per-trade cap"
    return contracts, risk
