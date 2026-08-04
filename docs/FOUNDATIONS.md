# Foundations — where the free canon lives in this system

The load-bearing ideas of the public literature, mapped to the mechanism that
enforces each one here. The system's position: none of this is advisory —
each idea is either structural (code) or governed (a measured unlock).

| Source | Idea | Where it is enforced |
|---|---|---|
| Kelly (1956), Thorp | Sizing beats selection; overbetting destroys wealth even while winning — and the formula runs on the TRUE edge, which you only estimate | Phase ladder launches at flat 2% (≈ deliberately sub-Kelly) under a 4% hard cap; `scripts/edge_report.py` computes f* from logged outcomes AND a conservative f* at the Wilson 90% lower bound of the win rate (the "Kelly gap" = estimation risk), then checks the ladder against half of the *conservative* number |
| Buffett letters | Never lose money = you cannot compound from zero | Circuit breaker (−6% day → flatten + disk-persisted lockout), 4%/7% caps that config can tighten but never loosen |
| Mandelbrot | Your tail risk is larger than your model says | Defined-risk debit verticals **only** — max loss is the debit paid, structurally; no model estimates the tail because no position depends on estimating it |
| Kahneman & Tversky (1979) | You take profits early and gamble on losers; your entry price hijacks your judgment | The exit engine owns every exit by priority (P0–P5); no rule consults feelings, and the stop fires on the spread mark your loss-aversion would argue with |
| Lefèvre / Livermore (1923) | Ruin is structural, not unlucky; revenge trading compounds it | One-shot rule (a losing direction closes for the day), burst protection, daily lockout that survives restart |
| Lo, Adaptive Markets | Edges decay because they work | Earn-your-weight governance: features enter at zero weight and must pass the logistic test to score points; `edge_report.py` decay check; phase unlocks require sustained calibration, and can be walked back |
| Bengen / Trinity | Sequence of returns, not average returns, ruins you | Daily loss limit anchored to *starting-day* equity (persisted across restarts), heat cap on concurrent risk — the bad day is capped before it can compound |
| Bogle | Cost is the one variable you fully control | `friction_per_contract` is charged in paper mode and logged on every live exit; `edge_report.py` prints friction as a % of gross profit |
| Simons / Medallion | Edges live inside a size range and die above it | Depth-clamped sizing, fat-finger ceilings, and the phase ladder — size is unlocked by evidence, never by enthusiasm |

The essay's closing point is the one this repo takes most seriously: the
paywall was never on the information — it was on the patience. The
calibration governance in `DESIGN_SPEC.md` is patience, mechanized: nothing
sizes up until the logged data says so, and the report tools will tell you
"no measured edge — the correct position is zero" without flinching.
