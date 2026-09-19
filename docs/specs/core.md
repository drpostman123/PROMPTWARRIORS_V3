# CORE sleeve specification (v0.1, 2026-09-19)

Slow-compounding stock-ETF + crypto trend / dual-momentum portfolio, built as a low-frequency
rebalancer inside the octopus (`docs/PICADOR_ORCHESTRATION.md`). Rules come from
`docs/research/portfolio.md` §1, §4–6; broker facts from `docs/research/brokers.md`. Items
verified in this session against live docs are tagged **[V]**; items taken from the research
briefs are **[R]**; items neither could confirm are **[U]** and appear again in §10 as tests.

Design intent in one line: the head decides target weights once a day from daily bars; two arms
(Public for ETFs, Robinhood Crypto API v2 for coins) execute small, passive, idempotent limit
orders in weekly tranches; a cross-broker lot ledger keeps the tax bill from silently eating the
premium.

---

## 1. Instrument universe and data needs

### 1.1 Equity ETF universe (Public arm) — 9 tickers, all $0 commission, fractional-eligible [U per ticker]

| Bucket | Ticker | Exposure | Why this ticker |
|---|---|---|---|
| Risk (dual-momentum candidates) | **VOO** | S&P 500 | 0.03% ER; NOT SPY so the CONVEX sleeve can use SPY/XSP/SPX options without touching a CORE ticker (wash-sale partition, §6) |
| Risk | **QQQ** | Nasdaq-100 | Deepest liquidity; CONVEX uses NDX (1256) for the same exposure |
| Risk | **IWM** | Russell 2000 | Small-cap leg of GEM-style breadth; CONVEX uses RUT |
| Risk | **VEA** | Developed ex-US | Antonacci "ex-US" leg; FTSE index (IEFA/EFA remain free for harvest swaps) |
| Risk | **VWO** | Emerging markets | Breadth; FTSE index (IEMG/EEM free for swaps) |
| Defensive (absolute-momentum alternatives) | **IEF** | 7–10y Treasuries | Faber GTAA bond leg; trend-filtered, not held blindly |
| Defensive | **TLT** | 20y+ Treasuries | Duration convexity in slow bears; trend-filtered |
| Defensive | **GLDM** | Gold | 0.10% ER; gold belongs to CORE as an ETF, MACRO expresses gold only via MGC futures (§6) |
| Cash | **SGOV** | 0–3m T-bills | The "T-bill fallback" is an actual position, earning ~DTB3; BIL is the harvest substitute |

Max 5 risk ETFs held at once (band-dependent, §9). Nothing on margin; `useMargin=false` on every order.

### 1.2 Crypto universe (Robinhood Crypto API v2 arm)

- **BTC-USD**, **ETH-USD** always eligible.
- Alt candidates, only at ≥ $50k core equity (§9): SOL-USD, XRP-USD, LINK-USD, AVAX-USD, DOGE-USD. Select at most **3** by trailing 30-day median daily dollar volume (Coinbase daily candles, `close × volume`) ≥ $2M **and** Robinhood `trading_pairs[].status == "tradable"` [R for the $2M filter; status enum value name U]. Re-select quarterly, never mid-month.
- Symbol format on Robinhood is `BTC-USD` uppercase; `min_order_size`, `max_order_size`, `asset_increment`, `quote_increment` are read from `GET /api/v2/crypto/trading/trading_pairs/?symbol=BTC-USD` at startup and cached 24h **[V, docs bundle]**.

### 1.3 Data sources

| Need | Primary | Fallback | Notes |
|---|---|---|---|
| ETF daily bars (signal series) | **Tiingo EOD** `GET https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate=2010-01-01&token=…` → `adjClose` (split+dividend adjusted; total return) | Public `GET /userapigateway/historicdata/EQUITY/{symbol}/FIVE_YEAR` (+ `get-bars-v2-with-aggregation`, `aggregation=ONE_DAY`) **[V endpoint, adjustment U]** | Signals need total-return series (dividends matter for IEF/TLT/SGOV). Tiingo free-tier limits **[U]**. Cross-check: Tiingo close vs Public close within 0.5% or flag. |
| ETF live quotes (for limit pricing) | Public `POST /userapigateway/marketdata/{accountId}/quotes` body `{instruments:[{symbol,type:"EQUITY"}]}` → `bid, ask, last, bidTimestamp, askTimestamp` **[V]** | — | Real-time per docs; `marketdata` scope required. |
| Crypto daily bars | **Coinbase Exchange** `GET https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400&start=…&end=…` (≤ 300 candles/request, no auth) **[V]** | Public `GET /userapigateway/historicdata/CRYPTO/BTC/FIVE_YEAR` **[V endpoint]** | Robinhood's API has **no historical candles** (only `best_bid_ask`, `estimated_price`) **[V]**. Daily bar = UTC 00:00 bucket. |
| Crypto live quotes | Robinhood `GET /api/v2/crypto/marketdata/best_bid_ask/?symbol=BTC-USD` (exchange prices, no spread mark-up on v2) **[V]** | Coinbase `GET /products/BTC-USD/ticker` | v1 `best_bid_ask` includes market-maker spread; do not mix v1 prices with v2 orders. |
| T-bill rate | **FRED DTB3** "3-Month Treasury Bill Secondary Market Rate, Discount Basis", daily, latest 3.97% on 2026-09-17 **[V]**. No-key CSV: `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3`; keyed JSON: `https://api.stlouisfed.org/fred/series/observations?series_id=DTB3&file_type=json&api_key=…` | Treasury FiscalData daily rates **[U]** | Convert discount basis to simple annual: `rf = 365·d / (360 − 91·d)` with d = DTB3/100; forward-fill weekends/holidays. |
| Corporate actions | Tiingo `adjClose` handles splits; Public `history` `type` entries record dividends actually received | — | Reconciliation (§5) uses received dividends, not modelled ones. |

