# PICADOR — Webull OpenAPI Prediction-Market Bot: Master Plan (v1.1, 2026-09-19)

> **v1.1:** three gap-research agents corrected several v1.0 facts. Read `PICADOR_RESEARCH_ADDENDA.md` alongside this file; where they disagree, the addenda win. Headline changes: BTC/ETH settle on a 60-second BRTI average; the S&P 4pm value is a real-time print, not the official close; SPX/XSP options data is required from day one (SPY strikes are too coarse); S4 near-expiry taking has negative measured markout and is removed; Webull crypto hours may be 24/7 and TIF may include GTC/IOC/FOK (both to be tested); sandbox = paper trading; MQTT has hard connection/symbol limits; the 2FA token dies after 15 idle days; Kalshi's authenticated WebSocket carries the resolution feed; Webull is under state enforcement for sports; tax treatment is unsettled; IBKR may be a cheaper API route and must be priced in Phase 0.

**Name.** *Picador*: the rider who wears the bull down with small, precise jabs, over and
over. A bot that chips cents out of Webull's event contracts thousands of times fits the
name. No trading product currently uses it. (Alternates if you dislike it: *Banderilla*,
*Toreo*, *Longhorn*.)

**Scope.** A 24/7-capable automated system that trades the Kalshi-backed event contracts
Webull exposes through its official OpenAPI, using Webull's own options and crypto legs for
fair value and hedges where it helps. Everything below was verified today against Webull's
developer documentation, Webull's help center, the official Python SDK source (cloned),
and live pulls from Kalshi's public API. Numbers that came from third parties are marked.
This supersedes the venue choice in `PREDICTION_MARKET_ARB_STRATEGY.md`; the fee and
market-structure research there still applies. Nothing here builds on any other code in
this repository.

---

## 0. The verdict in one paragraph

Webull's OpenAPI is real and complete enough for this: event contracts have discovery,
snapshot, depth, bars, tick, MQTT streaming, gRPC order/position events, preview, place,
replace and cancel, plus a sandbox and (since July 2026) a paper-trading environment that
includes event contracts. But three facts shape everything. **(1) Fees are a flat $0.02 per
contract per side** (Kalshi $0.01 + Webull $0.01), with no maker discount, so the
maker-rebate "chip away" strategies that work on Kalshi direct or Polymarket are 4 to 8
times more expensive on Webull and are off the table there. **(2) Only LIMIT/DAY,
buy-to-open and sell-to-close orders exist**, so shorts are expressed as buying NO, and
there is no GTC or IOC. **(3) Index and crypto events trade only weekdays** (index 8am–4pm
ET, crypto 8am–6pm ET) on Webull even though Kalshi's own BTC hourlies run around the
clock; only sports are 24/7. The strategies that survive those facts are **hold-to-
settlement mispricing trades on the hourly S&P 500, Nasdaq-100, BTC and ETH contracts,
priced against options-implied and spot-implied probabilities**, plus intra-event
consistency arbitrage across strikes. Those pay a single $0.02 (entry only; settlement is
free), which at mid prices is exactly what a Kalshi taker pays anyway. The academic and
practitioner evidence for options-implied mispricing in Kalshi index contracts is the best
edge evidence we have in the whole prediction-market space. That is Picador's core.

---

## 1. Webull OpenAPI — verified facts

### 1.1 Access
| Item | Fact | Source |
|---|---|---|
| Who | Individuals and institutions with a Webull US account; "Individual Application Process" on the website; review 1–2 business days; no extra charge for API access | developer docs Getting Started / FAQ |
| Credentials | App Key + App Secret generated in the Webull portal after approval; HMAC-signed requests; 2FA token persisted to a token dir (`WEBULL_OPENAPI_TOKEN_DIR`), SDK polls 5 s for up to 300 s at first verification | SDK samples, FAQ |
| IP whitelist | Not required for individuals | FAQ (third-party summary) |
| Environments | Sandbox `api.sandbox.webull.com` (shared test accounts, no application needed); gRPC events `events-api.sandbox.webull.com`; production `api.webull.com` / `events-api.webull.com`; paper trading via OpenAPI rolled out July 2026 covering stocks, options, crypto, futures, bonds, event contracts | skills repo `api_reference.md`, PR Newswire |
| Protocols | HTTP (REST), MQTT (market data streaming), gRPC (order/position events) | SDK |
| SDKs | Official Python 3.8–3.14 (`webull-openapi-python-sdk`, Apache-2.0, last commit 2026-09-16), Java; official agent skills, MCP server, Go CLI | GitHub webull-inc |
| Region | US only for event contracts (skills matrix: event contracts "Yes" only under US) | skills repo |

