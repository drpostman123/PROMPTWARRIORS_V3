#!/usr/bin/env python3
"""Backtest the order-book imbalance signal on the system's own logs.

    I = (V_bid - V_ask) / (V_bid + V_ask)

Reads the per-bar "score" records the runtime appends to
``state/decisions.jsonl`` (each carries price + book imbalance), joins every
record with the record `--horizon` minutes later to get a forward return, and
answers the only question that matters under the design governance
(DESIGN_SPEC §1.2 / §2.4): does imbalance predict forward returns well enough
to EARN score weight?

Outputs:
  1. Imbalance-bucket table: count, mean/median forward return, hit rate.
  2. Thinning table: forward |move| when depth was thinning vs normal
     (thinning should predict *volatility*, not direction).
  3. Logistic test  P(up) = sigmoid(a + b*I): slope b, its z-score, and the
     verdict against the spec's bar (b > 0, p < 0.05, n >= 200).

Usage:
    python scripts/backtest_imbalance.py [--log state/decisions.jsonl]
                                         [--horizon 5] [--min-n 200]

No trades are placed; this is measurement only. A passing verdict is the
prerequisite for giving the component points — not a promise it will keep
predicting.
"""

from __future__ import annotations

import argparse
import json
import math
from bisect import bisect_left
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, median

import numpy as np


def load_records(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"no decision log at {path} — run the system first; it logs every scored bar")
    out = []
    for line in path.read_text().splitlines():
        if not line:
            continue
        r = json.loads(line)
        if r.get("kind") == "score" and r.get("price") and r.get("book"):
            r["_ts"] = datetime.fromisoformat(r["ts"])
            out.append(r)
    return out


def forward_join(records: list[dict], horizon_min: int) -> list[tuple[float, float, bool]]:
    """(imbalance, forward_return_pct, thinning) per record with a future match."""
    records.sort(key=lambda r: r["_ts"])
    times = [r["_ts"] for r in records]
    joined = []
    for i, r in enumerate(records):
        target = r["_ts"] + timedelta(minutes=horizon_min)
        j = bisect_left(times, target, lo=i + 1)
        if j >= len(records):
            continue
        # accept a match within +/- 90s of the target horizon
        if abs((times[j] - target).total_seconds()) > 90:
            continue
        fwd = 100.0 * (records[j]["price"] - r["price"]) / r["price"]
        joined.append((float(r["book"]["imbalance"]), fwd, bool(r["book"]["thinning"])))
    return joined


def bucket_table(rows: list[tuple[float, float, bool]]) -> str:
    edges = [(-1.01, -0.3), (-0.3, -0.1), (-0.1, 0.1), (0.1, 0.3), (0.3, 1.01)]
    lines = [f"{'imbalance':>14} {'n':>6} {'mean fwd %':>10} {'med fwd %':>10} {'hit(up) %':>9}"]
    for lo, hi in edges:
        sub = [f for i, f, _ in rows if lo <= i < hi]
        if not sub:
            lines.append(f"[{lo:+.1f},{hi:+.1f})".rjust(14) + f" {0:>6}")
            continue
        hit = 100.0 * sum(f > 0 for f in sub) / len(sub)
        lines.append(f"[{lo:+.1f},{hi:+.1f})".rjust(14) +
                     f" {len(sub):>6} {mean(sub):>10.4f} {median(sub):>10.4f} {hit:>9.1f}")
    return "\n".join(lines)


def thinning_table(rows: list[tuple[float, float, bool]]) -> str:
    thin = [abs(f) for _, f, t in rows if t]
    fat = [abs(f) for _, f, t in rows if not t]
    lines = [f"{'book state':>12} {'n':>6} {'mean |move| %':>13} {'med |move| %':>13}"]
    for name, sub in (("thinning", thin), ("normal", fat)):
        if sub:
            lines.append(f"{name:>12} {len(sub):>6} {mean(sub):>13.4f} {median(sub):>13.4f}")
        else:
            lines.append(f"{name:>12} {0:>6}")
    return "\n".join(lines)