All series are stored in the head's Postgres/SQLite as `bars(source, symbol, date, open, high, low, close, adj_close, volume)`; the signal engine reads only the DB, never the network.

---

## 2. Signal math

Notation: `P_t` = adjusted close on trading day t; equities use a 252-day year (21/63/252 lookbacks); crypto uses a 365-day year (30/91/365 lookbacks) on UTC daily candles. `rf_t` = simple annual T-bill rate from DTB3.

### 2.1 The 1/3/12 blend (Hurst–Ooi–Pedersen; Faber for the SMA leg)

```
R12 = P_t / P_{t-L12} - 1 - rf_avg(t-L12..t)            # 12-month excess return
R3  = P_t / P_{t-L3}  - 1 - rf_avg(t-L3..t) * (3/12)     # 3-month excess return
R1  = P_t / P_{t-L1}  - 1 - rf_avg(t-L1..t) * (1/12)     # 1-month excess return
SMA10 = mean(close at the last 10 month-end dates, with today's close standing in for the current month)
s12 = 1[R12 > 0];  s3 = 1[R3 > 0];  sSMA = 1[P_t > SMA10]
S   = (s12 + s3 + sSMA) / 3                              # ∈ {0, 1/3, 2/3, 1}
```

Trend multiplier with hysteresis (Goulding–Harvey–Mazzoleni slow/fast disagreement as a de-risk cue):

```
if S == 1:                    m = 1.0
elif S == 2/3:                m = 0.5 if (R12 > 0) != (R1 > 0) else 1.0    # slow/fast disagree → half size
elif S == 1/3:                m = 0.5 if m_prev >= 0.5 and R12 > 0 else 0.0  # exit only when the slow signal also fails
else:                         m = 0.0
```

`m_prev` is yesterday's multiplier for the same asset; the hysteresis stops the 1/3 ↔ 2/3 flicker from generating trades. A "flip" for §4 purposes is any change of `m` between {0, 0.5, 1}.

### 2.2 Dual momentum for the equity sleeve (Antonacci GEM generalised)

```
RISK   = [VOO, QQQ, IWM, VEA, VWO]
DEF    = [IEF, TLT, GLDM]
K_risk, K_def = by equity band (§9)

eligible_risk = [x for x in RISK if m[x] > 0]              # absolute momentum: excess return > 0, trend intact
chosen_risk   = top K_risk of eligible_risk by R12          # relative momentum
if len(chosen_risk) == 0:
    eligible_def = [x for x in DEF if m[x] > 0]
    chosen_def   = top K_def of eligible_def by R12
    if len(chosen_def) == 0: chosen = {SGOV: 1.0}           # T-bill fallback
    else:                    chosen = chosen_def
else:
    chosen = chosen_risk
# Partial defence: if fewer than K_risk risk names are eligible, the empty slots go to chosen_def first, then SGOV.
```

Rank stickiness: an incumbent is replaced only if the challenger's R12 exceeds it by > 2 percentage points (turnover control, Baltas–Kosowski).

### 2.3 Crypto: independent TSMOM per coin

Each coin gets its own `m` from §2.1 (365-day calendar, `rf` still DTB3). No relative ranking among coins; each is sized independently under the crypto caps (§3). Minimum hold: once bought, a coin is not sold for 7 calendar days unless `m` drops to 0 with S = 0, a ladder rung fires, or kill.

### 2.4 Whipsaw counter (Babu et al.)

`flips_126[x]` = number of changes of `m[x]` in the trailing 126 trading days. If ≥ 4, the asset's risk budget `b_x` is halved for the next 63 trading days.

### 2.5 Daily pseudocode (head, 16:35 ET for equities; 00:10 UTC for crypto)

```
for x in UNIVERSE:
    load bars (≥ 13 months), rf series
    compute R12, R3, R1, SMA10, S, m[x] (with hysteresis vs m_prev[x])
    update flips_126[x]
equity_targets = dual_momentum(RISK, DEF, m, R12, K_risk, K_def)
crypto_targets = {c: m[c] for c in CRYPTO}
weights = vol_target(equity_targets, crypto_targets)        # §3
persist signal_snapshot(date, per-asset {R12,R3,R1,SMA10,S,m,sigma,w_target})
emit intents for the tranche due today                        # §4
```

---

## 3. Volatility targeting and weight caps

### 3.1 Volatility estimate (risk control only, never an alpha source — Cederburg et al.)

```
sigma_ewma(x) = sqrt(EWMA of squared daily log returns, half-life 20 days) * sqrt(A_x)   # A = 252 equities, 365 crypto
sigma_60(x)   = stdev(daily log returns, last 60 days) * sqrt(A_x)
sigma(x)      = max(sigma_ewma(x), sigma_60(x), sigma_floor(x))
sigma_floor   = 8% for equity ETFs, 4% for IEF/TLT, 3% for SGOV (SGOV is never vol-scaled; it is the residual), 40% for crypto
```
Taking the max is deliberately conservative: rising vol cuts size immediately, falling vol re-adds slowly.

