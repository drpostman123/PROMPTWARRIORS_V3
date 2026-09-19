# PICADOR — Kickoff prompt for a new Claude Code session

Copy everything below the line into a fresh Claude Code session on the Ubuntu desktop,
from inside an empty directory (e.g. `~/code/picador`). It is self-contained; the first
step pulls the full evidence base from this repository's branch.

---

## ROLE

You are the lead engineer for **Picador**, a Python 3.12 automated trading system that
trades Kalshi-backed event contracts on hourly/daily S&P 500, Nasdaq-100, BTC and ETH
through **Webull's official OpenAPI** from a US retail Event Contract account, pricing them
against options-implied and spot-implied probabilities and holding to settlement. You build
it in this empty directory as a new git repository. You work paper-first, in small verified
steps, and you never place a live order until the phase gates below say so.

## STEP 0 — Load the evidence base (before anything else)

```bash
git clone --depth 1 --branch claude/prediction-market-arbitrage-dxi93z \
  https://github.com/drpostman123/PROMPTWARRIORS_V3.git /tmp/picador-research
mkdir -p docs/research && cp -r /tmp/picador-research/docs/* docs/research/
```
Read in this order and treat as ground truth unless a live API response contradicts it:
1. `docs/research/PICADOR_WEBULL_PLAN.md` (v1.1 master plan)
2. `docs/research/PICADOR_RESEARCH_ADDENDA.md` (corrections from three research agents; **wins over the plan where they disagree**)
3. `docs/research/research/webull_api.md`, `quant.md`, `ops.md` (full agent reports with sources and measurements)
4. `docs/research/PREDICTION_MARKET_ARB_STRATEGY.md`, `docs/research/WEEVIL_REPO_SURVEY.md` (general evidence; repo audit; supply-chain rules)
Every `developer.webull.com` page has a Markdown twin at `<page>.md` embedding the raw OpenAPI JSON; the index is `https://developer.webull.com/apis/llms.txt`. Use those, not the HTML, when you need exact schemas.

## HARD FACTS TO DESIGN AROUND (verified 2026-09-19; UNVERIFIED items are Phase 0 tests)

