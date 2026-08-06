#!/usr/bin/env python3
"""Monte-Carlo proof of the -6% day law — through the REAL RiskGovernor.

Round-5 requirement: the worst-case-day gate must hold under sequential,
correlated losses (the all-lose path IS maximum correlation), one-lot
granularity, soft-tier halving, and fee drag — proven by executing the
actual approval code path over simulated days, never by re-modeling it.

Each simulated day:
  - random starting equity in [2,000, 30,000] (the small-account band)
  - up to 6 intent attempts (score 93-100, legal debit band 0.30-0.42W)
  - every approved trade FILLS AT THE WORST PERMITTED PRICE (cap_price)
    and then LOSES 100% of the debit plus fees (max-loss expiry) with
    probability (1 - p); winners exit at the profit target
  - equity updates flow back through governor.update_equity so the
    breaker, soft tier, and worst-case-day gate see the real path

Assertion per day: realized cash loss <= 6% of starting equity + one
fee increment of slack (the gate bounds risk INCLUDING fees; slack covers
float rounding only). One violation fails the run.

Usage: python scripts/mc_risk_proof.py [--days 10000] [--p 0.0] [--seed 7]
       (--p 0.0 = pure worst case; raise it to see realistic paths)
"""

from __future__ import annotations

import argparse
import random
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from godmode0dte.config import AppConfig                      # noqa: E402
from godmode0dte.models import (                              # noqa: E402
    Direction, Position, ScoreComponent, SetupScore, TradeIntent, VerticalSpec,
)
from godmode0dte.risk.circuit_breaker import CircuitBreaker   # noqa: E402
from godmode0dte.risk.governor import ApprovedTrade, RiskGovernor  # noqa: E402

UTC = timezone.utc


def make_intent(score: float, debit: float, contracts: int = 50) -> TradeIntent:
    now = datetime.now(UTC)
    return TradeIntent(
        score=SetupScore(total=score, direction=Direction.LONG,
                         components=(ScoreComponent("all", score, 100),), ts=now),
        vertical=VerticalSpec(underlying="SPY", direction=Direction.LONG,
                              expiration="2026-08-04", long_strike=560.0,
                              short_strike=562.0, width=2.0, debit=debit,
                              contracts=contracts, long_symbol="L", short_symbol="S"),
        ts=now, quote_ts=now)


def run(days: int, p_win: float, seed: int, verbose: bool = True) -> tuple[int, float]:
    """Return (violations, worst observed day loss pct). Must be (0, <= 6.0x)."""
    rng = random.Random(seed)
    cfg = AppConfig()
    cfg.risk.min_intent_spacing_sec = 0
    cfg.risk.min_equity = 1_500.0          # exercise the deep small-account band too
    from datetime import time as dtime
    cfg.signal.entry_window_start = dtime(0, 0)
    cfg.signal.entry_window_end = dtime(23, 59, 59)
    friction = cfg.execution.friction_per_contract

    violations = 0
    worst_pct = 0.0
    tmp = Path(tempfile.mkdtemp(prefix="mcproof-"))
    for day in range(days):
        cfg.risk.lockout_file = str(tmp / f"d{day}" / "lock.json")
        cfg.risk.day_trade_file = str(tmp / f"d{day}" / "dt.json")
        start_eq = rng.uniform(2_000.0, 30_000.0)
        gov = RiskGovernor(cfg, CircuitBreaker(cfg.risk, date(2026, 8, 4)))
        gov.update_equity(start_eq, datetime.now(UTC))
        equity = start_eq
        realized_loss = 0.0
        for attempt in range(6):
            debit = round(rng.uniform(0.60, 0.84), 2)
            result = gov.evaluate(make_intent(rng.uniform(93.0, 100.0), debit))
            if not isinstance(result, ApprovedTrade):
                continue
            # Fill at the worst permitted price; register at that basis.
            fill = result.cap_price
            pos = Position(trade_id=result.trade_id, vertical=result.vertical,
                           entry_debit=fill, entry_ts=datetime.now(UTC),
                           score_at_entry=result.score, or_mid=560.0,
                           current_value=fill, mark_ts=datetime.now(UTC))
            gov.register_position(pos)
            win = rng.random() < p_win
            n = result.vertical.contracts
            if win:
                exit_val = min(1.65 * fill, 0.80 * result.vertical.width)
                pnl = (exit_val - fill) * n * 100 - friction * n
            else:
                pnl = -fill * n * 100 - friction * n     # max loss + fees
                realized_loss += -pnl
            equity += pnl
            gov.update_position(Position(**{**pos.__dict__, "exit_ts": datetime.now(UTC),
                                            "exit_value": max(0.0, fill + pnl / (n * 100)),
                                            "exit_reason": "sim"}))
            gov.update_equity(equity, datetime.now(UTC))
        day_loss_pct = 100.0 * max(0.0, start_eq - equity) / start_eq
        worst_pct = max(worst_pct, day_loss_pct)
        if day_loss_pct > 6.0 + 100.0 * (2 * friction) / start_eq + 1e-6:
            violations += 1
            if verbose:
                print(f"VIOLATION day {day}: start {start_eq:.0f}, loss {day_loss_pct:.2f}%")
    return violations, worst_pct


def main() -> None:
    import logging
    logging.disable(logging.INFO)      # the proof needs verdicts, not 2000 days of approvals
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=10_000)
    ap.add_argument("--p", type=float, default=0.0, help="win probability (0 = pure worst case)")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    violations, worst = run(args.days, args.p, args.seed)
    print(f"days={args.days}  p_win={args.p}  violations={violations}  "
          f"worst day loss={worst:.2f}% (law: <= 6% + fee slack)")
    if violations:
        raise SystemExit("THE -6% LAW WAS VIOLATED — do not deploy")
    print("PROOF HOLDS: the worst-case-day gate bounded every simulated path.")


if __name__ == "__main__":
    main()
