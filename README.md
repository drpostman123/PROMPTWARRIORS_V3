# GodMode0DTE

An **extremely selective**, defined-risk 0DTE options system for Tastytrade.
Debit verticals on SPY/SPX only. Most days it takes **zero trades** — it fires
only when a transparent multi-factor Setup Score reaches **93/100**, and every
trade passes a risk governor that the signal engine cannot bypass.

> ⚠️ **Safety.** This configuration ships in **live production mode** (armed by
> the `GODMODE_CONFIRM_LIVE=YES` interlock below). 0DTE options are among the
> highest-risk instruments retail traders can touch: theta and gamma move
> violently, and a defined-risk debit vertical can and regularly does go to
> zero. **No configuration of this system — or any system — guarantees
> profits.** The "GodMode" name refers to the selectivity bar, not to any
> promise about outcomes. The hard caps below are what make live operation
> survivable — they cannot be loosened from config. Never trade money you
> cannot afford to lose entirely. Paper mode remains available
> (`paper_mode: true`) and is the recommended way to validate any parameter
> change before it touches real money.

## Hard constraints (enforced in code, not just config)

| Constraint | Value | Where enforced |
|---|---|---|
| Naked short options | **never** — debit verticals only | `execution/verticals.py` builds nothing else |
| Max risk per trade | **4% of live equity** | `risk/sizing.py` + governor check #9; config ceiling in `config.py` |
| Max portfolio heat | **7% of equity** | governor check #10 |
| Max concurrent positions | **2** | governor check #8 |
| Daily loss limit | **−6% of starting-day equity → flatten & lock** | `risk/circuit_breaker.py`, lockout persists on disk across restarts |
| Sizing basis | live account equity, never fixed dollars | `risk/sizing.py` |

The YAML config can **tighten** these numbers but pydantic validators reject any
attempt to loosen them. Live mode requires *both* `paper_mode: false` in YAML
(the shipped default) *and* the `GODMODE_CONFIRM_LIVE=YES` environment variable —
an arming switch so a copied config can never fire real orders by accident.

### Why the signal engine cannot bypass risk

- The scoring layer emits `TradeIntent` objects — plain data, no broker access.
- Orders require an `ApprovedTrade`, whose constructor raises unless invoked
  with a module-private token that only `RiskGovernor.evaluate()` supplies.
- The broker reference lives only in the runtime (`app.py`) and the governor
  path; `scoring/` imports nothing from `execution/`.
- The circuit breaker writes its lockout to `state/lockout.json`; a restart on
  the same day boots straight into `LOCKED_OUT`. A corrupt lockout file fails
  **safe** (locked, not armed).

## Architecture

```
data → features → regime → scoring → risk → execution → monitoring

godmode0dte/
├── config.py            pydantic-validated YAML config, hard ceilings
├── models.py            shared frozen dataclasses (Bar, Quote, TradeIntent, …)
├── app.py               async runtime: clock/signal/risk/snapshot tasks
├── data/                market_data (DXLink streamer), macro cluster, econ calendar
├── features/            indicators (EMA/RSI/ADX/VWAP/ATR), bars, opening range, rel-volume
├── regime/              RegimeModel interface; rule-based v1; optional HMM v2
├── scoring/             transparent 0-100 Setup Score with hard gates
├── risk/                sizing ladder, circuit breaker, RiskGovernor (sole order gateway)
├── execution/           vertical construction, paper/tastytrade brokers, priority exits
├── state/               trading-day state machine, snapshot store
├── monitoring/          structlog JSON logging
└── dashboard/app.py     Streamlit dashboard
```

### Setup Score (100 points, trade at ≥ 93 — arbitrated weights, spec §1.2)

| Component | Max | What earns it |
|---|---|---|
| Opening range | 20 | OR (09:30–09:35 ET) complete, width within % and ATR bands |
| Breakout confirmation | 25 | Close beyond OR edge + relative volume ≥ 1.3× + close-location ≥ 0.7 |
| MTF alignment | 21 | 1m/5m/15m EMA(9/21) order, VWAP side, RSI bands, ADX floor |
| Regime | 20 | Trend regime matches direction, confidence ≥ 0.70, confidence-scaled |
| Macro cluster | 8 | DXY/VIX/yields/gold confirmation (VIX half-weighted — dedup rule) |
| VIX preference | 6 | VIX 14–22 sweet spot (day-of-week priors zeroed until calibrated) |
| Event / microstructure | 0 | **Gates, not points** — the debate's ruling: cost control is not alpha, and calendar-cleanliness points inflate scores exactly at threshold |

