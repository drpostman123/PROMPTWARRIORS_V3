# GodMode0DTE v2 — High-Conviction Aggressive Style

A deliberate parallel system. v1 measures a small edge with small size and
statistical gates; v2 hunts intraday extremes and goes heavy on long
single-leg 0DTE options when a *perfect* setup fires, selling into
strength fast. They share the data plane and nothing else philosophical.

Run: `python -m godmode0dte_v2.app --config config/config.v2.yaml`

## What v2 is

- **Instrument**: long 0DTE calls/puts (defined risk = premium paid; the
  8–20× day profile belongs to single legs, not verticals).
- **Trigger**: the `PerfectSetupDetector` — a config-driven condition list
  per side (RSI extreme, VWAP stretch in ATRs, volume climax, book
  imbalance, day move, VIX, time-of-day). "Perfect" means **all**
  conditions pass. Your exact discretionary rules drop into
  `config.v2.yaml` as data; near-misses (≥75%) are logged with the full
  metric snapshot so the definition tunes against evidence.
- **Size**: `conviction_risk_pct` (default **20%** of equity, ceiling 33%)
  of premium at risk per perfect setup. On a $3k account that's a ~$600
  line — a heavy hit, ~10× v1.
- **Exits**: sell into strength — 50% off at 2× premium, 25% at 4×,
  runner trailed 40% below high-water; hard stop −50% (2 marks); 60-min
  max hold; force-flat 15:30.
- **Modes**: `present` (default) surfaces the setup + suggested size and
  the human fires; `auto` fires itself. Either way every surfaced setup
  is shadow-followed, so outcomes accumulate regardless.

## The one law aggressive keeps

You kept exactly one survival rule from v1 — the daily loss breaker —
so v2 makes it *real*: **per-trade risk is clamped to the remaining
daily-loss headroom** (default daily limit 25%, flatten + lock + persist
on breach), and per-trade % must be ≤ the daily limit at config load.
"Near full account risk" is not in the software because it would make the
one rule you kept into theater: a single −100% position cannot be
flattened at −25%. The knobs are yours up to the 33%/40% ceilings; the
ceilings are where the code stops helping the account die in one candle.

Also enforced: max 2 trades/day (a third "perfect" setup in one session
means the definition is broken, not that the market is generous), and
missing data never counts toward perfect.

## The honest arithmetic of this style

At 20% risk per trade, the account survives ~3 consecutive full stops per
day-limit cycle and roughly 10–15 full losers lifetime without a big
winner. The style is therefore entirely dependent on two things being
true: the perfect-setup definition really does mark extremes, and the
scale-out engine banks the multiple when it comes. Neither is assumed
anywhere: v2 keeps no promotion gate to *stop* you, but it also keeps the
shadow book and full logs, so `state/v2_decisions.jsonl` will tell you —
after 20–30 surfaced setups — whether "perfect" is prophecy or pattern-
matching. Read it. The v1 tooling (`edge_report.py` pointed at
`state/v2_trades.jsonl`) works on v2 logs too.

v1's small-account tables (docs/SMALL_ACCOUNTS.md) do not apply here;
this style's distribution is a barbell: most months small negative
(stops + theta on wrong extremes), occasional large positive months.
Whether the barbell nets positive is exactly what the logs will show.
Nothing in this document claims it does.