### 1.2 Rate limits (production; sandbox is 30/60s for almost everything)
| Endpoint | Limit | Implication for Picador |
|---|---|---|
| Place order | 600 / 60 s | 10 orders/s ceiling per app key |
| Cancel order | 600 / 60 s | same |
| Preview order | 150 / 10 s | can preview every order for fee/BP check |
| Account balance | **2 / 2 s** | do not poll; consume gRPC position/order events |
| Account positions | **2 / 2 s** | same |
| Open orders | 2 / 2 s | same |
| Market data REST (snapshot, depth, bars, tick) | 60 / 60 s | 1 request/s across *all* symbols: discovery only; live data must come from MQTT |
| Streaming subscribe calls | 60 / 60 s | subscribe in batches at startup |
| Auth server-to-server | 10 / 10 s | |
| Enforcement | per app key, independent counters, HTTP 429, repeated abuse → temporary IP block | rate-limits page |

### 1.3 Event contract order rules
| Rule | Value |
|---|---|
| Order types / TIF | `LIMIT` only, `DAY` only |
| Sides | `BUY` to open, `SELL` to close; **no sell-to-open**; `event_outcome` = `yes` or `no` |
| Price | $0.01–$0.99, 1¢ tick (some markets $0.0001 per broker-API guidance) |
| Quantity | 0–2 decimals; max 50,000 contracts per order (trade-API page) — broker-API page says 500,000 contracts / $50,000 notional; treat 50,000 as the binding number until sandbox says otherwise |
| `client_order_id` | ≤ 32 chars, alphanumeric only, unique per account |
| Request fields | `combo_type=NORMAL`, `instrument_type=EVENT`, `market=US`, `symbol` (Kalshi ticker, e.g. `KXINXU-26SEP21H1000-T7769.9999`), `entrust_type=QTY`, `quantity`, `limit_price`, `side`, `event_outcome`, `time_in_force=DAY`, `order_type=LIMIT` |
| Endpoints (SDK `order_v3`) | `preview_order`, `place_order`, `replace_order` (quantity/limit_price by client_order_id), `cancel_order`, `get_order_detail`, plus `account_v2` balance/positions |
| Leverage / PDT | Not leveraged; exempt from PDT (margin PDT flags can still cut buying power) |

### 1.4 Fees, hours, account
| Item | Value |
|---|---|
| Fees | $0.01 exchange + $0.01 Webull **per contract per side, on opening and closing trades**. Holding to settlement incurs no closing fee → $0.02 total; round trip before expiry → $0.04 total. Same in API as in app |
| Hours (Webull) | Index events Mon–Fri 8:00–16:00 ET; crypto Mon–Fri 8:00–18:00 ET; economics Mon–Fri 8:00–23:00 ET; sports 24/7 except maintenance; Webull may change hours without notice |
| Account | Separate Event Contract account (own statements) opened from an existing Individual Cash or Margin account; buying power shared: cash = settled cash − provisional cash; margin = lesser of overnight/intraday BP excess − provisional cash; no minimum; carried by Webull Futures LLC (CFTC FCM); not SIPC |
| Eligibility | US residents; sports contracts unavailable to NV, MD, CT, MI residents; category agreements (e.g. sports) must be signed in-app |
| Settlement | Automatic at $1/$0; proceeds "credited to available balance immediately" and withdrawable same day; 1099 reporting |
| Catalog | Curated subset of Kalshi: financial hourlies/dailies, crypto hourlies, Fed/CPI/jobs, sports, politics, culture, climate, science; no weather buckets or fine-grained CPI strikes (third-party) |

