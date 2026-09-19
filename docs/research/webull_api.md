# Webull OpenAPI — what PICADOR_WEBULL_PLAN.md missed or got wrong (research, 2026-09-19)

Method. Every developer.webull.com page has a Markdown twin (`<page>.md`, indexed at
https://developer.webull.com/apis/llms.txt) that embeds the raw OpenAPI JSON for reference
pages; those were downloaded and parsed (saved under `scratchpad/wbdocs/`). The official
Python SDK clone (`webull-openapi-python-sdk`, `webull/core/__init__.py` says 3.0.1, last
commit 2026-09-16) was read directly. GitHub issue pages, Webull help-center pages and press
releases were fetched. Items marked **UNVERIFIED** could not be confirmed from any source.

Legend: (D) = developer docs, (S) = SDK source, (H) = Webull help center, (G) = GitHub.

---

## 1. Event-contract market-data payloads, MQTT topics, limits

### REST (all `GET`, `x-version: v3`, category param only accepts `US_EVENT`)
Source: https://developer.webull.com/apis/docs/reference/event-snapshot.md ,
…/event-depth.md , …/event-bars.md , …/event-tick.md (raw OpenAPI JSON inside each).

| Endpoint | Path | Params | Response fields |
|---|---|---|---|
| Snapshot | `/market-data/event-contracts/snapshots/list` | `symbols` (≤ **100** per query) | `instrument_id, symbol, name, price, volume, last_trade_time (ms int), open_interest, yes_bid, yes_bid_size, yes_ask, yes_ask_size, no_bid, no_bid_size, no_ask, no_ask_size` — prices are **dollars** ("0.13"), not cents, despite the SDK docstring saying "usd_cent" |
| Depth | `/market-data/event-contracts/depths/list` | `symbol` (one), `depth` (string, "default 10 levels"; no documented max) | `instrument_id, symbol, quote_time (ms), yes_bids[], yes_asks[], no_bids[], no_asks[]` each `{price,size}` — **four ladders in REST**, and the page text still says "Only yes/no bids are returned (a yes bid at X equals a no ask at 100-X)"; treat `*_asks` as derived mirrors until observed |
| Bars | `/market-data/event-contracts/bars/list` | `symbols` (≤100), `timespan` ∈ `M1,M5,M15,M30,M60,M120,M240,D`, `count` 1–**1200** (default 200), `real_time_required` (required; minute bars only) | `instrument_id, symbol, result[{open,close,high,low,volume,time (ISO UTC)}]` |
| Tick | `/market-data/event-contracts/ticks/list` | `symbol`, `count` 30–1200 | `result[{time (ms), yes_price, no_price, volume, side ("yes"/"no" = taker side), trade_id (uuid)}]` |

Rate limit: 60/60 s production, 30/60 s sandbox for each of these (D rate-limits page). The
plan's "1 request/s shared" reading is right per endpoint, but the counters are **per
endpoint** ("Each endpoint has its own independent rate limit counter"), so snapshot+depth+
bars+tick together give 4 req/s, not 1. https://developer.webull.com/apis/docs/rate-limits.md

### MQTT streaming
Source: https://developer.webull.com/apis/docs/market-data-api/data-streaming-api.md ;
SDK `webull/data/quotes/subscribe/message.proto`, `payload_type.py`, `data_streaming_client.py`,
`webull/data/internal/quotes_client.py`.

- Endpoints: `data-api.webull.com:1883` (TCP, SDK default `transport="tcp"`, `tls_set()` on
  1883) or `wss://data-api.webull.com:8883/mqtt`. Sandbox streaming host
  `data-api.sandbox.webull.com` (skills repo `api_reference.md` lists `api.sandbox.webull.com`
  for US MQTT — inconsistent; SDK `endpoints.json` has **no sandbox entries at all**, so the
  sandbox hosts must be passed explicitly via `http_host`/`mqtt_host`).
- CONNECT: ClientId = your `session_id`, username = App Key, password = anything.
- **Hard limits**: max **5 concurrent connections per App Key** (error 105); server retains
  disconnected state ~1 min; **server pushes at most 3 messages per second per connection**
  (this is a throttle on push frequency; it is the binding constraint for a many-symbol book
  cache); subscriptions are **not restored after reconnect** — must re-subscribe.
- Subscribe is a REST call `POST /market-data/streaming/subscribe` with `session_id, symbols`
  (≤ **100 symbols per call**), `category`, `sub_types`, optional `depth` (L2 levels, default
  10, "US stocks max 50"), `grab` (snapshot on subscribe; not exposed by the SDK), and
  `overnight_required`. Rate 60/60 s prod, 30/60 s sandbox. Max symbols per *session* is
  **UNVERIFIED** (not documented anywhere found).
- `SubscribeType` enum (S): `QUOTE, SNAPSHOT, TICK` for every category incl. `US_EVENT`
  (`Category.US_EVENT = 13`; note `HK_FUTURES` also = 13 in the SDK enum — harmless for US).
- Topics for event contracts: `event-quote` (protobuf `EventQuote{basic, yes_bids[], no_bids[]}`
  — **bids only, no asks, no level cap documented**), `event-snapshot` (`EventSnapshot`, same 13
  fields as REST snapshot), `event-tick` (`EventTick{yes_price,no_price,volume,side,trade_id,time}`),
  `notice` (JSON `{type:"status", rtt, drop, sent}`), `echo` (heartbeat). `Basic{symbol,
  instrument_id, timestamp}` (+ `trading_session` in the SDK proto, absent from docs).
- So: **depth IS streamed** (as `event-quote`), but only the yes/no bid ladders; REST depth is
  the only place `yes_asks/no_asks` appear.
- Reconnect (S): `reconnect_on_failure=False`; the SDK's own loop retries forever with a fixed
  **10 s** delay only on rc 3/5 or transport exceptions; the sample re-subscribes inside
  `on_connect_success`, which is the pattern Picador must copy.
- Market data permission: **Event contracts need no additional subscription** (D market-data
  overview "Market Data Permissions" table).
  https://developer.webull.com/apis/docs/market-data-api/overview.md

## 2. gRPC trading events

Source: https://developer.webull.com/apis/docs/reference/custom/subscribe-trade-events.md ,
https://developer.webull.com/apis/docs/reference/custom/subscribe-position-events.md ,
SDK `webull/trade/trade_events_client.py`, `events/default_retry_policy.py`, `events/types.py`.

- Hosts `events-api.webull.com:443` / `events-api.sandbox.webull.com` (docs' Python sample
  still says the old `us-openapi-alb.uat.webullbroker.com`). Auth = app key + HMAC over the
  serialized protobuf (SDK forces **HMAC-SHA256**; metadata `x-app-key, x-signature-algorithm,
  x-signature-version, x-signature-nonce, x-timestamp, x-signature`); **no `x-access-token`**
  is sent on gRPC (S).
- SDK subscribes with `subscribeType=7` (bitmask 1 order | 2 position | 4 option); docs say
  order page "only 1", position page "only 2". `EVENT_TYPE_ORDER=1024`, `EVENT_TYPE_POSITION=1028`,
  `EVENT_TYPE_OPTION=1032`.
- **Order payload** (JSON, `contentType application/json`): `request_id, account_id,
  client_order_id, order_id, instrument_id, order_status (SUBMITTED|FILLED|FAILED|CANCELLED…),
  symbol, qty, filled_price, filled_qty, filled_time, side, scene_type, category, order_type`.
  `scene_type` ∈ `FILLED` (partial), `FINAL_FILLED`, `PLACE_FAILED`, `MODIFY_SUCCESS`,
  `MODIFY_FAILED`, `CANCEL_SUCCESS`, `CANCEL_FAILED`. **Fills are pushed only as these order
  events** (one message per partial fill, then FINAL_FILLED); there is no separate execution
  stream, no `event_outcome`, no fee/commission field on the Trading-API push (fee fields were
  added only to the Broker-API push per changelog 2026-08). Per-fill price on a partially
  filled order is `filled_price` = running average per Order Detail semantics — reconcile via
  Order Detail `fees[]`/`commission{}`.
- **Position events (new, event-contract settlement)**: subscribeType 2 pushes
  `{event_name, yes_condition, settle_result, settle_side, quantity, cost, settle_amount}` at
  settlement; "requires the latest SDK version". No symbol/client_order_id in the sample
  payload — map by `event_name`+`yes_condition` or reconcile via positions REST.
- Control events: `SubscribeSuccess(0), Ping(1) (~60 s), AuthError(2), NumOfConnExceed(3)
  (limit undocumented), SubscribeExpired(4)`.
- **Reconnect semantics (S, important)**: `DefaultSubscribeRetryPolicy` retries forever with
  fixed 5 s delay only when the stream raises `grpc.RpcError` with status `UNAVAILABLE|INTERNAL|
  UNKNOWN`. If the server **ends the stream cleanly** (e.g. after `SubscribeExpired`, which the
  SDK merely logs at FATAL), the `for response in iterator` loop exits, `should_retry()` sees
  `grpc_status_code=None` → `NO_RETRY` → `do_subscribe()` **returns silently**. Picador must
  wrap `do_subscribe` in its own supervisor loop and treat `SubscribeExpired`/`AuthError` as
  resubscribe triggers. `_build_request` also `print()`s the signature to stdout on every
  (re)subscribe — patch or redirect.
- Field trap: G issue #13 (2026-07-24, open, no maintainer reply) reports SubscribeSuccess +
  pings but **zero ORDER_STATUS_CHANGED messages** on a US margin account with real orders.
  https://github.com/webull-inc/webull-openapi-python-sdk/issues/13 — the plan's "never poll,
  consume gRPC" rule needs a REST fallback (Open Orders 2/2 s) as a liveness check.

## 3. Event instrument discovery

Source: https://developer.webull.com/apis/docs/reference/event-market-list.md ,
…/event-series-list.md , …/event-events-list.md , …/event-categories-list.md ; SDK
`get_event_instrument_request_v2.py`.

- Hierarchy: categories → series (`frequency` ∈ `HOURLY, DAILY, WEEKLY, MONTHLY, ANNUAL,
  ONE_OFF, CUSTOM`) → events (`symbol, name, status ACTIVE|INACTIVE, short_name, strike_date,
  strike_period, mutually_exclusive`) → markets.
- **Markets/instruments v2** `GET /trading/instruments/event-contracts/markets/list`, params
  `series_symbol`, `event_symbol`, `symbols` (≤100), `expiration_date_after` (default = today
  inclusive), `pagination_key` (opaque base64 cursor; decoded example shows `pageSize:500`;
  key absent on last page). Response per market: `series_id, series_symbol, series_name,
  event_symbol, event_name, instrument_id, symbol, name, yes_condition, last_trading_date
  ("Last Notice Day", date only), status (NOT_SET|LISTING|DELISTING|OTHER|UNRECOGNIZED),
  tradable_status (OC tradable | CO liquidate-only | NT non-tradable), can_close_early,
  expected_exp_date, latest_exp_date, payout_date, fractionable, price_ranges[{start,end,step}]`.
- **Missing vs the plan's assumptions**: there is **no `strike`, no `stop_trading_time`, no
  settlement-source, no category field, and no intraday timestamp at all** — every date is a
  calendar date. For hourlies the exact expiry hour must be parsed from the Kalshi ticker
  (`KXINXU-26SEP21H1000-T7769.9999` → 10:00 ET) or pulled from Kalshi's public API. The
  broker-guidance page's `stop_trading_time` is a Kalshi concept not surfaced by this endpoint.
- Enumerating only open hourly index/crypto contracts: series list filtered by
  `category=FINANCIALS` / `CRYPTO`, keep `frequency=HOURLY`; then markets list per
  `series_symbol` with `expiration_date_after=<today>`, keep `status=LISTING` and
  `tradable_status=OC`; page through `pagination_key`. Whether Webull's "curated subset"
  appears as absent series or as `NT` markets is **UNVERIFIED**. Rate 60/60 s per endpoint.
- `price_ranges.step` is the tick (e.g. 0.01, or 0.0001 for sub-cent markets); `fractionable`
  flags 0.01-contract markets. Changelog: fractional support 2026-03-07, `category_id` became
  integer 2026-09-05, cursor pagination migration 2026-09-05.
  https://developer.webull.com/apis/docs/changelog.md

## 4. Order placement corner cases

Source: https://developer.webull.com/apis/docs/reference/common-order-place.md ,
…/common-order-preview.md , …/common-order-replace.md , …/common-order-cancel.md ,
…/order-batch-place.md , …/order-detail.md , https://developer.webull.com/apis/docs/trade-api/event-contract.md ,
https://developer.webull.com/apis/docs/broker-api/event-contract-guidance.md ,
https://www.webull.com/help/faq/11053-Trading-Event-Contracts .

- **TIF is not DAY-only in the API schema.** The place/replace/order-detail specs say "Event
  trading supports the following Time in Force (TIF) values: DAY, GTC, IOC, GTD, and FOK"
  (`expire_date` for GTD), while the narrative event-contract page and the courseware still
  say LIMIT/DAY only. The Webull app help page lists Day/IOC/FOK/GTC/GTD for event contracts.
  → Test IOC/GTC in sandbox before building the "emulate GTC/IOC" machinery in the plan.
- **A market-style order exists**: `event_trade_mode` ∈ `TRADE_IN_AMOUNT | TRADE_IN_CONTRACT`
  ("executes at the best available market price; limit_price is ignored"), and
  `entrust_type=AMOUNT` is allowed for events (BUY only, TIF must be FOK). This is the app's
  "Quick Order". Useful for S4 convergence sweeps; dangerous by default.
- `quantity`: "Event contract trading accept 0-2 decimal places on input"; fractional fills
  possible on `fractionable` markets even for whole-number orders (guidance page).
- **Max size**: Trading-API page "Maximum quantity per order: 50,000 contracts"; Broker-API
  guidance "Maximum Order Amounts: $50,000 / Maximum Order Quantity: 500,000". No spec field
  encodes either. **UNVERIFIED** which applies to retail OpenAPI; the notional cap ($50k) is
  the more plausible binding one at prices ≥ $0.10. Keep the plan's 50,000 and add a $50,000
  notional guard.
- `combo_type` must be `NORMAL` for EVENT; **batch-place is EQUITY-only** (`instrument_type`
  enum = `[EQUITY]`, 150/60 s); OTO/OCO/MASTER combos are not available for events.
- **Preview response has only `estimated_cost` and `estimated_transaction_fee`** — no
  buying-power-after field. Balance for `EVENTS_CASH` accounts: `total_cash_balance`,
  `total_net_liquidation_value`, `cash_balance`, `settled_cash`, `unsettled_cash`,
  `day_trades_left` are documented as **empty**; use `account_currency_assets[].buying_power`.
  Account list exposes `account_class=EVENTS_CASH` / `account_label="Events Cash"`; the event
  account is a **separate `account_id`** (MCP repo resolves it automatically).
- Replace: `modify_orders[{client_order_id, quantity, limit_price, time_in_force,…}]` keyed by
  the **same** `client_order_id` (no new id is issued; response echoes `client_order_id` and
  `order_id`); gRPC pushes `MODIFY_SUCCESS/MODIFY_FAILED`. Whether a replace loses queue
  priority on Kalshi is **UNVERIFIED** (Kalshi's own amend does).
- Cancel-after-cutoff: guidance page — unfilled orders can be cancelled "up until the
  contract's trading cutoff time"; during the settlement/halt period "New orders cannot be
  placed, and existing orders cannot be modified". App help: DAY orders auto-cancel at
  **12:10 AM ET the following day** (so an unfilled DAY order on a 4 pm hourly is not purged at
  4 pm; it dies with the market halt or at 00:10).
- Error model: HTTP 200 success; 401 `{error_code:"UNAUTHORIZED"}`; **417 = business error**
  `{error_code, message}` (examples `OPENAPI_NO_NIGHT_TRADING_TIME`, `OPENAPI_NO_TRADING_TIME`,
  `INVALID_PARAMETER`); 429 rate limit; 500 `SYSTEM_ERROR`. Trading FAQ: `INVALID_TOKEN` is
  usually a **signature mismatch**; missing event-contract agreement returns an error carrying a
  **signing URL**. No consolidated error-code table exists on the new site (the legacy
  `/api-doc/develop/error/` link now serves the landing page).
- Order statuses (Order Detail): `PENDING, SUBMITTED, CANCELLED, FILLED, FAILED, PARTIAL_FILLED`;
  Order Detail returns `event_outcome`, `event_trade_mode`, `filled_price` (avg), `fees[]`,
  `commission{}`. Positions return `instrument_type=EVENT`, `event_outcome`, `cost_price`,
  `last_price`. Query endpoints are 2/2 s.
- Known breakage (G): #1 order-history pagination ignored `last_client_order_id` (Jan 2026,
  open; endpoint has since moved to cursor pagination), #7 order-history 404 in production on
  legacy paths (May 2026, open). Use only the v3 `list_order_history` cursor API and verify in
  production early.

## 5. Paper trading via OpenAPI

- Webull's July 21 2026 release ("OpenAPI access for paperTrade… stocks, options, crypto,
  futures, bonds and event contracts", pricing engine "designed to better reflect live market
  conditions") gives no hosts or steps.
  https://www.prnewswire.com/news-releases/webull-unveils-enhanced-paper-trading-experience-with-professional-grade-and-openapi-multi-asset-simulation-302830191.html
- The developer docs never say "paper". The Individual Application page's **Sandbox tab** is
  the paper flow: Developer Tool → My Application → "**Using OpenAPI service in Paper Trading**"
  button → Sandbox Trading page → apply (auto-approved "within a few minutes") → generate a
  **separate sandbox App Key/Secret** → hosts `api.sandbox.webull.com`,
  `events-api.sandbox.webull.com`, `data-api.sandbox.webull.com`.
  https://developer.webull.com/apis/docs/authentication/IndividualApplicationAPI.md
- So **sandbox == paper trading** for retail; the plan's three-tier "sandbox → paper → live"
  collapses to two tiers. Whether the sandbox account list includes an `EVENTS_CASH` account
  and whether event fills are simulated against live Kalshi books is **UNVERIFIED** (issue #6,
  May 2026, "API currently only supports cash and margin accounts", is still open with no reply).
- Data realism: sandbox serves **15-minute delayed data** for subscription-gated products
  (upgraded to real-time if you hold a production OpenAPI subscription); event data needs no
  subscription, so its sandbox latency is **UNVERIFIED**. Sandbox rate limits are 30/60 s
  almost everywhere. Tokens in sandbox "are valid by default — no 2FA verification needed".

## 6. Sandbox: shared credentials?

- **No shared test App Key/Secret or account IDs are published.** The Getting Started page's
  "shared test accounts" link resolves to `sdk.md#test-accounts`, which only says "refer to
  Trading API Application to create a test account". You need your own sandbox key (minutes).
  https://developer.webull.com/apis/docs/sdk.md
- Hosts (2026-07-08 migration): `api.sandbox.webull.com`, `events-api.sandbox.webull.com`,
  `data-api.sandbox.webull.com`, `broker-api.sandbox.webull.com`. Skills/MCP repos default to
  `WEBULL_ENVIRONMENT=uat` = these hosts. The SDK's `endpoints.json` has no sandbox mapping;
  pass hosts explicitly to `ApiClient.add_endpoint`, `DataStreamingClient(http_host=, mqtt_host=)`,
  `TradeEventsClient(host=)`.
- What is stubbed is **not documented**; known: delayed market data (above), auto-valid tokens,
  lower limits. Issue #17 (Aug 2026, SDK 2.0.18) reports `DataStreamingClient` hitting
  production `/openapi/config` despite sandbox args; in 3.0.1 `http_host` is applied via
  `api_client.add_endpoint` before `ClientInitializer`, so re-test rather than assume.
  https://github.com/webull-inc/webull-openapi-python-sdk/issues/17

## 7. Options market data via OpenAPI

Source: https://developer.webull.com/apis/docs/reference/option-snapshot.md , …/option-tick.md ,
…/option-historical-bars.md , …/option-contract-list.md , market-data overview, help FAQ 126.

- Endpoints: snapshot (`symbols` ≤ **20**, category `US_OPTION` only) returns `price, open,
  high, low, pre_close, volume, change, change_ratio, last_trade_time, close, strike_price,
  gamma, delta, rho, theta, vega, imp_vol, open_interest, quote_time, bid, ask, bid_size,
  ask_size, deal_amount` (top of book only, Greeks included — useful for S1); tick (1 symbol,
  ≤1200); bars (≤20 symbols, `M1…Y`, ≤1200). 60/60 s each. **No option streaming** (streaming
  categories are Stocks, ETFs, Futures, Crypto, Event Contracts) — the plan's "six SPY strikes
  every few seconds" is bounded by 60 snapshot calls/min × 20 symbols.
- Chain/contract list `GET /trading/instruments/options/contracts/list`: filters
  `underlying_symbols, root_symbol (e.g. SPXW — "mainly for index options"), start_date
  (exact expiry), end_date, strike_price_gte/lte, option_type, style, ppind, status`, cursor
  pagination (`pageSize:1000` in the example key). Response includes `underlying_type`
  (`INDEX_CALL_OPTION`…), `settlement_method`, `expired_cycle` (`DAILY` = 0DTE series), `style`.
  The spec's own example symbol is `SPX241220P04200000`, so **SPX/SPXW contracts are
  discoverable**; whether `option-snapshot` returns SPX/XSP quotes is **UNVERIFIED** (category
  enum is only `US_OPTION`; index quotes come from Cboe CGIF which Webull lists as a separate
  feed). Webull trades SPX, SPXW, XSP, NDX, NDXP, VIX, DJX in-app ($0.50/contract + exchange
  fee) https://www.webull.com/trading-investing/index-options .
- SPY 0DTE: SPY dailies exist (`expired_cycle=DAILY`, `start_date=today`); nothing suggests
  exclusion.
- Subscription: "Subscribe **OPRA Real-Time Non-display** for options last sale and quotation";
  OpenAPI subscriptions are separate from app subscriptions and only visible after login
  (avatar → Advanced Quotes → OpenAPI Advanced Quotes). **Price UNVERIFIED** — no public page
  lists it. App-side reference points: OPRA real-time $2.99/mo non-pro, $45/mo pro (help FAQ
  126). Non-display OPRA is typically priced above display; budget accordingly.
  https://developer.webull.com/apis/docs/market-data-api/subscribe-quotes.md
- Also: US stock/ETF L1 for OpenAPI requires "Nasdaq Basic (Level 1) … for Non-Display OpenAPI
  usage" — the plan's "Stocks/ETF L1 free" is true in-app, **not evidenced for OpenAPI**
  (the marketing page says "Free Level 1 Streaming Quotes"; the developer overview lists it
  under subscriptions). Only one device may consume L1/L2 per subscription.

## 8. Open GitHub issues (official Python SDK)

https://github.com/webull-inc/webull-openapi-python-sdk/issues — 14 open verified by
enumeration (GitHub header shows 14–15; #3, #5 are closed PR/issue, #9 closed):

| # | Opened | Title | Relevance |
|---|---|---|---|
| 1 | 2026-01-26 | Order history pagination does not work | reconciliation |
| 2 | 2026-02-06 | Take Profit Stop Loss order for OPTION that is already bought | no |
| 4 | 2026-04-12 | V3 Combo Order Sample | no |
| 6 | 2026-05-13 | paper trading with the api? | **paper** (no reply) |
| 7 | 2026-05-14 | Production API: Order History Endpoint Returns 404 Not Found | reconciliation |
| 8 | 2026-06-03 | OAuth access token support for gRPC trade events subscription? | auth/gRPC |
| 10 | 2026-07-17 | Tradetmus@marketprice-all | spam |
| 11 | 2026-07-17 | Gabriel | spam |
| 12 | 2026-07-17 | Funding | spam |
| 13 | 2026-07-24 | No ORDER_STATUS_CHANGED events pushed despite access token NORMAL + SubscribeSuccess (US margin account) | **gRPC events** |
| 14 | 2026-07-24 | Option Assignments in API | no |
| 15 | 2026-07-29 | QuotesClient TLS handshake fails against documented WSS endpoint ("Peer sent no certificates") … gRPC token-refresh leg intermittently UNAVAILABLE | **streaming/auth**; workaround = plaintext 1883 |
| 16 | 2026-08-04 | Feature request: custom screeners via API | no |
| 17 | 2026-08-27 | DataStreamingClient routes to production despite sandbox config (401) | **streaming/sandbox** |

No issue has a maintainer reply. Closed items of note: PR #5 "Fix _build_sign_string" (the
`=` vs `&` join when there is no path) was closed unmerged 2026-08-11 with a docs note instead.

## 9. Authentication

Source: https://developer.webull.com/apis/docs/authentication/signature.md , …/token.md ,
…/IndividualApplicationAPI.md , FAQ, SDK `core/auth/*`, `core/http/initializer/token/*`.

- Headers: `x-app-key, x-timestamp (ISO-8601 UTC seconds), x-signature-algorithm,
  x-signature-version, x-signature-nonce, x-version (v2|v3), x-signature`, plus
  `x-access-token` when 2FA is on. Signed = path + sorted(query ∪ signing headers incl.
  `host`) + hash(body); key = `app_secret + "&"`; result base64. **Docs describe HMAC-SHA1
  with MD5 body; SDK 3.0.1 hard-codes HMAC-SHA256 with SHA-256 body** (`sha_hmac256_new`,
  header value `HMAC-SHA256`, version `1.0`). Use the SDK signer or mirror it exactly; the
  gRPC variant joins with `=` when there is no path (documented quirk).
- Token/2FA: optional — only if the account has 2FA. Flow: `POST /auth/tokens/create` →
  status `PENDING` + SMS → approve in app within **5 min** (`EXPIRED` otherwise) → `NORMAL`.
  Token goes `INVALID` after **15 consecutive days without API calls** (not a fixed TTL; the
  MCP README says "valid for 15 days and auto-refreshes"). SDK: on startup reads
  `conf/token.txt` (or `WEBULL_OPENAPI_TOKEN_DIR`/`set_token_dir`), calls create-token with
  the local token, polls check-token every 5 s for 300 s, then **raises `ERROR_INIT_TOKEN`**
  if not NORMAL; it also first calls `/openapi/config` for `token_check_enabled`. **Headless
  implication**: a token that lapses forces an interactive phone approval — a 24/7 bot must
  either keep 2FA off for the API account (allowed) or alarm on `INVALID`. Sandbox tokens are
  valid without verification. Rate: create/check 10/10 s.
- Key rotation: "Reset Key" in API Keys Management — new key effective **immediately, old key
  invalidated immediately** (no overlap window → plan a cut-over). No read-only scopes exist
  for retail keys (institutional keys get permissions + IP whitelist; individuals have no IP
  whitelist). The gRPC channel has no token; only AK/SK signing (issue #8).
- Trading FAQ: `403` = missing/invalid auth headers; `INVALID_TOKEN` usually = signature
  mismatch from body serialization.

## 10. Terms of use / restrictions

- The OpenAPI agreement is accepted as a checkbox ("I have read and accept the agreement")
  when registering an app in API Keys Management; its **text is not published** on the open
  web (searched developer site, webull.com/policy, agreement CDN). **UNVERIFIED** whether it
  contains order-to-fill, message-rate or redistribution clauses.
- What is public: FAQ lists "Automated trading strategies (e.g., algorithmic or quantitative
  trading)" as an intended use; rate-limit page: 429 on breach, "repeatedly exceeding rate
  limits may result in temporary IP-level blocking" (the only order-rate rule found; no
  order-to-fill ratio); Webull Data Disclaimer PDF (Sept 2024) is a liability disclaimer for
  Nasdaq/OPRA/Cboe/CME data with no redistribution clause in it, while the Data Disclaimer
  help category carries the Refinitiv/LSEG line "Any copying, republication or redistribution
  … is expressly prohibited". Market-data permissions are explicitly **"Non-Display"**
  licenses (Nasdaq Basic Non-Display, OPRA Non-Display, Order Flow Non-display) — those
  exchange licenses prohibit redistribution and displaying to third parties by their own terms.
  Agentic page: Webull "assumes no liability for losses resulting from automated or AI-directed
  decisions".