### 3.2 Risk budgets

```
sigma_star   = 0.10                          # core target vol (band 10–12%); ladder multiplier L applies (§7)
b_crypto     = 0.25 of core risk (hard cap 0.35; falls to 0.20 when corr cap binds, §7.3)
b_equity     = 1 - b_crypto
b_x (equity) = b_equity / len(chosen)        # equal risk across chosen ETFs
b_c (crypto) = b_crypto * share_c, share = {BTC 0.55, ETH 0.30, alts 0.15 split equally}; if a coin has m=0 its share is NOT redistributed
```

### 3.3 Raw weights, caps, and the correlation correction

```
w_raw[x] = m[x] * b_x * sigma_star * L / sigma(x)

caps: w[VOO|QQQ|IWM|VEA|VWO] ≤ 0.40 each; w[IEF|TLT] ≤ 0.30 each; w[GLDM] ≤ 0.20
      w[BTC] ≤ 0.12, w[ETH] ≤ 0.08, w[alt] ≤ 0.03 each; sum(w_crypto) ≤ 0.20 (0.10 when ladder ≥ rung 2)
w[x] = min(w_raw[x], cap[x])

# portfolio vol check with 60-day correlation matrix C:
sigma_p = sqrt(w' * diag(sigma) * C * diag(sigma) * w)
if sigma_p > 1.25 * sigma_star * L:   w *= (sigma_star * L) / sigma_p        # scale everything down, never up
# crowding rule (Hurst et al.): if mean pairwise 60d corr of held assets > 0.6: w *= 0.75
gross = sum(w);  assert gross ≤ 1.0 (if > 1.0, scale down proportionally — no leverage, ever)
w[SGOV] = 1.0 - gross                                                        # residual to T-bills
```

Worked example, $10,000 core, ladder L = 1, chosen = {VOO, QQQ}, BTC m = 1, ETH m = 0.5:
σ(VOO)=0.15, σ(QQQ)=0.20, σ(BTC)=0.60, σ(ETH)=0.75; b_equity = 0.75 → b_x = 0.375; b_BTC = 0.25·0.55 = 0.1375, b_ETH = 0.075.
w_VOO = 0.375·0.10/0.15 = 0.25; w_QQQ = 0.375·0.10/0.20 = 0.1875; w_BTC = 0.1375·0.10/0.60 = 0.0229; w_ETH = 0.5·0.075·0.10/0.75 = 0.005 → below the $500 lot floor (§4), so w_ETH = 0 and w_SGOV = 1 − 0.4604 = 0.54. Dollar targets: VOO $2,500, QQQ $1,875, BTC $229 → also below the $500 crypto floor, so at $10k BTC is held only when its lot ≥ $500 (see §9: crypto begins at $5k but only if the sized lot clears $500; otherwise the crypto budget waits in SGOV). This is the intended behaviour: at 10% target vol a 60%-vol asset is a small line.

---

## 4. Rebalance protocol

### 4.1 Cadence and triggers

- Signals and target weights are recomputed **every trading day** (equities) / every UTC day (crypto). Nothing trades on a signal alone.
- A position trades only when **one** of these holds:
  1. **Flip**: `m[x]` changed since the last executed target for x.
  2. **Relative drift**: `|w_actual − w_target| / max(w_target, 0.02) > 0.20`.
  3. **Absolute drift**: `|w_actual − w_target| > 0.05`.
  4. **Ladder / kill** message from the head (§7) — executes across all tranches immediately.
- **Four weekly tranches** (Hoffstein–Faber–Braun): the portfolio is split into sub-portfolios T0..T3 of 25% each. Tranche k is rebalanced to target on the Tuesday of ISO week `w` where `w mod 4 == k` (Wednesday if Tuesday is a holiday). A flip therefore completes over four weeks; a flip to `m = 0` with `S = 0` (all three signals negative) executes **two** tranches at once (so a full exit takes two weeks). Crypto tranches run on the same weekday at 14:00 UTC.
- Below $5k core equity, tranching is collapsed to 2 (biweekly), below $2k to 1 (monthly); see §9.

### 4.2 Minimum lots and rounding

| Asset | Minimum trade | Rounding | Notes |
|---|---|---|---|
| ETFs | $25 notional (Public rejects fractional orders < $5.00 **[V, fractional disclosure]**; $25 keeps rounding noise out of the 20% drift rule) | Whole shares first; fractional residual to 5 decimals (Apex rounds holdings to the 5th decimal **[V]**) | Positions ≥ $2,000 are traded in whole shares, residual in fractional |
| BTC / ETH | **$500 notional** [R fee-stack rule] | `asset_quantity` rounded **down** to `asset_increment`; `limit_price` rounded to `quote_increment` **[V field names]** | Also `≥ min_order_size`, `≤ max_order_size` from `trading_pairs` |
| Alts | $500 notional | same | — |

Trades smaller than the minimum are deferred, not skipped: the unmet drift stays queued and is re-evaluated next tranche.

### 4.3 Equity order type: limit at mid ± x bps (Public)