### 1.5 Market data
| Item | Value |
|---|---|
| Event REST | categories, series, events, instruments (with `expiration_date_after`, pagination), snapshot (yes/no bid/ask + sizes, last, volume, OI), depth (yes_bids / no_bids ladders), bars (1m+), tick |
| Event streaming | MQTT, `Category.US_EVENT`, subscribe types `QUOTE`, `SNAPSHOT`, `TICK`; decoders exist for snapshot, depth and tick payloads (protobuf) |
| Options data | REST snapshot ≤ 20 symbols per query, bars, tick, contract lists; `Category.US_OPTION` only (equity/ETF options: SPY, QQQ); index options (SPX/XSP) not evidenced in the SDK; requires a **separate OpenAPI market-data subscription** (app subscriptions do not carry over); one device per L1/L2 subscription |
| Stocks/ETF L1 | Free streaming | |
| Futures data | Paid | |

---

## 2. Fee reality: Webull vs Kalshi direct (per contract)

Kalshi taker = ceil(0.07·p·(1−p)·100)/100, maker ≈ ¼ of that. Webull = $0.02 flat per side.

| Price p | Kalshi taker | Kalshi maker | Webull | Webull ÷ Kalshi maker |
|---|---|---|---|---|
| 0.05 / 0.95 | $0.01 | ~$0.0025 | $0.02 | 8× |
| 0.20 / 0.80 | $0.02 | ~$0.005 | $0.02 | 4× |
| 0.50 | $0.02 | ~$0.005 | $0.02 | 4× |

Consequences:
- **Maker-side set completion, rebate farming and quote-on-both-sides market making are not viable through Webull.** If you want that engine, it must run on a direct Kalshi account (same order book, different fee schedule). Picador is designed so that adding a direct-Kalshi executor later is a config change, not a rewrite.
- **Hold-to-settlement directional-with-an-edge trades cost Webull $0.02 total**, which equals Kalshi taker at mid prices and is 2× Kalshi taker at the tails. That is the regime Picador lives in.
- **Break-even mispricing:** entering at ask `a` and holding, expected profit per contract = `p_true − a − 0.02`. At `a = 0.50` you need `p_true ≥ 0.52` just to break even; the study-grade edges in §4 are in the 3–8¢ range when they appear.
- Round-trip exits before expiry cost $0.04 plus spread. Picador exits early only to cut risk, never to harvest.

---

## 3. The target markets, from live Kalshi data (2026-09-19)

Kalshi's public REST needs no account and returns the *same* books Webull routes into, so Picador reads Kalshi directly for depth and history even while executing through Webull.

| Series | What | Cadence | Strikes | Source of truth | Observed |
|---|---|---|---|---|---|
| `KXINXU` | S&P 500 above X at HH:00 ET | hourly, 10:00–16:00 | 5-point spacing (e.g. 7,770 / 7,765 / 7,760…) | Kalshi (was "e.g. Google Finance"; Kalshi modified source to itself) | Monday 10:00 book on a Saturday: one resting ask 46¢ × 38, no bids; 1¢ tick; position limit **$7,000,000 per member** (contract terms) |
| `NASDAQ100I` | Nasdaq-100 above X | hourly | similar | same | |
| `KXNDQ15M` | Nasdaq-100 15-minute | 15 min | | same | exists; not yet surfaced by Webull as far as we can tell |
| `KXBTC` / `KXBTCD` | BTC above X (`T` tickers) **and** BTC in range (`B` tickers) at HH:00 | hourly incl. overnight and weekends (saw a 02:00Z Saturday expiry) | $100 (ranges) / $250 (thresholds) | CF Benchmarks BRTI | near-the-money spreads 1–3¢ (e.g. 39/40, 23/24), tails 1¢ bid vs 8¢ ask; both T and B strikes live in the *same event* |
| `KXETH` | ETH above/in range | hourly | | CF Benchmarks | same shape |
| Fee type (Kalshi direct) | all of the above `quadratic`, multiplier 1 | | | | |

Two structural facts to exploit later: (a) every hourly event is a **strike ladder that must be a valid CDF** (P(>7,760) ≥ P(>7,765) ≥ …); (b) in crypto events **a range contract equals the difference of two threshold contracts** (P(B[87,375–87,625]) = P(T>87,374.99) − P(T>87,624.99)). Both are checkable in integer cents from the Kalshi public book.

---

## 4. Strategy stack (ranked; all hold-to-settlement unless noted)

