# GodMode0DTE

An **extremely selective**, defined-risk 0DTE options system for Tastytrade.
Debit verticals on SPY/SPX only. Most days it takes **zero trades** — it fires
only when a transparent multi-factor Setup Score reaches **93/100**, and every
trade passes a risk governor that the signal engine cannot bypass.

> ⚠️ **Safety first.** This software ships in **paper mode** and should stay
> there until you have weeks of logged paper decisions and a calibrated score
> threshold. 0DTE options are among the highest-risk instruments retail traders
> can touch: theta and gamma move violently, and a defined-risk debit vertical
> can and regularly does go to zero. **No configuration of this system — or any
> system — guarantees profits.** The "GodMode" name refers to the selectivity
> bar, not to any promise about outcomes. Never trade money you cannot afford
> to lose entirely.

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
*and* the `GODMODE_CONFIRM_LIVE=YES` environment variable.

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

### Setup Score (100 points, trade at ≥ 93)

| Component | Max | What earns it |
|---|---|---|
| Opening range | 15 | OR (09:30–09:35 ET) complete, width within % and ATR bands |
| Breakout confirmation | 20 | Close beyond OR edge + relative volume ≥ 1.3× + close-location ≥ 0.7 |
| MTF alignment | 20 | 1m/5m/15m EMA(9/21) order, VWAP side, RSI bands, ADX ≥ 20 |
| Regime | 15 | Trend regime matches direction, confidence-scaled (rules or HMM) |
| Macro cluster | 10 | DXY/VIX/yields/gold confirmation (VIX half-weighted to avoid double-counting) |
| Event/sentiment | 5 | Clean calendar; high-impact windows are hard gates, not point deductions |
| Day-of-week / VIX prefs | 5 | Modest priors: Tue/Thu favored, VIX 13–24 sweet spot |
| Microstructure | 10 | Both legs: tight spreads, OI, fresh quotes — graded, gated |

**Hard gates** (zero the setup regardless of points): no confirmed breakout, OR
width filter fail, event blackout (FOMC day = full lockout), forced-bias
conflict, extreme vol regime / VIX > 32, counter-regime signal.

Score bands scale size *within* the 4% cap: 93→2%, 95→3%, 97+→4% of equity.
Profit targets scale the same way: +60% / +80% / +100% of debit.

### Exit priority (first hit wins)

1. Circuit breaker (flatten all, urgent)
2. Heat breach (trim worst position)
3. Hard stop: −50% of debit
4. Structure stop: close back through OR midpoint
5. Time stop 15:15 ET → 6. Force-flat 15:45 ET (no exceptions)
7. Profit target (score-dependent)

## Setup (Ubuntu)

```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv
git clone <this repo> && cd PROMPTWARRIORS_V3
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # add ,hmm for the HMM regime model

export TASTYTRADE_USERNAME="you"
export TASTYTRADE_PASSWORD="…"      # env vars only — never in YAML

pytest                              # 29 tests must pass before first run

# Terminal 1 — the runtime (paper mode by default):
godmode --config config/config.yaml

# Terminal 2 — the dashboard:
streamlit run godmode0dte/dashboard/app.py
```

`config/econ_calendar.yaml` is operator-maintained: add CPI/FOMC/NFP rows and
any headline-risk days (all-day lockout or directional-bias-only).

## Paper → live checklist

1. ≥ 4 weeks of paper trading with the decision log accumulating.
2. Verify score calibration: bucket logged scores (93–95, 95–97, 97+) and check
   hit-rate monotonicity from `state/decisions.jsonl` / `state/trades.jsonl`.
3. Volume baseline warmed up (20 sessions in `state/volume_profile.json`).
4. Only then: `paper_mode: false` **and** `GODMODE_CONFIRM_LIVE=YES`, with the
   smallest ladder (tighten `sizing_ladder` in YAML — config may always tighten).

## Design provenance

The parameter set was produced by a five-persona agent debate (signal
researcher, risk manager, execution engineer, quant statistician, systems
architect) — see [`docs/DESIGN_SPEC.md`](docs/DESIGN_SPEC.md) for the full
synthesized specification and the debate's resolved conflicts.
