# MASTER SYSTEM — Kickoff prompt for a fresh Claude Code session

Paste everything below the horizontal rule into a fresh Claude Code session started inside
an empty directory on the Linux desktop (see `DESKTOP_SETUP_GUIDE.md`). It is
self-contained; Step 0 pulls the full research bundle.

---

## ROLE

You are the lead engineer for a personal, multi-sleeve automated trading system for one US
retail operator. The system's purpose, in priority order: (1) slowly compound a stock-ETF
and crypto portfolio that survives; (2) run a small, capped options sleeve for asymmetric
payoffs; (3) run a prediction-market mispricing "grind box" on Kalshi-backed event
contracts (codename **Picador**); (4) run regime-gated macro sleeves (commodities, rates,
FX) only when trends are strong; (5) eventually coordinate several brokerage accounts as
one "octopus" (one head, independent arms). Survival beats speed: you build one sleeve at
a time, paper-first, and nothing goes live without the gates below. You work in this empty
directory as a new git repository, in Python 3.12.

## STEP 0 — Load the evidence base (before anything else)

```bash
git clone --depth 1 --branch claude/prediction-market-arbitrage-dxi93z \
  https://github.com/drpostman123/PROMPTWARRIORS_V3.git /tmp/research
mkdir -p docs/research && cp -r /tmp/research/docs/* docs/research/ && git add docs && git commit -m "Import research bundle"
```
Read in this order; treat as ground truth unless a live API response contradicts it, and
treat `redteam.md` as the tie-breaker on process:
1. `docs/research/MASTER_SYSTEM_CHARTER.md`, then this prompt's **Canonical defaults** (they override every other number in the bundle).
2. `docs/research/research/redteam.md` — the build order and the 15 tests come from here.
3. `docs/research/PICADOR_WEBULL_PLAN.md` + `docs/research/PICADOR_RESEARCH_ADDENDA.md` (Webull/Kalshi facts; addenda win).
4. `docs/research/specs/events_routing.md`, `core.md`, `convex.md`, `macro.md` (buildable sleeve specs).
5. `docs/research/research/sizing.md` (equity-band ladder), `brokers.md`, `portfolio.md`, `quant.md`, `ops.md`, `webull_api.md`.
6. `docs/research/PICADOR_ORCHESTRATION.md` (phase-3 target, not v1).
Every `developer.webull.com` page has a Markdown twin at `<page>.md` with the raw OpenAPI JSON (`https://developer.webull.com/apis/llms.txt`). Kalshi public REST is `https://api.elections.kalshi.com/trade-api/v2`; Kalshi demo is `external-api.demo.kalshi.co`.

## CANONICAL DEFAULTS (single source of truth; encode them in `config/canonical.yaml` and cite this section in the file header)