**Hard gates** (zero the setup regardless of points): no confirmed breakout, OR
width filter fail, event blackout (FOMC day = full lockout), forced-bias
conflict, extreme vol regime / VIX > 32, counter-regime signal, leg
liquidity/OI/staleness failures, **one-shot rule** (a losing trade closes that
direction for the day), and the **order-book layer**: L1 imbalance
`I = (V_bid − V_ask)/(V_bid + V_ask)` stacked against the trade beyond ±0.30,
or inside depth thinning below 35% of its rolling median (liquidity vanishes
right before the move everyone calls unexpected). The book is gate-only —
zero score weight — until `scripts/backtest_imbalance.py`, run against the
system's own logged bars, shows a positive logistic slope at p < 0.05 with
n ≥ 200 (the spec's earn-your-weight test). Entries run **09:36–11:30 ET** —
the minute after the opening range completes, because the best 0DTE breakouts
happen in the first 15 minutes. That early window is only honest because the
data hub backfills prior-session and premarket 5-minute candles at boot, so
ATR14, the EMAs, and the regime features are fully calibrated at the bell; if
the warmup fails, the regime floor holds entries back until session bars
suffice — the gate degrades, never the safety.

**Sizing is phased** (spec §2.1): launch is a flat **2%** of equity per trade.
The 3%/4% tiers for 97+ scores unlock only after ≥ 200 logged outcomes show
score→hit-rate monotonicity (Wilson lower bound above breakeven). The 4%
per-trade cap is the compiled ceiling in every phase.

### Exit priority (spec §7 — first hit wins, strict pre-emption)

- **P0** Circuit breaker → flatten all, urgent (beats a position up 80%)
- **P1** 15:30 ET force-flat → unconditional, urgent
- **P2** Heat `Σ max(entry_debit, mark)` > 7% → close largest-heat position
- **P3** Hard stop: mark ≤ 50% of debit on **2 consecutive marks** (spread mark, never underlying-only)
- **P4a** Profit: mark ≥ min(1.65 × debit, 0.80 × width)
- **P4b** Structure: close back through the OR trigger with P&L < +10% of debit
- **P5** Time: held ≥ 90 min with mark < 1.10 × debit, or open at 14:50 below entry

## Setup (Ubuntu)

```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv
git clone <this repo> && cd PROMPTWARRIORS_V3
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # add ,hmm for the HMM regime model

export TASTYTRADE_USERNAME="you"
export TASTYTRADE_PASSWORD="…"      # env vars only — never in YAML
export GODMODE_CONFIRM_LIVE=YES     # arming switch for live mode (shipped default)

pytest                              # all tests must pass before first run

# Terminal 1 — the runtime (LIVE by default; set paper_mode: true to simulate):
godmode --config config/config.yaml

# Terminal 2 — the dashboard:
streamlit run godmode0dte/dashboard/app.py
```

`config/econ_calendar.yaml` is operator-maintained: add CPI/FOMC/NFP rows and
any headline-risk days (all-day lockout or directional-bias-only).

## Live-operation notes

- **Restart behavior**: booting live with unrecognized open positions engages
  the kill switch (fail-flat) — the system will not blindly adopt risk it
  cannot attribute. Clear or close positions manually, then restart.
- **Volume baseline**: relative volume needs ~20 sessions of history in
  `state/volume_profile.json`; until then it falls back to session medians and
  the breakout component rarely maxes out (fewer trades, not worse ones).
- **Calibration governance** (from the quant debate): keep the 93 threshold
  fixed until ≥ 200 logged score outcomes exist with ≥ 30 per band and the
  93–95 / 95–97 / 97+ hit-rates are monotonic in
  `state/decisions.jsonl` / `state/trades.jsonl`.
- **Parameter changes**: validate any signal/exit change in `paper_mode: true`
  before it touches real money; risk caps can only be tightened.

## Design provenance

The parameter set was produced by a five-persona agent debate (signal
researcher, risk manager, execution engineer, quant statistician, systems
architect) — see [`docs/DESIGN_SPEC.md`](docs/DESIGN_SPEC.md) for the full
synthesized specification and the debate's resolved conflicts.