- Kalshi's data terms for the Webull-routed book were not examined (out of scope here).

---

## Cross-cutting corrections to the plan (not in the 10 buckets)

- **Trading hours conflict.** Developer docs and the courseware: crypto Mon–Fri 8am–6pm ET,
  index 8–4, econ 8–11pm, sports 24/7, "Crypto Event Contracts may also trade outside their
  standard hours". Webull help FAQ 11053 (live page, fetched today): "Index Event Contracts are
  available from 8:00 AM to 4:00 PM EST. **Crypto, Companies, Financials, Weather, Economic,
  Culture, and Sports-related Event Contracts (Cleared Swaps) are available 24/7**, outside of
  any maintenance windows." The plan's §0 fact (3) "crypto weekdays only" is contradicted by
  the newest help text; resolve empirically in Phase 0 (subscribe to a KXBTC hourly on a
  weekend). https://www.webull.com/help/faq/11053-Trading-Event-Contracts
- Account list production rate limit is **10/30 s** (not 2/2 s); balance/positions/open
  orders/order history/detail are 2/2 s each, independent counters.
- MQTT `Category.US_EVENT` snapshot/quote/tick arrive on **separate topics** from stock data;
  the SDK's decoders map `event-quote → EventDepthResult`, `event-snapshot → EventSnapshotResult`,
  `event-tick → EventTickResult`.
- `client_order_id`: docs say ≤32 chars unique per account (no "alphanumeric only" rule found;
  samples use uuid hex).
- The SDK prints gRPC connection parameters and **the request signature** to stdout on every
  subscribe (`trade_events_client.py`) — scrub before production logging.
- SDK version is **3.0.1** (not 2.x); `ChangeLog.txt` in the repo is empty; the repo's own
  `endpoints.json` maps `br`/`mx` to US hosts.
