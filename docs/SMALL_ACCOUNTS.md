# Small Accounts ($2k–$25k): What This System Can and Cannot Do

This document is arithmetic, not encouragement. All numbers use the shipped
Phase A configuration: SPY $2-wide debit verticals, sizing ladder `{93: 0.50}`
(= 2.0% of live equity per trade, sized at the worst permitted fill),
accepted mid-debit 0.30W–0.42W, ladder price cap `min(1.10 × mid, 0.45W)`,
friction $2.60 per contract per round trip, profit target 1.65× debit
(nominal +65%), hard stop −50%, force-flat 15:30 ET.

## 1. Effective equity floors (the numbers `min_equity: 2000` does not tell you)

One SPY contract at the worst permitted fill costs, in risk dollars:

| Mid debit (of $2 width) | Cap price | Risk/contract | Equity needed at 2%/trade |
|---|---|---|---|
| 0.30W = $0.60 (cheapest accepted) | $0.66 | $66 | **$3,300** |
| 0.36W = $0.72 (typical) | $0.79 | $79 | **$3,960** |
| 0.42W = $0.84 (most expensive accepted) | $0.90 | $90 | **$4,500** |

**One-lot minimum (shipped, `allow_one_lot_minimum: true`):** when the 2%
ladder target cannot hold one contract but the HARD 4% cap can — including
the $2.60 friction — the governor takes one contract and logs the overshoot.
That moves the true floor to **$1,715 (cheapest debit) – $2,315 (worst)**,
with actual per-trade risk running **2.0–4.0% of equity until ~$4,600**
(one contract IS the position; `risk_pct_actual` is logged per trade).
`min_equity` ships at **3000** so a −6% day never strands the account below
its own floor; below the floor the runtime and dashboard now say **SIZING
DEAD** loudly instead of scoring in silence.

- **$3,000–$4,600: alive at one contract, elevated per-trade risk.** The
  one-lot rule admits every legal debit; risk per trade is 2.0–4.0% and the
  worst-case-day gate blocks a second position whenever combined day loss
  could breach −6%.
- **$4,600–$8,999: one contract, always.** Realized risk is quantized at
  $69–$93, i.e. 0.8%–2.0% of equity depending on your balance — usually
  *under* the intended 2%, which cuts growth below every table in §3.
- **SPX (W = 20 pts): one contract risks $660–$900 → equity floor
  $33,000–$45,000.** SPX is out of the question below ~$33k; `AUTO`
  resolves to SPY, which is correct for this account class.

## 2. The per-trade profit engine, honestly stated

Per contract at a typical $0.80 fill (risk = $80):

- Nominal winner: +65% of debit ($52). Realized average winner after
  slippage, time-stop exits, stale flushes, and force-flats: **≈ +40% of
  debit ($32)** — this is what the spec's own breakeven formula assumes
  when it reports p_be ≈ 0.61.
- Realized average loser: **≈ −50% of debit (−$40)**.
- Friction: **$2.60 (3.3% of risk) every round trip, at every account
  size** — fees are per contract, so scaling up never dilutes them. They
  are why breakeven is ~0.59–0.61 instead of ~0.56.

Expected value per trade as a fraction of debit: `EV = 0.90·p − 0.5325`
(p = hit rate on ≥93 setups — **unproven**; the calibration pipeline
exists precisely because nobody knows p yet).

| Hit rate p | EV (% of risk) | EV (% of equity at full 2% sizing) |
|---|---|---|
| 0.55 | −3.8% | −0.077% |
| 0.59 | ≈ 0 (breakeven) | ≈ 0 |
| 0.62 | +2.6% | +0.051% |
| 0.65 | +5.3% | +0.105% |

## 3. Compounding within the caps: annual growth band

**All EV and growth figures below are assumption-dependent** — the realized
average winner is unmeasured until the trade log accumulates; the honest
range at p=0.65 is roughly +0.10% to +0.43% of equity per trade. Treat every
row as a scenario, never a forecast.

Selectivity at score ≥93 plus the one-shot rule, event blackouts, FOMC
lockouts, and the −6% daily lockout yields roughly **2–6 trades/month**.

| Scenario | p | Trades/mo | Annual growth |
|---|---|---|---|
| Pessimistic | 0.55 | 4 | **≈ −3.6%** (and no lockout hits assumed) |
| Base | 0.59–0.61 | 4 | **≈ 0%** — you pay to collect calibration data |
| Optimistic | 0.65 | 6 | **≈ +7.8%** |
| Perfect-fill upper bound | 0.65, every winner exits at full +65% with zero slippage | 6 | ≈ +36% — the exit engine cannot deliver this on average; it is a bound, not a forecast |

Below $9k apply the granularity haircut from §1 (realized risk 55–90% of
2%): the optimistic case at $6k is ~+5%/yr, the base case ~0.

**Time for $5,000 to reach $25,000 by compounding alone: at +7.8%/yr,
21 years. At +3.7%/yr, 44 years. At base, never.** A small account reaches
$25k through deposits, not through this system's compounding. That is not
a defect of this system; it is the arithmetic of 2% risk × a thin, honest
edge × 2–6 trades a month. Any 0DTE product promising materially more from
a $2k–$10k start is describing the right tail of a survivorship
distribution, not an expectation.