```
session: CORE only, window 10:00–15:30 ET (skips both auctions; fractional and limit availability is broadest in CORE)
quote  = POST /userapigateway/marketdata/{accountId}/quotes
mid    = (bid + ask) / 2; spread_bps = (ask - bid)/mid * 1e4
if spread_bps > 25 or quote age > 5 s: skip this symbol this pass
BUY : limitPrice = round_up  (mid * (1 + 5e-4), 0.01)     # mid + 5 bps
SELL: limitPrice = round_down(mid * (1 - 5e-4), 0.01)     # mid - 5 bps
body = {orderId: <uuid from intent>, instrument:{symbol, type:"EQUITY"}, orderSide, orderType:"LIMIT",
        expiration:{timeInForce:"DAY"}, quantity | amount, limitPrice, equityMarketSession:"CORE",
        openCloseIndicator, useMargin:false, taxLotMatchingInstructions (sells only, §6)}
POST /userapigateway/trading/{accountId}/order   (preflight-single-leg first on every new symbol/day) [V endpoints]
re-price ladder: unfilled after 5 min → cancel (DELETE cancel-order) and resend at mid ± 10 bps with a NEW client id
                 (the old id is spent); after 15 min → mid ± 15 bps; after 30 min, if remaining notional < $200 → MARKET (CORE)
                 else leave for next tranche day
```
Fractional handling: buys of < 1 share or of a residual use `amount` (notional, ≥ $5); sells use `quantity` with up to 5 decimals ("quantity … used when buying/selling whole shares and when selling fractional; mutually exclusive with amount" **[V]**). Fractional limit orders are accepted since 2026-08-27 **[V changelog]**, but "certain order types, times in force, or trading sessions may not be available for all Fractional Share orders" **[V disclosure]** → the arm relies on `preflight-single-leg`; if a fractional LIMIT is rejected, fall back to MARKET in CORE only for residuals ≤ $50, otherwise round to whole shares and carry the residual.
GTD (≤ 90 days ahead, `expirationTime` ISO-8601 **[V rPublic]**) is not used by CORE; DAY only, so nothing rests overnight.

### 4.4 Crypto order type: synthesized post-only maker limit (Robinhood Crypto API v2)

Fact that shapes the design: **the Robinhood Crypto API has no post-only / maker-only flag** **[V, docs bundle + routing article]**. Under exchange routing (which v2 "fee tier" orders use), "limit/stop-limit orders … generally routed to Bitstamp … to rest on Bitstamp's order book until your order becomes executable"; resting fills get the maker fee, and an order that is immediately executable, or that is routed to another exchange for a better price, pays taker **[V]**. So the arm must guarantee the order cannot cross at submission:

```
q = GET /api/v2/crypto/marketdata/best_bid_ask/?symbol=BTC-USD          # exchange best bid/ask, no MM spread [V]
mid = (bid + ask)/2
BUY : limit = round_down(min(bid, mid * (1 - 10e-4)), quote_increment)  # ≤ best bid AND ≥ 10 bps under mid
      require limit < ask - quote_increment                              # would-not-cross guard
SELL: limit = round_up  (max(ask, mid * (1 + 10e-4)), quote_increment)
      require limit > bid + quote_increment
body = {client_order_id: <uuid>, symbol, side, type:"limit",
        limit_order_config:{asset_quantity: qty, limit_price: limit, time_in_force:"gfd"}}   # one of asset_quantity|quote_amount [V]
POST /api/v2/crypto/trading/orders/?account_number=<acct>                                    # [V]
poll GET /api/v2/crypto/trading/orders/{id}/?account_number=… every 30 s (≤ 20 req/min of the 100/min budget) [V rate limit]
if state == "open" after 20 min: POST …/orders/{id}/cancel/ then resend at the new best bid/ask (max 6 re-prices ≈ 2 h)
if still unfilled: leave for next tranche day — EXCEPT when reason ∈ {LADDER, KILL}: send type:"market" (taker 0.95% base tier) [R fee]
never more than one open order per symbol; gfd so nothing survives the day unattended
```
Fee verification: `GET /api/v2/crypto/trading/accounts/` → `fee_tier_status{fee_ratio, thirty_day_volume, next_fee_tier_threshold, next_fee_tier_ratio}` **[V fields]** is logged daily; each fill's realized fee is inferred from `executions[].effective_price` vs the limit price and reconciled to the monthly statement. Robinhood's base tier is maker 0.50% / taker 0.95% (< $10k 30-day volume) **[R fee schedule]**. Only v2 orders count toward tier volume **[V]**.

### 4.5 Rate budgets

Public: 10 req/s per account **[V]**; CORE arm self-limits to 2 req/s and batches quotes in one call. Robinhood: 100 req/min, 300 burst, token bucket per endpoint **[V]**; CORE arm budget 40 req/min total (20 polling, 20 headroom), shared with any future GRIND-CRYPTO arm through the head's per-arm limiter.

---

## 5. Arm interface

### 5.1 Who executes what

| Arm id | Broker | Instruments | Account | Paper mode |
|---|---|---|---|---|
| `arm:public-core` | Public Individual Trader API (bearer PAT from API secret) | The 9 ETFs of §1.1 | Public taxable brokerage → Public IRA when §6.4 triggers | **Local replay executor** (Public has no sandbox **[R]**) |
| `arm:rh-crypto` | Robinhood Crypto Trading API v2 (Ed25519-signed, `x-api-key/x-timestamp/x-signature`, 30 s timestamp window) | BTC-USD, ETH-USD, selected alts | Robinhood Crypto (default API account) | **Local replay executor** (no sandbox **[R]**) |
| backup | Webull OpenAPI | ETFs and crypto | — | Webull paper | Only if a primary arm is down > 5 trading days; note Webull crypto costs 1% spread/side [R] |