**Allocation (barbell):** CORE 80 % of capital (≤ 20 % of capital in crypto, crypto ≤ 35 % of CORE risk); GRIND-PM cap 10 % (a measurement budget until proven); MACRO 5 % capital at a 3 % vol budget; CONVEX premium budget 3 % of equity per rolling 365 days, ≤ 1 % per expiry cycle, quarterly tranches never refilled by profits; T-bill (SGOV) buffer. Portfolio target vol 10–12 %.
**Profit sweep:** CONVEX realized gains 50 % → GRIND-PM (until its cap) / 50 % → CORE; GRIND-PM excess above cap → CORE monthly; CORE never refills CONVEX beyond the annual budget. Sweep prediction-market profits out weekly (settled wins can be clawed back under Rule 5.12 or court orders).
**Drawdown ladder (vs 12-month high-water mark, total equity):** −8 % halve CONVEX and MACRO budgets; −12 % CORE ×0.75, crypto cap 10 %; −16 % CORE ×0.50, all other sleeves paused; −20 % CORE ×0.25, everything else flat; re-risk one rung per new 3-month high. (This replaces the charter's −5/−10/−15 ladder and the specs' references to it.)
**Sizing:** shrink every edge/probability estimate by 50 % before sizing; Kelly fraction 0.15 on the shrunk edge (≈ quarter-Kelly after shrinkage); size per **event** (all strikes/hours of one underlying-expiry together) on the worst-case terminal scenario; Grossman–Zhou floor at 85 % of HWM for CORE/MACRO risk scaling.
**GRIND-PM caps:** per-event committed stake ≤ 5 % of GRIND equity; total committed stake ≤ 25 % of GRIND equity (hold-to-settlement losses are committed at entry, so a mark-to-market daily lock cannot protect you); per-market ≤ 3 %; ≤ 5 open events; minimum net edge 4 ¢ after the venue's exact fee; price band ±10 ¢ around **fair value** (not the mid), refuse one-sided or > 20 ¢-wide books and the first 60 s after a market opens; $50,000 notional guard per order; keep a $3,000 cash reserve per event venue for Rule 5.11 error-trade charges.
**Loss locks:** daily realized+committed loss −3 % of start-of-day GRIND equity → cancel all, no new opens, persisted lockout surviving restart; CORE/MACRO/CONVEX follow the ladder; clock drift > 250 ms or any stale feed (MQTT, BRTI, index, options) → halt that sleeve.
**Gates (fixed horizon, no peeking):** GRIND-PM sandbox→canary needs ≥ 10 trading days recorded, an out-of-sample Brier improvement of `p_fair` over the Kalshi mid on ≥ 300 settled markets, ≥ 300 paper settlements, zero governor-bypass tests failing, zero reconciliation drift; canary ($500–1,000, 1–5 contracts, index S1 only) → 2× after **exactly 400** live settlements if Wilson 95 % lower bound of net edge > 0 and profit factor ≥ 1.3 and max drawdown < 25 % of deployed; 2× → 5× after 1,000 cumulative settlements with Wilson LB > 1 ¢ and no lockout in 20 trading days. CORE graduates after one full four-tranche cycle plus one signal flip with tracking error ≤ 1.5 %/yr and 20 clean reconciliations. CONVEX S1 (earnings straddles) needs one full earnings season in shadow; CONVEX S2/S3 and any fat-tailed structure are **never** killed on N < 30 or on win rate, only on the premium budget. MACRO graduates only after a real gate-ON regime has been paper-traded.
**Equity-band ladder (from `sizing.md`):** <$500: CORE fractional DCA on one ETF/SGOV, 1-contract PM calibration trades ≤ $25/mo, everything else off; $500–5k: CORE vol-targeted 1–2 ETFs (no crypto), PM measurement budget, MACRO via ETFs, CONVEX off; $5–25k: + BTC, + CONVEX index-only from $5k and earnings straddles from $15k, + Kalshi maker route; $25–100k: + ETH, MES/MGC-class micros as feasible, PM router across routes; $100k–1M: full universe, PM share falls to 2–5 % because measured Kalshi depth caps it at ~$500–3k/hour (BTC) and ~$5–20k (S&P 4pm).
**Venue facts that bind:** Webull events LIMIT-only, buy-to-open/sell-to-close, $0.02 per contract per side (settlement free), hours are config (help center says 24/7 for crypto/financials, developer docs say weekdays — test), rate limits per App Key (balance/positions 2/2s; market-data REST 60/60s total; place/cancel 600/60s), MQTT 5 connections/100 symbols per subscribe/no resubscribe on reconnect, 2FA token dies after 15 idle days, sandbox = paper. Kalshi direct is cheapest at every price (taker ≤ 1.75 ¢, maker ≤ 0.44 ¢), requires `self_trade_prevention_type` on every order, demo env separate keys. Only Webull, Kalshi direct and (UNVERIFIED) IBKR trade events by API; Robinhood/tastytrade events are app-only; Public has none. Robinhood Crypto API has no post-only flag. Neither Public nor Robinhood Crypto has a sandbox → CORE paper = local replay. BTC/ETH hourlies settle on the 60-second BRTI/ERTI average; S&P hourlies settle on Kalshi's real-time HH:00 print; SPY strikes are too coarse → SPX/XSP options data required for S1.
**Compliance that binds:** before a second event account ever trades, email Kalshi listing every account (Rule 3.3(b)); never let two of your accounts hold opposite sides or cross (5.17(c) has no intent qualifier); position limits aggregate across all your accounts (5.19(f)); a cancelled error trade costs $3,000 with no automation exception for FCM customers (5.11); each account is operated only by its owner; sports contracts out of scope (Webull under state enforcement); unofficial broker libraries are banned. Tax: wash sales cross all accounts including IRAs → cross-broker lot ledger and no-shared-ticker rule between sleeves; event-contract characterization is unsettled (possible wagering treatment with a 90 % loss cap) → export-ready journal and a CPA opinion before GRIND-PM scales past canary.

## BUILD ORDER (survival-first; stop and report after each numbered step)

**Phase 0 — Decide and measure with zero broker keys**
0.1 `docs/PLAN.md`: restate this plan, list every UNVERIFIED item from the bundle as a checkbox, note disagreements. `docs/DECISIONS.md` for every conservative choice you make.
0.2 Scaffold: `pyproject.toml` (uv, ruff, mypy --strict, pytest + hypothesis), `src/ms/{config,money,fees,venues/{webull,kalshi,ibkr,public,rh_crypto,tastytrade},marketdata,recorder,pricing,scanners,risk,execution,journal,reconcile,ops,cli}`, `config/canonical.yaml`, `.env.example`. Money as fixed-point decimal with venue tick precision (Kalshi fees round to $0.000001; some markets tick $0.0001; fractional contracts to 0.01), never plain integer cents.
0.3 **Recorder** (Kalshi public REST + authenticated WS `cfbenchmarks_value`/`cfbenchmarks_value_5hz` with a free Kalshi key; index quote source; SPX/XSP near-strike option snapshots from whichever data source is available; Deribit public): Parquet, all open hourly index/crypto markets, top-of-book each second, depth every 5 s. Nightly report.
0.4 **Venue decision report:** price IBKR (Kalshi contract addressing in the API, hourlies availability, paper support) and Kalshi direct against Webull for this exact strategy. Recommend the first event venue before any executor exists. Default if IBKR is unverifiable: Kalshi direct for maker/data + Webull as the first FCM route.
0.5 **Offline go/no-go:** out-of-sample Brier test of the S1 fair-value model (Breeden–Litzenberger + butterfly digital, live index reference, per-hour variance schedule from `quant.md` §4) against the Kalshi mid on ≥ 300 settled hourly index markets. If `p_fair` does not beat the mid, GRIND-PM stays a recorder and you proceed to CORE.
0.6 **After-tax edge memo** for the operator's CPA (inputs: fee schedule, expected win rate by price bucket, wagering-cap scenario).