## 4. Phase B/C does not rescue the small account

Phase B (3% at score ≥97) and C (4% at ≥97) only lift the ≥97 slice —
historically expect ~20–30% of qualifying trades. Blended risk goes from
2.0% to ~2.3% (B) and ~2.6% (C): a ×1.15–×1.3 multiplier on every row of
§3. Optimistic +7.8%/yr becomes ~+10%/yr. Meanwhile the unlock itself
requires ≥200 shadow+paper outcomes with a statistically significant
score→win-rate slope, then a further quarter plus 50 more outcomes for C.
At 5–10 qualifying setups a month that is **2–4 years**. Phase B/C is
governance for a mid-size account's future, not a small account's yield.

## 5. Which constraint binds first, by equity

| Equity | Binds first | Then | Then |
|---|---|---|---|
| <$2.3k | Granularity — zero trades possible (SIZING DEAD banner) | — | — |
| $2.3–4.6k | Granularity — one-lot rule, 2.0–4.0% realized risk | Worst-case-day gate | Fee drag |
| $4.6–9k | Granularity — locked at 1 contract, 0.8–2.0% realized risk | Fee drag | Day-trade budget (if broker enforces) |
| $9–25k | Fee drag + the daily trade cap (3 approvals/session) | Day-trade budget (if broker enforces) | Granularity |
| ≥$25k | None of these; the 7% heat / 2-position / one-shot caps take over | | |

Fee drag is the constant across all rows (per-contract, ~3.3% of risk); it
sets the breakeven, not the equity floor — and the governor's `fee_floor`
check now rejects any configuration whose profit target cannot clear 5×
round-trip friction.

**Day trades — regulatory status (verified August 2026):** the FINRA Pattern
Day Trader rule ($25k / 3-in-5) was **eliminated effective 2026-06-04**
(SEC-approved 2026-04-14), replaced by intraday-margin-exposure monitoring.
The change has an 18-month broker phase-in through October 2027 and
**enforcement timing varies by broker** — verify with yours. The governor
enforces a classic 3-per-5-business-day budget only when
`account_type: margin_small` is set (`day_trade_budget` rejections, ledger
persisted in `state/day_trades.json`); the post-repeal default is
`margin_large` (no self-imposed budget). Cash accounts: no day-trade
counter, but T+1 settlement means same-day reuse of sale proceeds risks
good-faith violations — settled-funds sizing is NOT implemented; the
caveat is logged once per session, and cash-account spread permission at
the broker is itself VERIFY-LIVE.

## 6. What the system will and will not do about all this

Will: size at the worst permitted fill INCLUDING friction; admit one
contract under the 4% cap when the 2% target can't hold it (logged
overshoot, never a rounded-up cap); block any second position that could
stack a day past −6% (worst-case-day gate); cap approvals at 3 per session
and halve size after a −4% day; reject junk economics via the fee floor;
show SIZING DEAD on the dashboard below the one-lot floor; enforce the
day-trade budget when configured; charge friction in paper mode; log every
rejection with a reason in `state/decisions.jsonl`; hold the caps against
any config edit.

Will not: loosen any cap for "opportunity"; martingale after the −6%
lockout; trade SPX below its floor; turn $2k into meaningful money.
Under ~$3,300 it is a data logger with a login. Between $3.3k and $25k it
is a capital-preserving edge-measurement instrument whose realistic
best case is high-single-digit annual growth. Fund it accordingly.

## 7. V2 addendum: distributions, the proof, and the sequential gate

**Terminal-equity distribution, one year from $5,000** (20,000 simulated
paths; one-lot regime, debit $0.75, realized winner +40% of debit, loser
−50%, fees $2.57, 48 trades/yr — every number assumption-labeled per §3):

| True hit rate p | 5th pct | 25th | median | 75th | 95th |
|---|---|---|---|---|---|
| 0.45 | $4,157 | $4,359 | $4,562 | $4,697 | $4,899 |
| 0.55 | $4,494 | $4,697 | $4,832 | $5,034 | $5,237 |
| 0.65 | $4,832 | $5,034 | $5,169 | $5,304 | $5,574 |

Read the middle row hard: at p=0.55 — a genuinely decent hit rate — the
**median year loses money** after fees, and the 95th percentile is +5%.
Even at p=0.65 the median year makes ~3.4%. The distribution's spread is
small precisely because the risk law works; the risk law cannot manufacture
expectancy. This is the arithmetic "high yield" collides with.

**The −6% law is now proven, not asserted:** `scripts/mc_risk_proof.py`
drives thousands of simulated days through the REAL RiskGovernor — pure
all-lose sequences included (maximum correlation) — and asserts realized
day loss never exceeds 6% plus fee slack. Current result: 0 violations,
worst observed day −5.99%. Run it yourself after any config change.

**Phase-B promotion is now a pre-registered sequential test:**
`scripts/phase_gate.py` runs a Wald SPRT (H0: p = after-fee breakeven,
H1: p = breakeven + 7pts; α=0.05, β=0.20) plus a Beta-posterior
requirement P(p > breakeven + 3pts) ≥ 0.95, n ≥ 60. It is peeking-safe:
run it after every trade if you like. It has three verdicts, and one of
them is REJECT — which means stop trading the edge, not collect more data.