The head owns pricing and sizing; arms never compute weights. `arm:rh-crypto` may later be shared with GRIND-CRYPTO; the head's `(symbol, side)` ownership lock from the orchestration design prevents CORE and any other sleeve from being on opposite sides of BTC-USD at the same time.

### 5.2 Intent schema fields used by CORE (subset of the common pydantic `Intent`)

```
Intent {
  owner_id, arm_id ∈ {"public-core","rh-crypto"}, sleeve = "CORE",
  intent_id: uuid4,                       # head-side identity
  client_order_id: uuid                   # = Public `orderId` / Robinhood `client_order_id`; see 5.3
  head_epoch: int, seq: int, created_at, ntp_offset_ms,
  tranche_id: "T0".."T3", tranche_date: date,
  reason ∈ {DRIFT, FLIP, LADDER, KILL, HARVEST, RECON_FIX},
  instrument {venue_symbol: "VOO" | "BTC-USD", asset_class ∈ {ETF, CRYPTO}},
  side ∈ {BUY, SELL},
  qty_mode ∈ {SHARES, NOTIONAL_USD, ASSET_QTY}, qty: Decimal,
  price_ref {bid, ask, mid, ts},          # what the head saw; arm re-quotes and refuses if mid moved > 50 bps
  limit_offset_bps: 5 | 10, max_slippage_bps: 25 (ETF) | 40 (crypto),
  tif ∈ {DAY, gfd}, session = "CORE" (ETF only),
  urgency ∈ {PASSIVE, NORMAL, RISK_OFF},  # RISK_OFF permits market orders
  target_weight, current_weight,          # audit only, never used by the arm for sizing
  valid_until: ts,                        # arm drops the intent after this
  tax_lots: [{lot_id, qty}] | null        # ETF sells only → Public taxLotMatchingInstructions
}
Fill { fill_id, intent_id, client_order_id, venue_order_id, symbol, side, qty, price, fee_usd, liquidity ∈ {MAKER, TAKER, UNKNOWN}, venue_ts, arm_ts }
Position { arm_id, symbol, qty, avg_cost, mark, mark_ts, source ∈ {STREAM, RECON} }
```

### 5.3 Idempotency

- `client_order_id = uuid5(NAMESPACE_CORE, f"{tranche_date}|{arm_id}|{symbol}|{side}|{attempt}")`, written to the head DB **before** the intent is published. Public: "orderId … deduplication key; if reused on the same account, the operation is idempotent" **[V]**. Robinhood: `client_order_id` "User input order id for idempotency validation", must be a valid UUID **[V]**.
- On any timeout/5xx the arm **re-sends the identical body with the identical id**; it never mints a new id for the same attempt. A re-price is a new `attempt` (and therefore a new id) only after the cancel is confirmed (`GET order` shows cancelled / `state == "canceled"`).
- On arm restart: fetch open orders (Public `get-order` per outstanding id from the local cache; Robinhood `GET /api/v2/crypto/trading/orders/?account_number=…&state=open`) before accepting new intents; unknown open orders → cancel and alert.
- Arm refuses intents whose `head_epoch` is older than the newest seen (two-heads guard), whose `valid_until` passed, or whose re-quoted mid differs from `price_ref.mid` by > 50 bps (reason reported back as `STALE_PRICE`).

### 5.4 Reconciliation from broker history (nightly 20:30 ET, and before every tranche)

- Public: `GET /userapigateway/trading/{accountId}/history?start=…&end=…&pageSize=…&nextToken=…` returns `{timestamp, id, type:"TRADE", subType, symbol, quantity, side, netAmount, principalAmount, fees, securityType}` **[V]**; positions from `get-account-portfolio-v2`; lots from `get-unrealized-tax-lots(-for-symbol|-csv)` **[V resource names]**.
- Robinhood: `GET /api/v2/crypto/trading/orders/?account_number=…&updated_at_start=…&state=filled` with `executions[{effective_price, quantity, timestamp}]`, `average_price`, `filled_asset_quantity` **[V]**; holdings from `GET /api/v2/crypto/trading/holdings/`.
- Match rule: every broker TRADE must map to a `Fill` with the same `client_order_id`/venue id, |Δqty| ≤ 1e-5 sh (ETF) or 1 `asset_increment` (crypto), |Δprice| ≤ 0.1%; position totals must match to the same tolerance. Any mismatch → arm frozen (no new buys, sells allowed), `RECON_MISMATCH` alert; a `RECON_FIX` intent is only ever created by hand.
- Dividends/interest (SGOV monthly) from Public `history` non-TRADE types feed the journal and the lot ledger's basis (no reinvest orders; cash just becomes SGOV drift).

---

## 6. Tax-aware execution

### 6.1 Cross-broker lot ledger (head DB, table `lots`)