def logistic_slope(rows: list[tuple[float, float, bool]],
                   horizon_min: int = 5,
                   bar_spacing_min: float = 1.0) -> tuple[float, float, float, int]:
    """Fit P(fwd>0) = sigmoid(a + b*I); return (b, z_naive, z_hac, n_eff).

    STATISTICS FIX (audit round 3): the rows are per-1-min bars but each
    carries a `horizon_min`-minute FORWARD return, so consecutive rows share
    up to L = horizon/spacing - 1 minutes of the same future path. The
    observations are serially correlated up to lag L, and the naive
    inverse-Hessian SE (which assumes independence) is too small by roughly
    sqrt(L+1) — z was inflated by ~sqrt(overlap).

    Remedy: Newey-West/HAC sandwich covariance on the per-observation score
    vectors g_t = x_t * (y_t - p_t) with Bartlett weights out to lag L:

        Cov(beta) = H^{-1} S H^{-1},
        S = sum_t g_t g_t' + sum_{l=1..L} w_l * sum_t (g_t g_{t-l}' + g_{t-l} g_t'),
        w_l = 1 - l/(L+1).

    Rows must be in time order (forward_join sorts). Day boundaries make the
    true correlation shorter than L at the seams, which only makes the HAC SE
    mildly conservative. n_eff = ceil(n / (L+1)) is the honest sample size to
    hold against the spec's min-n bar.
    """
    X = np.array([[1.0, i] for i, _, _ in rows])
    y = np.array([1.0 if f > 0 else 0.0 for _, f, _ in rows])
    n = len(y)
    beta = np.zeros(2)
    for _ in range(50):
        p = 1.0 / (1.0 + np.exp(-X @ beta))
        W = p * (1 - p)
        H = X.T @ (X * W[:, None]) + 1e-9 * np.eye(2)
        step = np.linalg.solve(H, X.T @ (y - p))
        beta += step
        if np.max(np.abs(step)) < 1e-8:
            break
    p = 1.0 / (1.0 + np.exp(-X @ beta))
    W = p * (1 - p)
    H = X.T @ (X * W[:, None]) + 1e-9 * np.eye(2)
    Hinv = np.linalg.inv(H)
    se_naive = math.sqrt(Hinv[1, 1])

    g = X * (y - p)[:, None]                      # (n, 2) score contributions
    L = max(0, int(math.ceil(horizon_min / bar_spacing_min)) - 1)
    S = g.T @ g
    for lag in range(1, min(L, n - 1) + 1):
        w_l = 1.0 - lag / (L + 1)                 # Bartlett kernel
        Gl = g[lag:].T @ g[:-lag]
        S += w_l * (Gl + Gl.T)
    cov_hac = Hinv @ S @ Hinv
    se_hac = math.sqrt(max(float(cov_hac[1, 1]), 0.0))

    b = float(beta[1])
    z_naive = b / se_naive if se_naive > 0 else 0.0
    z_hac = b / se_hac if se_hac > 0 else 0.0
    n_eff = int(math.ceil(n / (L + 1)))
    return b, z_naive, z_hac, n_eff


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", default="state/decisions.jsonl")
    ap.add_argument("--horizon", type=int, default=5, help="forward-return horizon, minutes")
    ap.add_argument("--min-n", type=int, default=200, help="spec's minimum sample for a verdict")
    args = ap.parse_args()

    records = load_records(Path(args.log))
    rows = forward_join(records, args.horizon)
    print(f"records: {len(records)} scored bars, {len(rows)} with a {args.horizon}-min forward match\n")
    if not rows:
        raise SystemExit("nothing to measure yet — let the system log more sessions")

    print(f"— Imbalance buckets ({args.horizon}-min forward return) —")
    print(bucket_table(rows), "\n")
    print("— Liquidity thinning (predicts volatility, not direction) —")
    print(thinning_table(rows), "\n")

    b, z_naive, z, n_eff = logistic_slope(rows, horizon_min=args.horizon)
    p_two = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2))))
    print(f"— Earn-your-weight test: P(up) = sigmoid(a + b*I) —")
    print(f"slope b = {b:+.4f}, z_naive = {z_naive:+.2f} (overlap-inflated, shown for honesty)")
    print(f"HAC z = {z:+.2f} (Newey-West, L={args.horizon - 1}), p = {p_two:.4f}, "
          f"n = {len(rows)}, n_eff ~ {n_eff}")
    if n_eff < args.min_n:
        print(f"VERDICT: INSUFFICIENT DATA (n_eff {n_eff} < {args.min_n}) — "
              "imbalance stays a gate, zero weight.")
    elif b > 0 and p_two < 0.05:
        print("VERDICT: PASS — eligible for score weight per DESIGN_SPEC §2.4 "
              "(assign a small weight, rebalance to 100, and keep monitoring).")
    else:
        print("VERDICT: FAIL — no directional edge at this horizon; keep it as a gate only.")


if __name__ == "__main__":
    main()
