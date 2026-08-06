#!/usr/bin/env python3
"""Phase-B promotion gate: pre-registered sequential test (round 5).

Replaces repeated-peek threshold checking with two jointly required,
peeking-safe adjudicators over the logged trade sequence:

1. Wald SPRT on the win indicator sequence:
       H0: p = p_be           (the edge only pays the bills)
       H1: p = p_be + delta   (the edge is worth sizing up)
   log-LR accumulates per trade; boundaries A = log((1-beta)/alpha),
   B = log(beta/(1-alpha)). Crossing A accepts H1; crossing B accepts H0.
   The SPRT is valid under continuous monitoring — no peeking penalty.

2. Beta-Bernoulli posterior with a flat prior Beta(1,1):
       require  P(p > p_be + margin | data) >= posterior_bar

PRE-REGISTERED PARAMETERS (changing them after looking at the data is
p-hacking; they are fixed here and in DESIGN_SPEC):
    alpha = 0.05, beta = 0.20, delta = 0.07, margin = 0.03,
    posterior_bar = 0.95, minimum n = 60 (power floor: below this even a
    true p1 edge rarely crosses A, so PROMOTE is unreachable — by design).

p_be is computed from the SAME logs (after-fee, managed exits) via
edge_report.breakeven_win_rate — the gate tests the edge against the
system's own realized cost structure, not a hoped-for one.

VERDICTS: PROMOTE (both pass) | REJECT (SPRT accepted H0 — the edge is
not there; stop trading it, do not "collect more data" past this) |
CONTINUE (still between boundaries).

Usage: python scripts/phase_gate.py [--log state/trades.jsonl]
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edge_report import breakeven_win_rate, load_rows      # noqa: E402

# --- pre-registered (see docstring; do not tune post hoc) ---
ALPHA = 0.05
BETA = 0.20
DELTA = 0.07
MARGIN = 0.03
POSTERIOR_BAR = 0.95
MIN_N = 60


def sprt_llr(wins: list[bool], p0: float, p1: float) -> tuple[float, str]:
    """Cumulative log-likelihood ratio and the first boundary crossed."""
    a = math.log((1 - BETA) / ALPHA)       # accept H1 above
    b = math.log(BETA / (1 - ALPHA))       # accept H0 below
    llr = 0.0
    for w in wins:
        llr += math.log(p1 / p0) if w else math.log((1 - p1) / (1 - p0))
        if llr >= a:
            return llr, "H1"
        if llr <= b:
            return llr, "H0"
    return llr, "none"


def beta_posterior_tail(wins_n: int, losses_n: int, threshold: float) -> float:
    """P(p > threshold) under Beta(1 + wins, 1 + losses), by regularized
    incomplete beta via simple numerical integration (no scipy dependency)."""
    a, b = 1 + wins_n, 1 + losses_n
    steps = 20_000
    total = 0.0
    tail = 0.0
    log_c = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    for i in range(steps):
        x = (i + 0.5) / steps
        d = math.exp(log_c + (a - 1) * math.log(x) + (b - 1) * math.log(1 - x))
        total += d
        if x > threshold:
            tail += d
    return tail / total if total > 0 else 0.0


def adjudicate(pnls: list[float], p_be: float) -> dict:
    wins = [x > 0 for x in pnls]
    n = len(wins)
    w = sum(wins)
    p0, p1 = p_be, min(0.99, p_be + DELTA)
    llr, crossed = sprt_llr(wins, p0, p1)
    post = beta_posterior_tail(w, n - w, p_be + MARGIN)
    if crossed == "H0":
        verdict = "REJECT"
    elif n >= MIN_N and crossed == "H1" and post >= POSTERIOR_BAR:
        verdict = "PROMOTE"
    else:
        verdict = "CONTINUE"
    return {"n": n, "wins": w, "hit_rate": w / n if n else 0.0, "p_be": p_be,
            "p0": p0, "p1": p1, "llr": llr, "sprt": crossed,
            "posterior_gt_margin": post, "verdict": verdict}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", default="state/trades.jsonl")
    args = ap.parse_args()

    exits, entries = load_rows(Path(args.log))
    if not exits:
        raise SystemExit("no trades logged yet — the gate has nothing to adjudicate")
    pnls = [float(r.get("pnl_net", r.get("pnl", 0.0))) for r in exits]
    p_be, friction_frac = breakeven_win_rate(exits, entries)

    r = adjudicate(pnls, p_be)
    print("— Phase-B sequential gate (pre-registered: "
          f"alpha={ALPHA}, beta={BETA}, delta={DELTA}, margin={MARGIN}, "
          f"bar={POSTERIOR_BAR}, min_n={MIN_N}) —")
    print(f"n={r['n']}  wins={r['wins']}  hit={r['hit_rate']:.3f}  "
          f"p_be={r['p_be']:.3f} (friction {friction_frac:.3%} of stake)")
    print(f"SPRT llr={r['llr']:+.3f}  crossed={r['sprt']}   "
          f"P(p > p_be+{MARGIN} | data) = {r['posterior_gt_margin']:.3f}")
    print(f"VERDICT: {r['verdict']}")
    if r["verdict"] == "PROMOTE":
        print("Phase-B unlock is statistically justified. Change sizing_ladder to "
              "{93: 0.5, 97: 0.75} in a REVIEWED commit — the gate does not edit config.")
    elif r["verdict"] == "REJECT":
        print("The SPRT accepted H0: the edge does not beat its own costs. The honest "
              "action is paper mode and signal work — NOT more live data collection.")
    else:
        print("Between boundaries — keep trading Phase A; re-run after new exits.")


if __name__ == "__main__":
    main()
