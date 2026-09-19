# PICADOR — Kickoff prompt for a new Claude Code session

Copy everything below the line into a fresh Claude Code session on the Ubuntu desktop,
from inside an empty directory (e.g. `~/code/picador`). It is self-contained, but the
first step tells Claude to pull the research documents from this repository's branch so
the full evidence base is on disk.

---

## ROLE

You are the lead engineer for **Picador**, a Python 3.12 automated trading system that
trades Kalshi-backed event contracts through **Webull's official OpenAPI** from a US retail
Event Contract account, priced against options-implied and spot-implied probabilities.
You build it in this empty directory as a new git repository. You work paper-first, in
small verified steps, and you never place a live order until the phase gates below say so.

## STEP 0 — Load the evidence base (do this before anything else)

```bash
git clone --depth 1 --branch claude/prediction-market-arbitrage-dxi93z \
  https://github.com/drpostman123/PROMPTWARRIORS_V3.git /tmp/picador-research
ls /tmp/picador-research/docs/
```
Read, in this order, and treat as ground truth unless a live API response contradicts them:
1. `docs/PICADOR_WEBULL_PLAN.md` — master plan (venue facts, fees, strategies, architecture, phases)
2. `docs/PICADOR_RESEARCH_ADDENDA.md` — gap research from three agents (API, quant, ops)
3. `docs/PREDICTION_MARKET_ARB_STRATEGY.md` — general prediction-market arbitrage evidence
4. `docs/WEEVIL_REPO_SURVEY.md` — third-party repo audit and supply-chain rules
Copy `docs/` into this repo under `docs/research/` so the project carries its own evidence.

## HARD FACTS YOU MUST DESIGN AROUND (verified 2026-09-19)

- Webull event orders: `LIMIT` + `DAY` only; `BUY` to open, `SELL` to close; no sell-to-open (short = buy NO); price $0.01–$0.99; `client_order_id` ≤ 32 alphanumeric, unique; `instrument_type=EVENT`, `market=US`, `entrust_type=QTY`, `event_outcome` = `yes`|`no`.
- Fees: $0.02 per contract per side on opening and closing trades; settlement is free. Hold-to-expiry = $0.02 total; early round trip = $0.04.
- Hours on Webull: index events Mon–Fri 08:00–16:00 ET; crypto Mon–Fri 08:00–18:00 ET; economics Mon–Fri 08:00–23:00 ET; sports 24/7. Webull may change hours without notice.
- Rate limits (prod, per app key): place 600/60s, cancel 600/60s, preview 150/10s, balance/positions/open-orders **2/2s**, market-data REST **60/60s total**, streaming subscribe 60/60s, auth 10/10s. 429 on breach; repeated breaches → IP block.
- Live prices come from MQTT (`Category.US_EVENT`, subscribe types `QUOTE`, `SNAPSHOT`, `TICK`); order/position state comes from the gRPC `TradeEventsClient`. REST is for discovery and reconciliation only.
- Options data via OpenAPI: `US_OPTION` only (SPY/QQQ, not SPX), 20 symbols per snapshot call, separate paid OpenAPI market-data subscription.
- Kalshi's public REST (`https://api.elections.kalshi.com/trade-api/v2`, no auth) serves the same order books Webull routes into: use it for depth, history, series metadata and as a second price source.
- Target series: `KXINXU` (S&P 500 hourly, 5-pt strikes), `NASDAQ100I`, `KXBTC`/`KXBTCD` (BTC hourly, range `B` and threshold `T` strikes in one event, CF Benchmarks BRTI), `KXETH`.
- SDK: `webull-openapi-python-sdk` (Apache-2.0, official). Never use the unofficial `webull` package.
- Environments: sandbox `api.sandbox.webull.com` / `events-api.sandbox.webull.com`; paper trading via OpenAPI; production `api.webull.com`. Identical code paths, different endpoint + account id.

## OPERATOR DEFAULTS (use unless told otherwise)

- Starting event buying power: $3,000. Daily loss lock: −3 % of start-of-day equity → cancel all, no new opens, persisted to disk, survives restart. Max 20 % per event, 10 % per market, max 6 concurrent open events, quarter-Kelly sizing on estimated edge, minimum net edge 3¢ after the $0.02 fee.
- Strategies in scope for v1: **S1** options-implied fair value on index hourlies/dailies (SPY/QQQ 0DTE butterfly digitals + Breeden–Litzenberger, SPY→SPX basis from live index quote); **S3 scanner only** (ladder CDF monotonicity; crypto range = threshold difference) with executor behind a config gate; **S2/S4** after S1 graduates. No market making, no maker/rebate strategies on Webull.
- Repo is `picador`; package `src/picador/`; `uv` for env; `ruff` + `mypy --strict` + `pytest`; `structlog` JSON logs; SQLite journal; DuckDB/Parquet recorder; Prometheus metrics; Telegram alerts; systemd units for Ubuntu.