### S1 — Options-implied fair value on index hourlies/dailies (core)
- **Thesis.** A Kalshi "S&P > K at 4pm" contract is a cash-or-nothing digital. Its fair value is the risk-neutral probability from the SPX/SPY option chain: `P(S_T > K) ≈ (C(K−h) − C(K+h)) / (2h)` on same-day expiries, or the full Breeden–Litzenberger density. A 2022–2024 study of SPXW options vs Kalshi S&P buckets found persistent, tradable inefficiencies, with the Breeden–Litzenberger density the strongest signal (Stevens FSC; summary only, full PDF not retrieved). A 2023 practitioner system traded S&P daily brackets from a vol model (paywalled; methodology not verified).
- **Data.** Webull OpenAPI options snapshots cover `US_OPTION` only, 20 symbols per call, 60 calls/min: enough for **six SPY 0DTE strikes around K every few seconds**, not for a whole chain. SPX/XSP quotes are not evidenced in the SDK. Plan: SPY (and QQQ for Nasdaq) chains via Webull with a live SPY→SPX basis from the index quote; upgrade to an external SPX feed (Cboe/OPRA vendor) only if Phase 0 shows SPY basis noise is eating the edge.
- **Rule.** Compute `p_fair` and a confidence band (bid/ask-implied density spread). Buy YES at ask `a` when `p_fair_low − a ≥ 0.02 + margin`; buy NO when `(1 − p_fair_high) − no_ask ≥ 0.02 + margin`. Hold to settlement. Margin starts at 3¢ and is tuned from Phase 0 data.
- **Size.** Kelly on the estimated edge at quarter-Kelly, capped per market and per hour; depth-walked against the Kalshi book.
- **Hedge (optional, later).** Delta-hedge a portfolio of hourlies with SPY/XSP verticals through the same Webull account when net delta exceeds a cap. Not in v1.

### S2 — Spot-implied fair value on BTC/ETH hourlies
- Same idea with the density from Deribit BTC/ETH options (public API, free) or a short-horizon realized-vol model; underlying = CF Benchmarks BRTI (subscribe to the exact resolution feed; a Coinbase mid is not the oracle, and the alpha playbook documents systematic "fade" edges when scanners use the wrong feed).
- Webull-side constraint: weekdays 8am–6pm ET only; overnight and weekend BTC hourlies exist on Kalshi but are unreachable through Webull.

### S3 — Ladder and range/threshold consistency inside one event
- Detect CDF inversions across strikes and `B ≠ T_low − T_high` breaks in crypto events from the Kalshi public book, in integer cents, sized to the thinnest leg, after 2¢ × legs of Webull fees (2-leg trades need ≥ 5¢ gross, 3-leg ≥ 7¢). Evidence says such baskets are rare and thin (two Kalshi scanners this month found zero survivors after exact fees on the general catalog) — but hourly crypto tails with 8¢ asks and 1¢ bids are precisely where they surface. Scanner in v1, executor behind a gate.

### S4 — Last-minutes convergence on hourlies
- With minutes to expiry the index or BRTI value is known to within ticks; contracts still offered at 90–97¢ pay `1 − a − 0.02`. Needs the resolution feed, an accurate clock, and a rule that refuses when the underlying is within a volatility-scaled band of K. Fast movers already do this; Picador wins on breadth (every strike, every hour) not speed.

### S5 — Sports (the only 24/7 category on Webull)
- Deprioritized for v1: market-making is dead at 2¢/side, and in-game convergence needs licensed score feeds. Revisit after S1–S4 graduate.

### Not through Webull
- Maker set completion, rebate farming, market making (fee), cross-venue Polymarket arbitrage (settlement-rule divergence and capital lockup; and Polymarket US is a separate liquidity pool). If wanted later: direct Kalshi executor module.

---

## 5. Architecture

```
Kalshi public REST/WS (no auth) ─┐  full depth, history, series metadata
Webull MQTT (US_EVENT quotes)   ─┼─▶ book cache ─▶ scanners (S1..S4) ─▶ RiskGovernor ─▶ WebullExecutor ─▶ Webull OpenAPI
Webull US_OPTION snapshots (SPY/QQQ) ┤                                     │                 (preview→place→events)
Deribit options / BRTI / index feed ─┘                                     ▼
                                        journal (SQLite) ◀── Webull gRPC order/position events ◀──┘
                                        reconcile daily from Webull order history/executions
                                        Prometheus + Telegram
```

