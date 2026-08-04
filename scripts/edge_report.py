#!/usr/bin/env python3
"""Edge report: Kelly discipline, edge decay, cost drag, and tail honesty.

Reads the system's own trade log (``state/trades.jsonl``) and answers the
four questions the classic literature says matter more than selection:

  1. KELLY (Kelly 1956, Thorp): from logged win rate p and payoff ratio b,
     f* = p - (1-p)/b. Is the configured sizing ladder above HALF-Kelly?
     Half, not full — the formula assumes you know your edge exactly, and
     you do not; overestimating the edge turns full Kelly into overbetting.
  2. DECAY (Lo, Adaptive Markets): rolling recent-window hit rate vs
     lifetime. An edge decays BECAUSE it works. Flag divergence.
  3. COST (Bogle): total friction vs gross P&L. The one variable fully
     under your control; on 0DTE verticals it is not small.
  4. TAILS (Mandelbrot): worst logged day vs average day — and the printed
     reminder that the realized tail UNDERSTATES the possible one. The only
     real protection is structural (defined risk, the debit is the max loss).

Usage:
    python scripts/edge_report.py [--log state/trades.jsonl]
                                  [--config config/config.yaml] [--window 20]

Measurement only; below ~30 trades it reports SAMPLE TOO SMALL rather than
fitting noise. Net P&L (after fees) is used everywhere — a gross-P&L Kelly
estimate flatters the edge exactly where it is thinnest.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def kelly_fraction(p: float, b: float) -> float:
    """Kelly optimal fraction for win prob p and win/loss payoff ratio b."""
    if b <= 0:
        return 0.0
    return p - (1.0 - p) / b


def load_exits(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"no trade log at {path} — the system writes it as it trades")
    rows = [json.loads(l) for l in path.read_text().splitlines() if l]
    return [r for r in rows if r.get("kind") == "exit"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", default="state/trades.jsonl")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--window", type=int, default=20, help="recent-window size for decay check")
    args = ap.parse_args()

    exits = load_exits(Path(args.log))
    n = len(exits)
    print(f"trades: {n}\n")
    if n == 0:
        raise SystemExit("nothing to measure yet")

    pnl = [float(r.get("pnl_net", r.get("pnl", 0.0))) for r in exits]
    fees = [float(r.get("fees", 0.0)) for r in exits]
    wins = [x for x in pnl if x > 0]
    losses = [-x for x in pnl if x <= 0]

    # --- cost drag (always meaningful, even at n=1) ---
    gross = sum(float(r.get("pnl", 0.0)) for r in exits)
    total_fees = sum(fees)
    print("— Cost drag (Bogle) —")
    print(f"gross P&L ${gross:+,.0f}   fees ${total_fees:,.0f}   net ${gross - total_fees:+,.0f}")
    if gross > 0:
        print(f"friction consumed {100.0 * total_fees / gross:.1f}% of gross profit")
    print()

    # --- tails ---
    daily = defaultdict(float)
    for r, x in zip(exits, pnl):
        daily[str(r.get("ts", ""))[:10]] += x
    if daily:
        worst = min(daily.values())
        avg = sum(daily.values()) / len(daily)
        print("— Tails (Mandelbrot) —")
        print(f"days: {len(daily)}   avg day ${avg:+,.0f}   worst day ${worst:+,.0f}")
        print("the realized tail understates the possible one; the debit cap and the -6%\n"
              "breaker are the protection, not this history\n")

    if n < 30:
        print(f"SAMPLE TOO SMALL (n={n} < 30) — Kelly and decay estimates would be noise.\n"
              "Keep trading the Phase-A ladder; re-run at 30+.")
        return

    # --- Kelly ---
    p = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    b = avg_win / avg_loss if avg_loss > 0 else 0.0
    f_star = kelly_fraction(p, b)
    half = f_star / 2.0
    print("— Kelly discipline (Kelly 1956 / Thorp) —")
    print(f"hit rate p={p:.3f}   avg win ${avg_win:,.0f}   avg loss ${avg_loss:,.0f}   payoff b={b:.2f}")
    print(f"full Kelly f* = {f_star:+.3%}   half Kelly = {half:+.3%}")
    ladder_pct = None
    try:
        import yaml
        cfg = yaml.safe_load(Path(args.config).read_text()) or {}
        risk = cfg.get("risk", {})
        ladder = risk.get("sizing_ladder", {}) or {}
        max_mult = max(ladder.values()) if ladder else 0.5
        ladder_pct = (risk.get("max_trade_risk_pct", 4.0) / 100.0) * max_mult
        print(f"configured max per-trade risk: {ladder_pct:.3%} of equity")
    except Exception:
        print("(could not read config — compare the numbers above to your ladder manually)")
    if f_star <= 0:
        print("VERDICT: NO MEASURED EDGE (f* <= 0). Any size is overbetting; the correct\n"
              "position is zero until the score threshold or gates change something.")
    elif ladder_pct is not None and ladder_pct > half:
        print(f"VERDICT: LADDER ABOVE HALF-KELLY ({ladder_pct:.2%} > {half:.2%}) — "
              "tighten sizing_ladder;\nvolatility is eating compounding even while winning.")
    elif ladder_pct is not None:
        print(f"VERDICT: OK — ladder {ladder_pct:.2%} <= half-Kelly {half:.2%}.")
    print()

    # --- decay ---
    if n >= args.window * 2:
        recent = pnl[-args.window:]
        p_recent = sum(1 for x in recent if x > 0) / len(recent)
        print("— Edge decay (Lo, Adaptive Markets) —")
        print(f"lifetime hit rate {p:.3f}   last {args.window}: {p_recent:.3f}")
        if p_recent < p - 0.15:
            print("VERDICT: DECAYING — recent window well below lifetime. Edges die because\n"
                  "they work; consider paper mode while recalibrating rather than sizing up.")
        else:
            print("VERDICT: stable at this sample size.")
    else:
        print(f"(decay check needs {args.window * 2}+ trades)")


if __name__ == "__main__":
    main()
