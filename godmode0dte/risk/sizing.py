"""Equity-scaled position sizing. No fixed dollar risk anywhere.

risk_dollars = contracts x (worst-fill price x 100 + round-trip friction)
contracts    = floor(ladder_target / per_contract_risk)

Friction is INSIDE the sized risk (round-4 audit): at small equity,
granularity puts the account exactly on the cap, and a cap-sized loss plus
$2.60 of fees would otherwise exceed 4% in cash terms.

Small-account minimum unit: when the ladder's fraction (e.g. Phase-A 2%)
cannot fit one contract but the HARD 4% cap can — including friction — one
contract is permitted and the overshoot is logged. The ladder expresses
INTENT; the 4% cap is law; the one-lot rule bends intent, never law.
"""

from __future__ import annotations

import math

from godmode0dte.config import RiskConfig
from godmode0dte.monitoring.logging import get_logger

log = get_logger("sizing")


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
    friction_per_contract: float = 0.0,
) -> tuple[int, float]:
    """Return (contracts, risk_dollars) for a debit vertical.

    ``debit_per_share`` is the WORST permitted fill (governor cap_price).
    Risk per contract = that price x multiplier + round-trip friction.
    Returns (0, 0.0) when even one contract would breach the 4% hard cap.
    """
    if equity <= 0 or debit_per_share <= 0:
        return 0, 0.0
    mult = ladder_multiplier(score, cfg.sizing_ladder)
    if mult <= 0:
        return 0, 0.0
    cap_dollars = equity * (cfg.max_trade_risk_pct / 100.0)
    target_dollars = cap_dollars * mult
    per_contract = debit_per_share * contract_multiplier + friction_per_contract
    contracts = math.floor(target_dollars / per_contract)
    if contracts < 1:
        if cfg.allow_one_lot_minimum and per_contract <= cap_dollars:
            # One contract fits under the HARD cap even though the ladder
            # target can't hold it. Take it, and say so.
            log.warning("min_contract_overshoot",
                        ladder_target=round(target_dollars, 2),
                        risk=round(per_contract, 2), hard_cap=round(cap_dollars, 2))
            contracts = 1
        else:
            return 0, 0.0
    risk = contracts * per_contract
    # Belt and suspenders: never exceed the hard cap even on rounding bugs.
    assert risk <= cap_dollars + 1e-6, "sizing exceeded per-trade cap"
    return contracts, risk