Design rules specific to Webull:
1. **Never poll balance/positions** (1/s cap). Subscribe with `TradeEventsClient` (gRPC) for order and position status and treat REST reads as a 60-second reconciliation.
2. **All live prices from MQTT**; REST snapshot/depth only for discovery and reconciliation (1/s budget shared by everything).
3. **Preview before place** for any order above a notional threshold: it returns fee and buying-power outcome and has a generous 150/10 s budget.
4. **Express short as BUY NO**; never emit `SELL` unless a position exists (the API rejects sell-to-open).
5. **DAY-only means re-arm at 8:00 ET**; GTC is emulated by the bot, IOC by place + immediate cancel with a fill check on the order event.
6. `client_order_id` = 32-char alphanumeric built from a per-process prefix + counter; persisted so restarts never reuse one.
7. **Fee model is exact:** `0.02 × contracts` per opening and per closing trade, zero at settlement; Kalshi's quadratic formula is used only for the direct-Kalshi executor.
8. **Hours gate**: per category, from config, with a manual override because Webull may change hours without notice; no opening orders within N minutes of each category's close except S4.
9. **Governor token**: scanners emit `Opportunity` data; only `RiskGovernor.evaluate()` mints an `ApprovedOrder`; the executor constructor-checks the token. Caps: per-market notional, per-event notional, per-hour count, total deployed, daily loss → cancel all + persisted lockout, staleness (MQTT silence, feed silence, clock drift) → halt.
10. **Reconcile PnL only from Webull executions/order history**, never from the journal; unreconciled rows are excluded from graduation gates.
11. **Sandbox → paper → live** with identical code paths; the only difference is the endpoint and the account id.
12. Secrets: App Secret and 2FA token dir on the VPS only; read-only key where Webull offers scopes; no unofficial Webull library (ToS risk, image-captcha logins, 2023-vintage code).

Stack: Python 3.12, `webull-openapi-python-sdk` (Apache-2.0) for execution and streaming, `httpx`/`websockets` for Kalshi public data and Deribit, `pydantic` config with ceilings, `structlog`, DuckDB/Parquet recorder, SQLite journal, Prometheus, systemd on a US-East VPS. Design patterns borrowed (not code): poly-maker's pure quoting function + regime machine + risk manager; the Kalshi exact-cent LP scanner; the alpha playbook's graduation ladder and antipatterns; the official `webull-openapi-skills` `guards.py` local order validation (Apache-2.0, can be copied).

Repos surveyed for Webull specifically: official SDK, skills, MCP, CLI (all active, Apache/MIT); `samdotson61/webull-trading-bot` (MIT, 5.2k LOC, paper-first equities, useful broker abstraction); `jashkad8967/webull-intraday-trading-bot` (38k LOC, no license, CI-heavy, equities); `tedchou12/webull` unofficial (2023, ToS-violating endpoints — do not use); the 2020–2022 Discord-alert bots on the unofficial API — irrelevant.

---

## 6. Phased plan with exit criteria

| Phase | Build | Exit criterion |
|---|---|---|
| **0. Access + record** (wk 1–2) | Apply for OpenAPI; open Event Contract account; buy the OpenAPI options data subscription; run the SDK against sandbox; recorder: Kalshi public books for all hourly index/crypto events + Webull MQTT event quotes + SPY/QQQ 0DTE near-K snapshots + Deribit + BRTI; nightly notebooks computing `p_fair − market` distributions, ladder/range violations, convergence windows | Category report with counts/day, median edge in ¢, depth, by hour; go/no-go per strategy |
| **1. Paper** (wk 2–4) | Full engine on Webull paper trading; replay simulator over recordings with fills only when price trades through | ≥ 200 paper settlements; realized edge within recorded distribution; zero governor bypass paths |
| **2. Live canary** (wk 4–6) | $500–1,000 event buying power; 1–5 contracts per trade; S1 (index) only | ≥ 100 settled trades; live vs paper degradation measured; reconciliation drift zero |
| **3. Scale ladder** | Wilson-lower-bound gates: 1× → 2× → 5× on N ≥ 30/60 fresh trades, profit factor ≥ 1.5; demote on 3 bad windows or −3 % deployed | Capital follows evidence |
| **4. S2–S4** | Add BTC/ETH fair value, ladder/range arbitrage executor, convergence | Each repeats 1–3 |
| **5. Optional direct-Kalshi executor** | Same scanners, maker fees, 24/7 crypto | Only if Phase 0 shows maker edge worth a second account |