```
lots(lot_id, sleeve, arm_id, account_id, account_kind ∈ {TAXABLE, IRA}, symbol, asset_class,
     open_ts, open_qty, open_price, fees_open, close_ts, close_qty, close_price, fees_close,
     term ∈ {ST, LT}, realized_pnl, wash_disallowed, replacement_lot_id, source_tx_id)
```
Every fill from every arm (CORE, CONVEX, MACRO, GRIND) writes here; brokers only report wash sales inside one account **[R]**, so this ledger is the only place the §1091 60-day window can be seen.

### 6.2 No-shared-ticker rule (enforced by the head's instrument registry)

- A ticker is **owned by exactly one (sleeve, arm, account)**. CORE owns VOO, QQQ, IWM, VEA, VWO, IEF, TLT, GLDM, SGOV. The registry rejects any other sleeve's intent on these symbols and on their "substantially identical" class: {VOO, SPY, IVV, SPLG}, {QQQ, QQQM}, {IWM, VTWO}, {GLDM, GLD, IAU, SGOL}, {SGOV, BIL, SHV} are treated as one class each (conservative; the IRS has never defined "substantially identical" for ETFs **[R]**).
- Consequences for the other sleeves: CONVEX trades options on SPY/XSP/SPX, NDX, RUT, GLD-class is off limits → gold convexity via MGC options only; MACRO expresses gold via MGC futures, rates via 10Y/ZN micros, equities via MES, never via GLD/TLT/SPY ETFs while CORE holds the class. Futures are not "securities" for §1091 in the practitioner view **[R, CPA question §10]**.
- **Options on a CORE ticker within ±30 days of a CORE loss sale are a wash sale** (a call is a contract to acquire); the registry blocks them.

### 6.3 Wash-sale guard and harvest substitutes

Before any CORE ETF sell at a loss (lot-level, using Public `taxLotMatchingInstructions` to choose lots; default selection = highest-cost lot first to realize ST losses, lowest-cost LT lots when selling at a gain):
1. Query `lots` for buys of the same class in `[t−30d, t]` in any account (including IRA). If found, the loss will be washed: the head prefers to sell a different lot or defers the sale to the next tranche unless `reason ∈ {LADDER, KILL}`.
2. After a loss sale, the class is **blocked for buys for 31 days** in all accounts. If the momentum rule wants back in during the block, the head buys the **substitute** and keeps it until the next natural exit (no swap-back churn):

| CORE ticker | Substitute (different index) |
|---|---|
| VOO | VTI |
| QQQ | ONEQ (Nasdaq Composite) |
| IWM | IJR (S&P 600) |
| VEA | IEFA (MSCI EAFE) |
| VWO | IEMG (MSCI EM IMI) |
| IEF | VGIT |
| TLT | VGLT |
| GLDM | none — no harvest, just wait out the block in SGOV |
| SGOV | BIL |

Substitutes inherit CORE ownership while held. Loss harvesting is never initiated for its own sake in year one (the drift/flip rules generate enough realized ST losses; Israel–Moskowitz).

Crypto: §1091 does not apply to digital assets as of today, but H.R. 9172 is drafted to apply to dispositions after 2026-06-08 **[R]**. The ledger therefore records crypto lots with the same fields and the same 31-day flags so a retroactive rule can be computed; Robinhood's API offers no lot selection, so the ledger assumes FIFO for crypto (confirm against the 1099-DA **[U]**).

### 6.4 Where an IRA fits

- Target end-state: the **high-turnover equity sleeve lives in an IRA** (Public Traditional/Roth IRA — options level irrelevant for ETFs; API exposure of IRA accounts **[U]**, the `accountType` enum header lists IRA/ROTH_IRA/TRADITIONAL_IRA **[V header, U in practice]**), crypto stays taxable at Robinhood.
- Trigger: once annual IRA contribution room is available and core equity ≥ $5k, new CORE equity capital goes to the IRA; the taxable Public account winds its ETF positions down on natural exits only (no forced sales). **Hard rule: never hold a CORE class in both the IRA and a taxable account** (Rev. Rul. 2008-5 makes an IRA-washed loss permanent **[R]**). Until the taxable book is empty, the IRA may only hold classes the taxable book has not held in 31 days.
- The head runs one `Intent` schema and one ledger; `account_kind` on each lot decides whether the wash-sale guard is advisory (IRA-only) or blocking.

---

## 7. Risk controls specific to CORE

### 7.1 Drawdown ladder hooks (total-equity ladder from `portfolio.md` §5.5; charter ladder maps onto it)

The head publishes `risk.ladder {rung, L_core, crypto_cap, ts}`; CORE reads only `L_core` and `crypto_cap`:

| Rung | Total equity vs 12-month HWM | L_core | crypto_cap | CORE action |
|---|---|---|---|---|
| 0 | > −8% | 1.00 | 0.20 | normal |
| 1 | ≤ −8% | 1.00 | 0.20 | none (CONVEX/MACRO cut) |
| 2 | ≤ −12% | 0.75 | 0.10 | re-target all four tranches within 2 trading days, `urgency=RISK_OFF` for crypto |
| 3 | ≤ −16% | 0.50 | 0.10 | same |
| 4 | ≤ −20% | 0.25 | 0.05 | same; no re-risk without manual sign-off |
| re-risk | new 3-month equity high | +1 rung | — | one rung per ≥ 20 trading days |

`L_core` multiplies `sigma_star` in §3.3, so it scales every weight (not just the risk names); SGOV absorbs the difference.

### 7.2 Max weights (restated) and gross