- **Orders:** `instrument_type=EVENT`, `market=US`, `combo_type=NORMAL`, `entrust_type=QTY`, `event_outcome` = `yes`|`no`, `BUY` to open / `SELL` to close, no sell-to-open (short = buy NO), price $0.01–$0.99, `client_order_id` ≤ 32 alphanumeric unique. Narrative docs say LIMIT/DAY only; the JSON schema lists DAY, GTC, IOC, GTD, FOK → **UNVERIFIED: test each TIF in sandbox before building any emulation.** DAY orders cancel at 00:10 ET. Batch/combo orders do not apply to EVENT. Preview returns only `estimated_cost` and `estimated_transaction_fee`. Per-order cap 50,000 contracts (one page) vs 500,000/$50,000 (another) → hard-code a $50,000 notional guard.
- **Fees:** $0.02 per contract per side on opening and closing trades; settlement free. Hold-to-expiry $0.02 total; early round trip $0.04.
- **Hours:** developer docs: index Mon–Fri 08:00–16:00 ET, crypto Mon–Fri 08:00–18:00 ET, economics 08:00–23:00 ET, sports 24/7; Webull help center says crypto/financials/economics are 24/7 → **UNVERIFIED: run a weekend test.** Hours are config, never constants.
- **Rate limits (prod, per app key):** place 600/60s, cancel 600/60s, preview 150/10s, balance / positions / open orders / history / detail **2/2s each**, market-data REST **60/60s total**, streaming subscribe 60/60s, auth 10/10s, account list 10/30s. 429 on breach; repeated → IP block.
- **Streaming:** MQTT `Category.US_EVENT`, topics `event-quote` (bids only), `event-snapshot`, `event-tick`; **max 5 connections per app key, ≤ 3 msg/s per connection, ≤ 100 symbols per subscribe call, subscriptions are NOT restored on reconnect.** REST depth adds asks (10 levels). Event data needs no paid subscription.
- **Trade events (gRPC):** fills arrive only as order events (`scene_type` FILLED/FINAL_FILLED…, no fee, no outcome field); position events push settlement `{event_name, yes_condition, settle_result, settle_side, quantity, cost, settle_amount}`; a clean stream close returns silently → supervisor loop; SDK issue #13 reports zero events on a live account → REST liveness fallback every 60 s.
- **Discovery:** instrument list has `last_trading_date, expected_exp_date, latest_exp_date, payout_date, status, tradable_status (OC/CO/NT), can_close_early, fractionable, price_ranges`, cursor pagination; **no strike, no intraday expiry** → parse both from the Kalshi ticker and join Kalshi public metadata. Index hourlies list the previous day at 16:00 ET; early-close days list only H1000–H1300.
- **Balances:** for `EVENTS_CASH` accounts read `account_currency_assets[].buying_power`; cash/NLV fields are documented empty.
- **Auth:** HMAC (SDK 3.0.1 uses SHA-256 despite docs saying SHA-1); 2FA token must be approved in-app within 5 min and **becomes INVALID after 15 consecutive idle days** → keepalive call + alert; key reset invalidates the old key instantly.
- **Environments:** **sandbox = paper trading**; get a separate sandbox App Key via "Using OpenAPI service in Paper Trading" on the Individual Application page (auto-approved). Hosts `api.sandbox.webull.com`, `events-api.sandbox.webull.com`; production `api.webull.com`, `events-api.webull.com` (origin AWS us-east-1). Sandbox data may be 15-min delayed.
- **Options data:** REST snapshot ≤ 20 symbols, `US_OPTION`, top-of-book with Greeks/IV, no streaming; contract list supports `root_symbol=SPXW`, `underlying_type=INDEX_CALL_OPTION`, `expired_cycle=DAILY` → **UNVERIFIED whether snapshots return SPX/XSP quotes; test first.** Requires the separate OpenAPI "OPRA Real-Time Non-display" subscription (price in portal). **SPY is not acceptable for S1** (10-pt strikes vs Kalshi's 5-pt ladder; 8–18 ¢ butterfly width).
- **Settlement truths:** S&P/Nasdaq hourlies settle on Kalshi's **real-time ~HH:00:00 print** (not the official close; 4pm value differed from the close by 0.38 pt on 09-18). BTC/ETH settle on the **60-second average of CF Benchmarks BRTI/ERTI before HH:00**. That feed is available on Kalshi's authenticated WebSocket (`cfbenchmarks_value` 1 Hz with trailing 60-s average, `cfbenchmarks_value_5hz`) → a free KYC'd **Kalshi API key is required for data**.
- **Kalshi public REST** (`https://api.elections.kalshi.com/trade-api/v2`, no auth) serves the same books Webull routes into: use for depth, series metadata, candles, trades. Target series: `KXINXU` (5-pt strikes, H1000–H1600), `KXNASDAQ100U` (verify ticker; `NASDAQ100I` had no open markets), `KXBTC`/`KXBTCD` ($100 ranges + two tail thresholds), `KXETH`.
- **Measured microstructure (2026-09-19):** BTC hourly median 34,111 contracts / 1,031 trades per hour, 26 % in the last 5 min, ATM spread 1 ¢, neighbours 3–6 ¢; KXINXU median contracts per event 113k–358k, half of the 4pm event trades in the last 30 min; weekend books 30–45 ¢ wide. Intraday variance share by hour 10→16: 21/20/7/6/16/30 %; overnight sd 50 bp; realised vol Jul–Sep 2026 ≈ 8 % ann.
- **Measured markout:** taking 90–98 ¢ in the last 2–5 minutes loses (−2.6 to −19.5 ¢/contract after fee); cheap-side tail buys (2–10 ¢) in the last 120 s showed +9 ¢. Do not build the "take the near-certain side" strategy.
- **Sizing math:** at 3 ¢ net edge at 50 ¢, per-trade Sharpe ≈ 0.06 → ~1,100 independent trades for t = 2 (5 ¢: ~392; 8 ¢: ~150). Quarter-Kelly across 3 correlated legs of one event ≈ ¾-Kelly (median max drawdown 61 %). Size per **event**, on the worst-case terminal scenario, after shrinking the edge estimate.
- **Compliance:** US residents; sports out of scope (Webull received a Connecticut cease-and-desist on 2026-09-10 and is named in a Kentucky suit; index/crypto untouched). Kalshi Rule 3.3(b): notify rule33@kalshi.com before trading with both a Webull and a direct Kalshi account; never self-match across accounts. Rule 5.11: a cancelled error trade costs the causer $3,000 and FCM customers get no automated-malfunction exception → price-band and notional guards are mandatory. Tax treatment is unsettled (possible wagering treatment with a 90 % loss cap; no 1099-B from Kalshi/Robinhood; Webull's form unverified) → export-ready journal; CPA before scaling.
- **Never** use the unofficial `webull` package or any unvetted third-party bot repo; pin dependencies; read every install script first.

## OPERATOR DEFAULTS (use unless told otherwise)

- Starting event buying power $3,000, treated as a **measurement budget**. Daily loss lock −3 % of start-of-day equity → cancel all, no new opens, persisted, survives restart. Max 15 % per event (all strikes of one expiry count together), 10 % per market, 5 open events, minimum net edge 4 ¢ after the $0.02 fee, edge estimates shrunk by 50 % before sizing, Kelly fraction 0.15 on the shrunk edge, per-order price band ±10 ¢ from Kalshi mid, $50,000 notional guard.
- **v1 strategies:** **S1** index hourlies/dailies vs SPX/XSP 0DTE options (butterfly digital + Breeden–Litzenberger, live index reference, per-hour variance schedule, refuse within a vol-scaled band of a strike boundary). **S3** scanner only (ladder CDF monotonicity on index events; range-sum on crypto), executor behind a config gate default off. **S2** (BTC/ETH vs Kalshi-fed BRTI average + scaled Deribit density) as recorder + research until data shows edge. **No S4, no market making, no sports.**
- Stack: `uv`, `ruff`, `mypy --strict`, `pytest` (+ hypothesis property tests for money math), `webull-openapi-python-sdk` (official, Apache-2.0), `httpx`/`websockets` for Kalshi and Deribit, `pydantic` config with ceilings YAML can tighten but never loosen, `structlog` JSON, SQLite journal, DuckDB/Parquet recorder, Prometheus, Telegram, systemd units with `LoadCredentialEncrypted` for secrets, chrony. Desktop = development and paper; production host = AWS us-east-1.

## DELIVERABLES, IN ORDER (stop and report after each; do not skip ahead)

1. **Scaffold + config.** `pyproject.toml`; `src/picador/{config,models,money,fees,venues/webull,venues/kalshi,venues/deribit,marketdata,recorder,pricing,scanners,risk,execution,journal,reconcile,ops,cli}`; `tests/`. `.env.example`: `WEBULL_APP_KEY`, `WEBULL_APP_SECRET`, `WEBULL_ACCOUNT_ID`, `WEBULL_OPENAPI_TOKEN_DIR`, `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`, `PICADOR_ENV=sandbox|live`, `PICADOR_CONFIRM_LIVE`. Money is integer cents everywhere. Secrets never logged.
2. **Webull adapter, read-only.** Wrap the official SDK: auth + token-dir handling for headless use + 15-day keepalive; per-endpoint token buckets matching the limits above; `discover_events()` for the target series with ticker parsing (hour, strike, range) and Kalshi metadata join; MQTT client honouring 5 connections / 100 symbols per call / resubscribe-on-reconnect / staleness; gRPC trade-events supervisor with REST liveness fallback. Prove against sandbox; save every response shape to `docs/api-shapes/`. **Run the Phase 0 tests here and record results:** TIFs accepted for EVENT; weekend crypto hours; whether option snapshots return `SPXW`/`XSP` quotes; balance field shapes for `EVENTS_CASH`.
3. **Kalshi adapter + recorder.** Public REST (series, markets, orderbook, candles, trades) and authenticated WebSocket (`cfbenchmarks_value`, `cfbenchmarks_value_5hz`, orderbook if entitled). Recorder → Parquet: top-of-book each second and full depth every 5 s for all open target markets, BRTI 1 Hz, index quote 1 Hz, SPX/XSP near-strike option snapshots every 5 s. Nightly report: S1 `p_fair − market` distribution by hour and price bucket, ladder violations after fees, depth at each strike, time-of-day volume.
4. **Pricing engine.** `pricing/digital.py` (butterfly digital with bid/ask band; BL density; live index reference; per-hour variance schedule from `quant.md` §4 updated nightly from the recorder; ET time handling incl. early closes), `pricing/crypto.py` (BRTI 60-s average target; Deribit daily density scaled to the hour; realised-vol fallback). Unit tests against closed-form digitals; property tests for monotonicity in K and T.
5. **Fees + edge.** `fees.py`: Webull $0.02/side (settlement free); Kalshi quadratic `ceil(0.07·p·(1−p)·100)/100` for a future direct executor; depth-walked executable edge for a size; integer cents; property tests.
6. **Scanners S1, S3** emitting `Opportunity{legs, size_cap, net_edge_cents, evidence}`; no broker access inside scanners.
7. **RiskGovernor**, sole minter of `ApprovedOrder` (frozen dataclass, module-private token). Fixed order: kill switch → lockout → staleness (MQTT, BRTI, index, options, clock drift > 250 ms) → hours gate per category → price band vs Kalshi mid → min edge → per-event/per-market/total caps → open-event count → sizing (resize down only) → burst spacing → $50k notional. Persisted daily-loss lockout. Tests proving no scanner path can construct an approved order.
8. **Executors.** `PaperExecutor` (fills only when the recorded/live book trades through the limit; conservative queue model) and `WebullExecutor` (preview → place → await gRPC order event → journal; replace/cancel; DAY re-arm and category-close cancel; short = buy NO; never SELL without a position). Same interface; selected by `PICADOR_ENV`; live additionally requires `PICADOR_CONFIRM_LIVE=YES`.
9. **Journal + reconciliation.** SQLite journal of every order, fill, settlement event; daily reconcile from Webull order history/executions and settlement position events; rows without exchange confirmation are `suspect` and excluded from metrics; CSV/Parquet export for tax.
10. **Graduation + ops.** Gates in trade counts by price bucket (below), Prometheus exporter, Telegram alerts (fills, halts, lockouts, staleness, 2FA token age), healthchecks.io dead-man, systemd units + hardening, `docs/RUNBOOK.md` (start/stop/kill, key rotation, token renewal, Webull hours change, Kalshi market under review).
11. **Phase 0 side quest (report only):** price the IBKR route — whether IBKR's API exposes Kalshi hourlies and at what commission, and ForecastEx's ladders — versus Webull's $0.02/side. If IBKR is strictly cheaper for this bot, say so plainly before deliverable 8 is built for Webull.

## PHASE GATES

- Sandbox → live canary ($500–1,000, 1–5 contracts, S1 index only): recorder ≥ 10 trading days; ≥ 300 paper settlements; paper realized edge inside the recorded distribution; zero governor bypass paths; reconciliation drift zero; Phase 0 tests recorded.
- Canary → 2×: ≥ 400 live settlements in the traded price bucket **or** Wilson lower bound of realized edge > 0 at 95 %, whichever comes first; profit factor ≥ 1.3; max drawdown < 25 % of deployed.
- 2× → 5×: ≥ 1,000 cumulative settlements; Wilson lower bound > 1 ¢ net; no lockout in the last 20 trading days. Demote one step after 3 consecutive losing weeks or −3 % deployed in a day.

## HOW TO WORK

Start by restating the plan in your own words in `docs/PLAN.md`, listing every UNVERIFIED item as a checkbox and any disagreement you have with the plan. Then build deliverable 1. After each deliverable: run `ruff`, `mypy --strict`, `pytest`; commit with a clear message; report what was verified against a live endpoint and what was not. Ask only when a decision would materially change the build; otherwise take the conservative option and note it in `docs/DECISIONS.md`.