Budget: $2–5k event buying power to start (Webull's flat 2¢ makes sub-$1k accounts fee-bound); ~$30–80/mo VPS + Webull OpenAPI market-data subscription (price only visible in the portal); no other data cost until an SPX feed is proven necessary.

---

## 7. Risks and compliance
- US residents only; sports blocked in NV/MD/CT/MI; state litigation against prediction markets is live in several states.
- Event account is not SIPC; funds are FCM-segregated.
- Webull can change hours and the curated catalog without notice; Kalshi can put a market "under review" (settlement timer 60 s; reviews can take longer).
- Index source ambiguity: Kalshi lists "for example, Google Finance" and states it modified the source agency to itself; Picador's convergence and fair-value legs must use the same series the contract references, and refuse to trade within a band of K.
- Position limit $7M/member per index series is not binding; Webull's per-order caps and buying power are.
- Tax: 1099 reporting; keep the journal export-ready.

---

## 8. Decisions needed before Phase 0 code
1. Confirm you can open the Webull Event Contract account (US resident; not in a sports-restricted state if sports matter).
2. Starting event buying power and daily loss lock (proposal $3k, −3 %/day lock, 20 % max per event, 10 % max per market).
3. Approve buying the OpenAPI options market-data subscription in Phase 0 (needed for S1).
4. Repository: new `picador` repo (recommended) vs a folder here.
5. Do you want the direct-Kalshi executor scoped in from day one (second account, maker fees, 24/7 crypto) or deferred to Phase 5?

---

## 9. Sources
- Webull OpenAPI docs: https://developer.webull.com/apis/docs/ · Getting Started https://developer.webull.com/apis/docs/getting-started/ · Rate Limits https://developer.webull.com/apis/docs/rate-limits/ · Event Contract Trading https://developer.webull.com/apis/docs/trade-api/event-contract/ · Broker event guidance https://developer.webull.com/apis/docs/broker-api/event-contract-guidance/ · Changelog https://developer.webull.com/apis/docs/changelog/ · FAQ https://developer.webull.com/apis/docs/faq/ · Market data FAQ https://developer.webull.com/apis/docs/market-data-api/faq/
- Official repos: https://github.com/webull-inc/webull-openapi-python-sdk · https://github.com/webull-inc/webull-openapi-skills · https://github.com/webull-inc/webull-openapi-mcp · https://github.com/webull-inc/webull-openapi-cli
- Webull event contract rules and fees: https://www.webull.com/learn/courseware/fZidum/Trading-Event-Contracts · https://www.webull.com/help/faq/11052-All-about-Event-Contracts · https://www.webull.com/help/faq/11053-Trading-Event-Contracts · https://www.webull.com/trading-investing/prediction-markets
- Webull–Kalshi partnership and products: https://www.prnewswire.com/news-releases/webull-connects-to-kalshi-to-offer-investors-innovative-prediction-markets-302373541.html · https://www.nasdaq.com/press-release/webull-launches-kalshis-hourly-crypto-markets-investing-platform-2025-06-10 · https://www.prnewswire.com/news-releases/webull-unveils-enhanced-paper-trading-experience-with-professional-grade-and-openapi-multi-asset-simulation-302830191.html
- Third-party fee/catalog analysis: https://marketmath.io/blog/webull-prediction-markets
- Kalshi: public API `https://api.elections.kalshi.com/trade-api/v2` (series, markets, orderbook pulled 2026-09-19) · S&P contract terms https://kalshi-public-docs.s3.amazonaws.com/contract_terms/INX.pdf · fee schedule https://kalshi.com/docs/kalshi-fee-schedule.pdf · SDKs https://docs.kalshi.com/sdks/overview
- Options-implied mispricing evidence: https://fsc.stevens.edu/event-contract-mispricing-via-options-implied-probabilities/ (summary via search; page currently 404) · https://github.com/quantgalore/kalshi-trading · https://www.quant-galore.com/p/prediction-markets-are-literally (paywalled)
- Webull options/index options/crypto: https://www.webull.com/trading-investing/index-options · https://www.webull.com/help/faq/11091-Fees-and-Limits
- Unofficial API risk: https://github.com/tedchou12/webull · https://zuplo.com/learning-center/webull-api
- Prior research in this repo: `PREDICTION_MARKET_ARB_STRATEGY.md`, `WEEVIL_REPO_SURVEY.md` (the "Weevil" name was a transcription of "Webull"; the repo audit stands)