Per ETF 40% / 30% / 20% by bucket; per coin 12% / 8% / 3%; crypto total ≤ `crypto_cap`; gross ≤ 100%; crypto risk share ≤ 35% (target 25%).

### 7.3 Correlation cap between crypto and equities

`rho_ce` = 60-day correlation of daily BTC-USD returns with the held equity basket's returns. If `rho_ce > 0.5`, `b_crypto` is cut from 0.25 to 0.20 and `crypto_cap` from 0.20 to 0.15 until `rho_ce < 0.4` for 20 consecutive days (BTC–SPX reached 0.74 in March 2026 **[R]**: in a stress month crypto is equity beta with 4× the vol, and the budget must reflect it).

### 7.4 Kill / freeze conditions (CORE-local; the head's global kill also applies)

| Condition | Action |
|---|---|
| Reconciliation mismatch (§5.4) | Freeze that arm's buys; sells allowed; page operator |
| Bars stale > 2 trading days for any held symbol, or DTB3 stale > 10 days | No new signals; hold targets; alert |
| CORE sleeve daily loss > 4% of core equity (≈ 5σ at 12% vol) | Halt buys 2 days; run ladder check; no forced sells |
| `fee_tier_status.fee_ratio` > 0.0050 or taker fills ≥ 3 in a month on `PASSIVE` intents | Pause crypto buys until re-priced logic reviewed |
| 20-day realized all-in cost > 2× budget (§8.3) | Arm to `PASSIVE` only; no market fallbacks |
| ≥ 3 consecutive broker rejects on one arm | Arm frozen; operator review |
| Whipsaw counter ≥ 4 in 126 days | Asset budget halved (§2.4) |
| Two-heads detection (older `head_epoch`) | Arm refuses all live intents; cancels resting orders |
| Arm loses head heartbeat > 60 s | Arm cancels its own resting orders (self-cancel-on-disconnect) |

---

## 8. Paper-test plan and graduation criteria

### 8.1 Replay executor (both arms; no sandbox exists at Public or Robinhood)

- Equities: an intent "fills" at its limit price if, in the 5-minute bars following submission (Public `get-bars-v2-with-aggregation`, `FIVE_MINUTES` if listed, else `FIFTEEN_MINUTES` **[U enum]**), the bar low (buy) / high (sell) crosses the limit by at least one tick; fill price = limit, cost = 0 commission + 2 bps modelled adverse selection. The re-price ladder is simulated exactly.
- Crypto: the arm polls `best_bid_ask` every 30 s and logs it (this is also the live data feed); a paper buy fills only when a later `bid` ≤ limit (i.e. the market traded through the resting price), fee = 0.50%; paper taker fallback at `ask` + 0.95%. This under-fills relative to a real maker queue, which is the conservative direction.
- Everything else — signals, tranches, intents, idempotency, ledger, reconciliation against a simulated broker history — runs the production code path.

### 8.2 Reference backtest

Same rules (§2–3, §4.1 triggers, tranches) run vectorised on the same `bars` table, executed at the day's close with a flat 5 bps (ETF) / 60 bps (crypto) cost per trade, from 2015-01-01 (crypto data start) to today. Outputs: daily target weights, NAV, turnover, cost drag, drawdown. Sanity anchors from the brief: net Sharpe 0.4–0.6, worst drawdown 25–35% (40% with crypto at cap) **[R]**. A backtest Sharpe above 0.8 net is treated as a bug until explained.

### 8.3 Cost and slippage budget

| Item | Budget (all-in, one way) |
|---|---|
| ETF | ≤ 10 bps (≈ half spread + 5 bps offset); market fallbacks ≤ 10% of ETF notional traded |
| BTC/ETH | ≤ 60 bps (0.50% maker + 10 bps offset drift); taker fills ≤ 1 per month |
| Turnover | ≤ 150%/yr of core NAV (both sides) → annual cost drag ≤ 0.40% |

### 8.4 Paper duration and graduation gate (all must pass)

1. **One full rebalance cycle**: all four tranches executed at least once **and** at least one signal flip processed end-to-end (or 8 calendar weeks, whichever is later).
2. **Tracking error**: annualised stdev of (paper daily return − reference daily return) ≤ 1.5%, and cumulative NAV gap ≤ 0.5% over the period, with every gap attributed (timing, cost, rounding) in the report.
3. **Reconciliation**: 20 consecutive nightly reconciliations with zero mismatches against the simulated history; then, in canary, 20 consecutive against real broker history.
4. **Idempotency chaos test**: kill the arm mid-order 10 times, restart the head twice, replay a duplicated NATS message; zero duplicate orders, zero orphaned resting orders.
5. **Cost**: realized paper costs within §8.3 budgets.
6. **Compliance**: wash-sale guard unit tests (cross-account, IRA case, option-on-ticker case) green; registry rejects a CONVEX intent on QQQ and a MACRO intent on GLD.
7. **Failover drill** in paper: standby promotes, verifies no resting orders per arm, continues the tranche schedule.

Canary after graduation: live at $500 (ETF only) for four tranches, then $2,500 with BTC, then band scaling per §9. Each step requires criteria 2–5 to hold on live data.

---

## 9. Equity-band behaviour (core equity = CORE's share of total equity)