**Phase 1 — Picador single venue (only if 0.5 passes)**
1.1 Venue adapter(s) for the chosen route(s): auth incl. 2FA keepalive, per-endpoint token buckets, discovery with ticker parsing (strike/hour from the Kalshi ticker), streaming with reconnect + resubscribe + staleness, order/position events with a supervisor loop and REST liveness fallback. Run the sandbox tests (TIFs accepted, weekend hours, option snapshot coverage, balance field shapes, per-order caps via preview, kill -9 leaves orders open and cancel-all on restart works) and record results.
1.2 Pricing engine (`pricing/digital.py`, `pricing/crypto.py`) with unit tests vs closed-form digitals and property tests for monotonicity.
1.3 Exact fee + depth-walked edge (`fees.py`) for Webull flat, Kalshi quadratic, IBKR; property tests.
1.4 Scanners S1 (index) and S3 (ladder monotonicity, crypto range-sum; scanner only). **No S4.** S2 (crypto) recorder-only until its own Brier test passes.
1.5 `RiskGovernor`: sole minter of `ApprovedOrder` (frozen dataclass, module-private token); fixed check order: kill → lockout → staleness → hours → price band vs fair value → book quality → min edge → committed-stake caps → open-event count → sizing (down only) → burst spacing → notional guard; persisted lockout; tests that no scanner path can construct an approved order.
1.6 Executors: `PaperExecutor` (fills only when the recorded book trades through; conservative queue) and the live executor(s) behind `MS_ENV=live` + `MS_CONFIRM_LIVE=YES`; DAY re-arm and category-close cancel; short = buy NO; never SELL without a position; Kalshi orders always carry `self_trade_prevention_type=taker_at_cross`.
1.7 Journal + nightly reconciliation from venue history/executions and settlement events; rows without exchange confirmation are `suspect`; tax export.
1.8 Fixed-stake canary on a dedicated cash account per the gates; Prometheus, Telegram, healthchecks dead-man, systemd units with `LoadCredentialEncrypted`, `docs/RUNBOOK.md` (incl. cancelling from the broker app when the API path is down).

**Phase 2 — CORE (can start in parallel with Phase 1 paper; it is the system's purpose)**
2.1 Implement `specs/core.md`: Public ETF arm (VOO/QQQ/IWM/VEA/VWO risk, IEF/TLT/GLDM defensive, SGOV T-bill leg) + Robinhood Crypto arm (BTC/ETH, synthesized post-only); Tiingo/Coinbase/FRED data; 1/3/12 + SMA10 signals; dual momentum; vol targeting; four weekly tranches; cross-broker lot ledger; wash-sale guard; local replay paper; reference backtest from 2015; graduation per the gates; canary $500 → $2.5k.

**Phase 3 — CONVEX, then MACRO, then the octopus**
3.1 `specs/convex.md` (Public XSP/equity options + tastytrade backup; three structures; premium ledger; shadow through one earnings season + one FOMC).
3.2 `specs/macro.md` (tastytrade micro futures with the 2-of-3 + magnitude gate; ETFs below one-contract feasibility; Kalshi macro contracts through the same event registry).
3.3 Only now the octopus per `PICADOR_ORCHESTRATION.md` v1.0 and `specs/events_routing.md`: common schema, head registry with per-ticker ownership lock, router, second event venue **after** the Rule 3.3(b) email, third-party lease with fencing tokens, manual promotion, cold-standby restore drill on the desktop.

## NON-NEGOTIABLES
- Paper-first; every sleeve earns its own evidence; fixed-horizon gates; no scope beyond the current phase.
- Fixed-point money at venue precision; sides by explicit outcome; `no_ask = 1 − yes_bid`.
- PnL only from exchange-confirmed data. Secrets never logged; production keys only through the encrypted store; sandbox keys for development.
- Official SDKs/APIs only; pin dependencies; read install scripts before running them.
- Any config that loosens a canonical default must fail validation.

## HOW TO WORK
After each numbered step: `ruff`, `mypy --strict`, `pytest`; commit; report what was verified against a live endpoint and what was not. Ask only when a decision would materially change the build; otherwise take the conservative option and record it in `docs/DECISIONS.md`.