## DELIVERABLES, IN ORDER (stop and report after each; do not skip ahead)

1. **Repo scaffold + config model.** `pyproject.toml`, `src/picador/{config,models,venues/webull,venues/kalshi_public,marketdata,recorder,scanners,pricing,risk,execution,journal,reconcile,cli}`, `tests/`. Pydantic config with hard ceilings that YAML can tighten but not loosen. `.env.example` for `WEBULL_APP_KEY`, `WEBULL_APP_SECRET`, `WEBULL_ACCOUNT_ID`, `WEBULL_OPENAPI_TOKEN_DIR`, `PICADOR_ENV=sandbox|paper|live`. Secrets never logged.
2. **Webull venue adapter (read-only first).** Wrap the official SDK: auth + token dir handling for headless use; `discover_events()` (categories → series → open instruments for the target series, paginated); MQTT stream for US_EVENT with reconnect and staleness detection; gRPC trade-events client; a token-bucket rate limiter matching the table above per endpoint. Prove it against sandbox; log every response shape to `docs/api-shapes/` for the record.
3. **Kalshi public adapter + recorder.** Series/markets/orderbook/trades/candles readers; a recorder writing Parquet (per market, per second book top + full depth snapshots each N s) for all open hourly index/crypto markets; nightly summary job.
4. **Pricing engine.** `pricing/digital.py`: butterfly digital from 0DTE SPY/QQQ quotes (bid/ask-implied band), Breeden–Litzenberger density, SPY→SPX basis, time-to-expiry handling in ET; `pricing/crypto.py`: Deribit/short-horizon vol fair value; unit tests against closed-form Black–Scholes digitals.
5. **Exact fee + edge math.** `fees.py`: Webull flat $0.02 per side (settlement free); Kalshi quadratic (`ceil(0.07·p·(1−p)·100)/100`) for future direct executor; depth-walked executable edge for a given size; integer-cent arithmetic; property tests.
6. **Scanners S1 and S3** emitting `Opportunity{legs, size_cap, net_edge_cents, evidence}`; no broker access inside scanners.
7. **RiskGovernor** — the only minter of `ApprovedOrder` (frozen dataclass, module-private token checked in constructor). Checks in fixed order: kill switch → lockout → staleness (MQTT, feeds, clock) → hours gate per category → min edge → per-market/per-event/total caps → concurrency → sizing (resize down only) → burst spacing. Persisted daily-loss lockout. Tests that no scanner code path can construct an approved order.
8. **Executors.** `PaperExecutor` (fills from recorded/live books only when price trades through; conservative), `WebullExecutor` (preview → place → await gRPC order event → journal; replace/cancel; DAY re-arm at 08:00 ET; short = buy NO; never SELL without a position). Same interface; selected by `PICADOR_ENV`.
9. **Journal + reconciliation.** SQLite journal; daily reconcile from Webull order history/executions; rows without exchange-confirmed fills are `suspect` and excluded from metrics.
10. **Graduation + ops.** Wilson-lower-bound gates (paper → live canary → 2× → 5×; demote on 3 bad windows or −3 % deployed); Prometheus exporter; Telegram alerts (fills, halts, lockouts, staleness); systemd units + hardening; `docs/RUNBOOK.md` (start/stop/kill, secret rotation, what to do when Webull changes hours).

## PHASE GATES (do not cross without the evidence)

- Sandbox → paper: adapters pass integration tests; recorder has ≥ 5 trading days; category report shows S1 edge distribution.
- Paper → live canary ($500–1,000, 1–5 contracts): ≥ 200 paper settlements, realized edge within recorded distribution, zero governor bypass paths, reconciliation drift zero.
- Canary → scale: ≥ 100 live settlements, Wilson lower bound of win rate above break-even for the traded price bucket, profit factor ≥ 1.5.

## NON-NEGOTIABLES

- Paper-first; `PICADOR_ENV=live` additionally requires `PICADOR_CONFIRM_LIVE=YES`.
- Money is integer cents; sides by explicit `event_outcome`; `no_ask = 1 − yes_bid`, not `1 − yes_ask`.
- PnL reported only from exchange-confirmed data.
- No unofficial Webull endpoints; no third-party packages from unvetted repos; pin dependencies; read every `pyproject`/`package.json` script before installing anything.
- Every module small enough to read; tests for every rule in this prompt.

## HOW TO WORK

Start by restating the plan in your own words in `docs/PLAN.md` with any disagreements flagged, then build deliverable 1. After each deliverable: run `ruff`, `mypy --strict`, `pytest`; commit with a clear message; report what was verified and what was not. Ask only when a decision would materially change the build; otherwise choose the conservative option and note it.