| Band | Equity ETFs | Crypto | Tranches | Order handling | Notes |
|---|---|---|---|---|---|
| **< $500** | K_risk = 1 among {VOO, QQQ} only, K_def = 0 (fallback straight to SGOV) | none | 1 (monthly) | Fractional notional buys (`amount`, ≥ $25); sells by fractional `quantity` | Drift rule off; trades on flips only. At this size CORE is a habit, not a portfolio |
| **$500 – $5k** | K_risk = 1 (2 above $2.5k) from all five; K_def = 1 | none — a $500 lot would be > 10% and the fee stack dominates | 2 (biweekly) above $2k, else 1 | Fractional throughout; limit at mid ± 5 bps, market fallback for residuals ≤ $50 | SGOV residual earns the T-bill rate |
| **$5k – $25k** | K_risk = 2, K_def = 1 | BTC only, lots ≥ $500, cap 12% → in practice BTC is held only when `w_BTC × equity ≥ $500` (≈ ≥ $5k at σ=60%, L=1, m=1) | 4 weekly | Whole shares for positions ≥ $2k, fractional residual | Crypto minimum-hold 7 days; alt coins off |
| **$25k – $100k** | K_risk = 3, K_def = 2 | BTC + ETH; +1 alt at ≥ $50k; lots ≥ $1,000 | 4 weekly | Whole shares + fractional residual; Public per-order notional caps **[U]** | IRA migration (§6.4) starts; Robinhood 30-day volume likely > $10k → maker 0.35% |
| **$100k – $1M** | K_risk = 3, K_def = 2 (full universe) | BTC + ETH + up to 3 alts; lots ≥ $2,500 | 4 weekly | Child orders ≤ $50k and ≤ 1% of 20-day ADV per symbol per day; crypto child orders ≤ `max_order_size` and ≤ $25k each | Above $50k/30d Robinhood maker is 0.125% **[R]**; above ~$250k consider MBT micro futures (1256) at tastytrade for part of the BTC line — separate decision, not in scope |

Band changes are evaluated monthly on the tranche-0 date; moving down a band takes effect immediately, moving up waits one month (avoid oscillation).

---

## 10. Open questions and UNVERIFIED items, written as tests

| # | Item | Test |
|---|---|---|
| 1 | Public fractional **limit** orders for each of the 9 ETFs in the CORE session (disclosure says availability varies per security) | Preflight a $25 fractional LIMIT buy and a 0.5-share LIMIT sell on each ticker; record accept/reject; fallback path per §4.3 |
| 2 | Public per-order / per-day notional caps and ADV-based throttles ("order designations based on behavior") | Preflight $10k, $50k, $100k VOO limit orders; read any error text; set child-order size accordingly |
| 3 | Public bars-v2 adjustment (split/dividend) and depth | Compare Public `close` series for IEF/TLT/SGOV with Tiingo `adjClose`/`close` over 3 years; confirm which one Public returns |
| 4 | Tiingo free-tier coverage and quotas for the 18 symbols (universe + substitutes) | Pull 15 years for all 18 in one batch; log 429s |
| 5 | Public IRA accounts visible/tradable via the same PAT (`GET /userapigateway/trading/account` `accountType`) | Open the IRA, list accounts, preflight a $25 SGOV buy in it |
| 6 | Robinhood `trading_pairs.status` enum value for tradable, exact `min_order_size` for BTC/ETH/alts | Call `GET /api/v2/crypto/trading/trading_pairs/`; pin the values in config with the fetch date |
| 7 | Whether a v2 limit order at or below the best bid is **always** posted as maker (routing article says "generally" Bitstamp; may route elsewhere as taker) | 20 paper-to-live $50 BTC test orders at bid − 10 bps; compare `effective_price` with limit and the monthly statement's fee line; count taker fills |
| 8 | Robinhood v2 order list supports `updated_at_start`/`state` filters as extracted | Query with each filter; assert results respect it |
| 9 | Robinhood fee tier reflected in `fee_tier_status.fee_ratio` matches the fee schedule (0.0050 maker base) | Log daily; compare with statement |
| 10 | Public `history` `type`/`subType` enum for dividends and interest (needed for the lot ledger and SGOV income) | Pull 90 days of history on a funded account; enumerate values |
| 11 | Public `get-bars-v2-with-aggregation` intraday enum (FIVE_MINUTES?) for the replay executor | Call with candidate values; fall back to FIFTEEN_MINUTES |
| 12 | Substantially-identical treatment: VOO↔SPY, GLDM↔GLD, ETF↔futures (MGC vs GLDM), option-on-ticker | Written CPA opinion before the second sleeve trades a related class (charter compliance item 5) |
| 13 | Crypto wash-sale legislation (H.R. 9172) status and Robinhood 1099-DA lot method | Quarterly check; ledger already carries the fields |
| 14 | Coinbase Exchange public candles rate limit (unauthenticated ~10 req/s per IP per docs, U) and symbol availability for alts | Backfill 10 years for 7 pairs at 300 candles/request; log 429s |
| 15 | DTB3 discount→simple conversion vs SGOV's actual 30-day yield | Compare monthly; if SGOV total return deviates > 25 bps/yr, switch `rf` to SGOV realized |
| 16 | Public rate limit behaviour on burst (10 req/s) and error format | Fire 30 quote calls in 1 s in canary; verify backoff |
| 17 | Backup-arm parity: Webull OpenAPI fractional ETF support and crypto spread on the same symbols | Only if the backup path is ever armed |
