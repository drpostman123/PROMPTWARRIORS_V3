# MACRO sleeve — buildable specification (v0.1, 2026-09-19)

Scope: the charter's sleeve #4 — commodities, Treasuries/yields, FX trend exposure "when it matters".
Sleeve vol budget **3 % of total equity (annualised)**; capital share **0–15 %, regime-gated**; mostly flat.
Inputs: `MASTER_SYSTEM_CHARTER.md`, `PICADOR_ORCHESTRATION.md`, `research/portfolio.md` §3–6, `research/brokers.md` §2/§4/§5, `research/quant.md` §0–1.

Legend: **[V]** verified this session from a primary source or a live API call; **[T]** third-party/secondary; **[U]** unverified → §11 test. Live levels used for worked numbers (Yahoo chart API, 2026-09-18 close, [V]): GC=F 4,415.9; CL=F 95.47; ZN=F 105.83; 6E=F 1.1526; ES=F 7,712.5; BTC-USD 81,356. FRED DGS10 2026-09-17 = 4.94 % [V]. CPI-U NSA (FRED CPIAUCNS) Aug-2026 YoY = **3.40 %**, Aug-2025 YoY 2.92 %, Feb-2026 YoY 2.41 % [V].

Design stance in one line: the sleeve is a **1/3/12-month trend filter with a magnitude gate**, expressed in **CME micro futures at tastytrade when one contract fits the vol budget**, in **ETFs below that**, and in **Kalshi economics contracts only for discrete event views the same gate already holds**. It never pyramids, never uses intraday margin, and is off more than it is on.

---

## 1. Instrument map (per theme)

Themes owned by MACRO: **GOLD, CRUDE, RATES (10Y), USD (vs EUR)**. Equity-index (MES/MNQ/M2K) and BTC (MBT) micros are documented because the charter's verify list names them, but they are **CORE instruments** (equity beta / crypto beta); MACRO may only use them as a head-assigned hedge overlay, never as a fifth theme (avoids double-counting CORE beta).

| Theme | Micro future (CME Globex code, tastytrade symbol root) | Contract unit / tick | $σ per contract per year (live level × assumed σ) | ETF fallback (ER, tax form) | Kalshi contract(s) that express the same view discretely |
|---|---|---|---|---|---|
| GOLD | **MGC** `/MGC` (COMEX) | 10 troy oz; tick 0.10 = $1.00 [V portfolio.md/T] → notional ≈ $44,160 | ≈ $7,070 at 16 % σ | **GLDM 0.10 %** (preferred), IAU 0.25 %, GLD 0.40 % — grantor trusts, 1099-B, LT gains = collectibles ≤ 28 % [T] | `KXGOLD` / `GOLD` "Price of gold" daily, source ICE [V series exists; **0 open markets today**, cadence U] |
| CRUDE | **MCL** `/MCL` (NYMEX) | 100 bbl; tick 0.01 = $1.00 [V] → notional ≈ $9,550 | ≈ $3,340 at 35 % σ | **USO 0.60–0.70 % ER, partnership K-1, 60/40 pass-through, structural contango drag** [T] → crude theme is **futures-only**; USO only if operator opts into K-1 | `KXWTI` "WTI oil on day" daily (settlement of the named CL month, source ICE) [V open]; `OIL` monthly EIA spot [V]; `KXAAAGASM` monthly AAA gas (maker-fee series) [V open]; `KXAAAGASW` weekly gas up/down [V]; `KXWTIMAX` annual high [V] |
| RATES | **10Y** `/10Y` (CBOT Micro 10-Year Yield) | **$1,000 × yield index; tick 0.001 = $1.00; DV01 = $10/bp; 2 nearest monthly contracts; cash-settled to BrokerTec 3 pm ET fixing on the last business day of the contract month; trading ceases 3 pm ET that day** [V CME article via search + Optimus; resolves portfolio.md conflict — the "0.005 = $0.50" figure on Ironbeam is wrong] | ≈ $950 at 95 bp/yr yield σ | **IEF 0.15 %** (7–10y, matches 10Y), TLT 0.15 % (20y+, ~2.5× duration); 1099, interest state-exempt [T]. Long-only: rising-yield signal in ETF mode = flat | `KXUST10AD` "10Y par yield above X on date" daily, source Treasury par-yield table [V open, strikes 4.98–5.08 %]; `KXNOTE10` monthly, `KXTNOTED` daily, `KX30YUSTW` weekly [V series exist, 0 open today]; `KXFED`/`KXFEDDECISION` (Fed upper bound after each meeting; maker-fee series) [V open through Apr-2027]; `KXRATECUTCOUNT` annual [V open] |
| USD | **M6E** `/M6E` (CME Micro EUR/USD) — short M6E = long USD | €12,500; tick 0.0001 = $1.25 [V] → notional ≈ $14,410 | ≈ $1,150 at 8 % σ | **UUP 0.77 %, commodity pool, K-1, 60/40 MTM** [T] for long-USD; **FXE 0.40 %** grantor trust, §988 ordinary [T] for long-EUR | `KXFXEURO` weekly (Fed H.10) and `KXEURUSDW` weekly (ICE/TradingView) [V series exist, 0 open today]; `KXEURUSDAH` hourly exists (not for MACRO) |
| (CORE overlay) EQUITY | MES `/MES` $5 × S&P, tick 0.25 = $1.25; MNQ `/MNQ` $2 × NDX; M2K `/M2K` $5 × RTY [V/T] | MES notional ≈ $38,560 | MES ≈ $5,780 at 15 % σ | SPY/IVV/VOO 0.03–0.09 % | `KXINXU`, `KXINXAB`, `KXSPXFOMC` (GRIND-PM territory) |
| (CORE overlay) BTC | MBT `/MBT` 0.1 BTC, tick $5 = $0.50; last Friday of month, cash to BRR [V/T] | notional ≈ $8,140 | ≈ $4,480 at 55 % σ | IBIT/FBTC ~0.25 % (property, not 1256) | `KXBTC` hourlies (GRIND-PM) |

All twelve tastytrade symbols above are on tastytrade's published futures product list (`/MES /MNQ /M2K /MGC /MCL /M6E /MBT /10Y /2YY /5YY /30Y /MYM /SIL /MHG /MNG /M6B /M6A`) [V tastytrade.com product page].

Kalshi series facts pulled live from `GET https://api.elections.kalshi.com/trade-api/v2/series/{ticker}` [V]: `fee_type` is `quadratic_with_maker_fees` for **KXFED, KXFEDDECISION, KXCPI, KXCPIYOY, KXAAAGASM, KXRATECUTCOUNT, KXGDP**; plain `quadratic` (no maker fee) for KXUST10AD, KXWTI, KXCPICORE, KXNOTE10, KXTNOTED, KXGOLD, OIL, KXFXEURO, KXEURUSDW, KXPCECORE. Contract-terms PDFs fetched: `FED.pdf` (underlying = upper bound of the target range; last trade 1:55 pm ET on meeting day; expiry 2:05 pm ET) and `CPI.pdf` (underlying = signed one-decimal MoM change in **SA CPI-U**; last trade 8:29 am ET release day) [V].

---

## 2. Regime / trend gate

### 2.1 Rule (from portfolio.md §3.3, made exact)

For each theme *i* with a daily continuous price series `P` (see 2.3) evaluated after the 4 pm ET settlement:

```
s12 = sign( P_t / P_{t-252} - 1 - RF_12m_if_ETF )     # 12-month (excess) return sign
s3  = sign( P_t / P_{t-63}  - 1 )                      # 3-month return sign
s10 = sign( P_t - SMA(P, 210) )                        # price vs 10-month SMA (≈210 trading days)
votes_long  = count(s == +1);  votes_short = count(s == -1)
dir = +1 if votes_long >= 2 else (-1 if votes_short >= 2 else 0)

sigma12 = std(daily log returns, last 252) * sqrt(252)   # annualised 12-month vol
mag     = abs(P_t / P_{t-63} - 1) / sigma12               # 3m return / 12m vol (≈ 1σ over 3m when = 0.5)

ENTER  when dir != 0 and mag > 0.50 for 5 consecutive closes and theme not in whipsaw lock
HOLD   while dir unchanged and mag > 0.25
EXIT   when dir flips or dir == 0 for 5 consecutive closes, or mag < 0.25 for 5 consecutive closes,
       or the position stop (see §6) is hit, or the roll rule (§4) says "do not roll"
```

Hysteresis (0.50 in / 0.25 out, 5-day confirmation) is the whipsaw control; Babu et al. (portfolio.md) justify trading only large moves. Signals are computed daily; **orders are placed only on the first trading day after the state changes**, never intraday on the signal day.

Direction semantics per theme: GOLD long/short MGC; CRUDE long/short MCL; RATES: `dir=+1` on the *yield* series = long `/10Y` (short duration), `dir=-1` = short `/10Y`; USD: series = DXY-like proxy or 1/EURUSD, `dir=+1` = **sell** M6E. In ETF mode only long ETF positions are allowed (GLDM, IEF/TLT, UUP/FXE); a short signal in ETF mode = flat.

### 2.2 CPI condition (commodity budget doubling)

```
yoy_t   = CPI_NSA[t] / CPI_NSA[t-12] - 1          # BLS CUUR0000SA0 (headline YoY is NSA)
rising  = yoy_t > yoy_{t-6}
INFLATION_ON = (yoy_t > 0.03) and rising          # re-evaluated on each CPI release day 8:30 ET
if INFLATION_ON: budget_multiplier[GOLD] = budget_multiplier[CRUDE] = 2.0   # still inside the 3 % sleeve cap
else: 1.0
```
Today: yoy = 3.40 % (Aug-2026), yoy six months earlier 2.41 % → **INFLATION_ON = true** [V from FRED CPIAUCNS]. Man Institute (portfolio.md) is the evidence; the doubling is applied to the *theme budget* in §3, never to leverage beyond the sleeve cap.

### 2.3 Data sources (free, tested today)

| Series | Source | Notes |
|---|---|---|
| Continuous futures for signals | Yahoo chart API `https://query1.finance.yahoo.com/v8/finance/chart/{GC=F,CL=F,ZN=F,6E=F,ES=F,BTC-USD}?range=max&interval=1d` — history from 2000-09/11 [V] | Unadjusted front-month splices; roll gaps bias the 12m return. Use for the backtest only after roll-gap adjustment (ratio-adjust at each roll), or use ETF total-return proxies below as a cross-check. |
| ETF proxies (total return) | Yahoo adjusted close: GLD (2004-11), USO (2006-04), IEF/TLT (2002-07), UUP (2007-02) [V] | USO embeds roll drag — fine for the signal (the tradable also pays it), not for MCL. |
| 10Y yield level | FRED `DGS10` daily CSV `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10` [V] | Signal series for RATES is the *yield*; s12/s3/s10 computed on yield, σ in bp. |
| CPI | FRED `CPIAUCNS` (NSA) and `CPIAUCSL` (SA) CSV [V]; BLS API v1 `…/publicAPI/v1/timeseries/data/CUUR0000SA0` (no key, 3 years, ~25 calls/day) [V]; v2 needs a free registration key (v2 unregistered quota was exhausted today) [V] | Kalshi KXCPI settles on **SA MoM**, KXCPIYOY on NSA YoY — keep both. |
| Live quotes/candles for execution | tastytrade DXLink `wss://tasty-openapi-ws.dxfeed.com/realtime`, token from `GET /api-quote-tokens` (24 h), streamer symbols from the instrument endpoint (form `/MGCZ26:XCEC`), candles `/{sym}{=d}` with `fromTime` [V developer docs] | Limits: 5 sessions, 25k subscriptions, 100 Candle subscriptions, 10k changes/min [V]. History depth per contract [U]. |
| Fed calendar / release dates | federalreserve.gov FOMC calendar; BLS CPI schedule | Feed the event registry (§7). |

---

## 3. Sizing

### 3.1 Vol-budget per theme

```
SLEEVE_VOL      = 0.03 * E                     # E = total equity (head's number, daily)
N_active        = number of themes with dir != 0 after the gate
b_theme         = min( SLEEVE_VOL / sqrt(max(N_active,1)), 0.02 * E ) * budget_multiplier[theme]
b_theme         = min(b_theme, 0.02 * E)       # per-theme hard cap 2 % of equity even when doubled
# realised-vol overlay (§6): if 20d realised sleeve vol > 1.5 × SLEEVE_VOL, scale every b_theme by SLEEVE_VOL / realised
```

### 3.2 Contracts

```
sigma_c   = notional_per_contract * sigma12_price      # $ vol per contract per year (RATES: sigma12_bp * $10)
n_target  = b_theme / sigma_c
if n_target >= 0.70:   n = max(1, round(n_target))     # futures
elif n_target >= 0.10: n = 0 → ETF mode, notional = b_theme / sigma12_price (fractional shares at Public)
else:                  theme skipped (budget too small to matter)
No pyramiding: n is recomputed only at roll dates or when a state change or the risk overlay demands a cut.
```

### 3.3 One-contract feasibility by equity band (b_theme = 1.5 % with four themes active; 2.0 % when it is the only active theme)

| Contract | $σ / contract / yr (live) | Min equity for 1 contract at 1.5 % | at 2.0 % | Overnight maintenance margin (AMP table, CME-derived, date unstated [T]) |
|---|---|---|---|---|
| `/10Y` | ≈ $950 | **≈ $44k** | ≈ $33k | $352 |
| `/M6E` | ≈ $1,150 | **≈ $54k** | ≈ $40k | $231 |
| `/MCL` | ≈ $3,340 | **≈ $156k** | ≈ $117k | $1,089 |
| `/MBT` (CORE) | ≈ $4,480 | ≈ $209k | ≈ $157k | [U] |
| `/MES` (CORE) | ≈ $5,780 | ≈ $270k | ≈ $202k | $2,863 |
| `/MGC` | ≈ $7,070 | **≈ $330k** | ≈ $247k | $2,426 |

Consequence: at a 3 % sleeve vol budget, **gold and crude are ETF/off until the account is well into six figures**; only the yield and FX micros fit a $50–100k account. The vol numbers are assumptions for illustration — the arm computes `sigma12` live and the table is regenerated nightly.

### 3.4 Margin headroom rule

```
IM_total(sleeve) <= 0.40 * MACRO_cash_allocation            # allocation itself <= 15 % of E
IM_total(sleeve) <= 0.05 * E
cash_in_futures_account >= 2 * IM_total + VaR_3d_99(sleeve)  # T-bill buffer per portfolio.md §5.3
```
Requirements come from `GET /margin/accounts/{acct}/requirements` and the pre-trade `POST /margin/accounts/{acct}/dry-run` [V endpoint names]; tastytrade's `futures-margin-rate-multiplier` intraday facility is **never applied for** (25 % intraday margin 8:30–15:00 CT [T]) — the sleeve is overnight-margined at all times.

### 3.5 ETF substitution below feasibility

ETF mode uses the CORE arm's broker (Public, $0 commission, fractional) and the CORE arm's ticker ownership: **the same ticker is never held in two arms** (portfolio.md §6 wash-sale rule). If CORE already owns GLD/IAU as an absolute-momentum alternative, MACRO's gold-ETF exposure is booked as a MACRO sub-account line on that same position in the head journal, not as a second holding elsewhere. Rebalance ETF notional only when drift > 20 % relative or a state change occurs.

---

## 4. Roll and expiry protocol

| Contract | Listing cycle used | Last trade / delivery risk | Roll trigger (business days before) | 2026-27 dates to load in the calendar (verify against CME [U]) |
|---|---|---|---|---|
| MGC | Feb/Apr/Jun/Aug/Oct/Dec (G J M Q V Z) | LTD 3rd-last business day of the contract month; **deliverable**, FND = last business day of the month before → never hold past FND − 3 | roll 5 bd before FND | GC/MGC Oct→Dec active roll 2026-09-28 (FND 2026-09-30, LTD 2026-10-28); Dec→Feb roll 2026-11-25 (FND 2026-11-30, LTD 2026-12-29) [T tradingnewsterminal] |
| MCL | monthly | LTD = CL rule: trading ceases 3 bd before the 25th of the month preceding delivery; settlement method (financial vs deliverable) [U] — exit regardless | roll 4 bd before LTD | Nov26 LTD 2026-10-20; Dec26 LTD 2026-11-20; Jan27 2026-12-19; Feb27 2027-01-20; Mar27 2027-02-20 [T two calendars agree] |
| 10Y | 2 nearest months | cash-settled 3 pm ET last business day of contract month [V] | roll 3 bd before month-end **only if** the next month's spread ≤ 2 ticks; otherwise flatten and re-enter after the new front month prints volume | month-ends: 2026-09-30, 10-30, 11-30, 12-31, 2027-01-29 |
| M6E | Mar/Jun/Sep/Dec | LTD 9:16 CT on the 2nd business day before the 3rd Wednesday; **physically delivered EUR** → out by LTD − 5 | roll 8 bd before LTD (same week as the equity roll) | Dec26 LTD 2026-12-14; Mar27 LTD 2027-03-15 [T rule-derived] |
| MES (CORE) | quarterly | 3rd Friday 9:30 ET SOQ, cash | roll 8 bd before LTD | LTD 2026-12-18, roll 2026-12-10/11; 2027-03-19 [T] |
| MBT (CORE) | monthly | last Friday 4 pm London, cash to BRR | roll 3 bd before | — |

Roll execution: during 09:00–15:00 ET only; two single-leg limit orders (close old at mid, open new at mid) sent back-to-back with a shared `external-identifier` prefix, second leg only after the first fills; if the second leg is not filled within 10 min, re-price to the touch; if still unfilled by 15:30 ET, stay flat and let the gate re-enter next day. **Do-not-roll rule:** if `mag < 0.40` on the roll day, exit instead of rolling (portfolio.md §3.3 "signal weakening near roll"). Roll cost is logged as carry (calendar spread × multiplier) so the journal separates trend PnL from roll yield. The arm refuses any *opening* intent in a contract inside its roll window and force-flattens at roll date + 1 if a roll failed; it does not rely on tastytrade's pre-FND liquidation [U timing].

Symbol convention: tastytrade uses `/` + product + month code + **1-digit year** for CME products (`/ESZ2`, `/CLZ2`) and 2-digit for VX (`/VXX22`) [V]; expected 2026 symbols `/MGCZ6`, `/MCLX6`, `/10YV6`, `/M6EZ6` — confirm via `GET /instruments/futures?product-code[]=MGC` (returns `active-month`, `next-active-month`, `expiration-date`, `last-trade-date`, `first-notice-date`, `stops-trading-at`, `streamer-symbol`, `tick-size`, `notional-multiplier`, `roll`) [V field list from reference page; types U].

---

## 5. Execution

### 5.1 tastytrade Open API (primary arm `arm:tasty`)

- **Auth:** OAuth2 personal grant → non-expiring refresh token → 15-min access JWT via `POST /oauth/token`; mandatory `User-Agent: product/version`; token lives only on the primary (orchestration §3.4) [V brokers.md].
- **Discover:** `GET /instruments/future-products` → `GET /instruments/futures?product-code[]=MGC&product-code[]=MCL&product-code[]=10Y&product-code[]=M6E` (leading slash not required in the query) [V].
- **Pre-trade:** `POST /margin/accounts/{acct}/dry-run` then `POST /accounts/{acct}/orders/dry-run` with the identical body; reject if `change-in-buying-power` breaches §3.4 [V endpoint + response field names].
- **Order body** (single leg, from the docs' futures example) [V]:
```json
{ "order-type": "Limit", "time-in-force": "Day", "price": "4415.90", "price-effect": "Debit",
  "legs": [ { "instrument-type": "Future", "symbol": "/MGCZ6", "action": "Buy to Open", "quantity": 1 } ],
  "external-identifier": "macro-GOLD-2026-09-21-entry-1" }
```
  `price-effect` = Debit for buys, Credit for sells. The docs' futures example shows `"action": "Buy"`; whether futures legs take `Buy`/`Sell` or `Buy to Open`/`Sell to Open`/`… to Close` is settled by a cert dry-run before any live order [U]. Submission is **not idempotent** → every intent carries a unique `external-identifier` and the arm reconciles `GET /accounts/{acct}/orders/live` before any retry [V].
- **TIF:** `Day`, `GTC`, `GTD` (+ `gtc-date`) [V]. Entries and rolls: `Day` limits at mid, re-priced to the touch after 10 min, cancelled at 15:30 ET. Stops: resting `GTC` Stop (`stop-trigger`) is allowed only as a *disaster* stop at 3 × the monthly σ budget; the working stop is the head's daily rule (§6). Market orders: never overnight; only for a kill (§6) inside 09:00–15:00 ET.
- **Brackets:** `POST /accounts/{acct}/complex-orders` supports OTOCO/OCO/OTO/PAIRS [V]; futures-specific bracket example not documented [U].
- **Session hours:** `GET /market-time/futures/sessions/current` (collections `CME`, `CFE`) [V]. CME Globex: Sun–Fri 18:00–17:00 ET with a 17:00–18:00 ET break; 10Y ceases 15:00 ET on its last day. The arm trades **09:00–15:00 ET only** (RTH liquidity), never in the first two minutes after the 18:00 ET reopen, never in the 16:00–18:00 ET window.
- **Rate limits:** REST unpublished ("reasonable thresholds", bare 429) [V] → arm budget 2 req/s sustained, 10 burst, exponential backoff with jitter on 429, and a circuit breaker after 3 consecutive 429s. DXLink limits per §2.3.
- **Streams:** account streamer `wss://streamer.tastyworks.com` for order/fill/position events; heartbeat to head every 5 s; self-cancel resting orders if the head is lost for 30 s (orchestration §3.2).
- **Fees:** micro $0.75 + $0.30 clearing per side + exchange + NFA ($0.02/rt) [V]; ≈ $3.1 round trip on MGC.
- **Sandbox:** `api.cert.tastyworks.com` fills are synthetic (market fills at $1, limit < $3 fills, ≥ $3 never) and market data returns 502 [V] → cert is for plumbing tests only; price-realistic paper runs in the head's own simulator against DXLink data (§8).

### 5.2 IBKR alternate (`arm:ibkr`, candidate)

- TWS API / `ib_async`: `Contract(symbol='MGC', secType='FUT', exchange='COMEX', currency='USD', lastTradeDateOrContractMonth='202612')`; MCL → `NYMEX`, MES/M6E/MBT → `CME`, 10Y → `CBOT`; `qualifyContracts` / `reqContractDetails` before use; `CONTFUT` only for `reqHistoricalData` (no orders) [T IBKR docs via search]. `LimitOrder(action='BUY', totalQuantity=1, lmtPrice=…, tif='DAY'|'GTC')`.
- Pacing 50 msg/s TWS (100 with booster), Client Portal 10 req/s [V brokers.md]. Paper account available once live is funded [V].
- Commissions [V pricing page HTML, fetched today]: **E-micro futures incl. MES, MNQ, M2K, 2YY, 5YY, 10Y, 30Y, MCL, MGC, SIL, …: USD 0.25/contract (≤ 1,000/month), 0.20 / 0.15 / 0.10 at higher tiers**; **MBT USD 0.85/contract**; exchange and regulatory fees passed through (ES example: exchange $1.38) [V]. Micro exchange fee amounts [U].
- Why alternate, not primary: no futures + Kalshi API self-match risk (IBKR routes Kalshi contracts too → §7 ownership lock must cover it), and the head already has the tastytrade token flow.

---

## 6. Risk

| Control | Rule |
|---|---|
| Sleeve vol | target 3 % of E; if 20-day realised sleeve vol > 4.5 % (1.5×) scale all themes by 3/realised at the next session; > 6 % (2×) flat the sleeve |
| Per-theme cap | b_theme ≤ 2 % of E (even after CPI doubling); ≤ 3 contracts per theme below $1M equity regardless of formula |
| Per-position stop | daily-mark loss on a theme > 2 × (b_theme / √12) since entry → exit next session; disaster GTC stop at 3× |
| Correlation to CORE | 60-day correlation of sleeve daily PnL with CORE daily PnL > 0.5 **and** sleeve is net long risk (long crude/gold, long EUR) → cut sleeve budget 25 %; MES/MNQ/M2K/MBT are CORE beta and count against CORE limits, never MACRO |
| Whipsaw lock | ≥ 4 state changes on a theme in 6 months → theme off 90 days (portfolio.md failure monitor a) |
| Drawdown ladder hooks (charter, total equity vs 12-month HWM) | **−5 %:** SLEEVE_VOL 3 % → 1.5 % (cut at next session if over); **−10 %:** no new entries, existing positions exit on their rules or within 5 sessions; **−15 %:** flat immediately (RTH market orders). Re-risk one rung per new 3-month equity high, ≥ 20 trading days apart. (portfolio.md's −8/−12/−16/−20 ladder is superseded by the charter's; note the discrepancy for the head's config.) |
| Margin-call prevention | §3.4 headroom; nightly `GET /accounts/{acct}/balances`; if maintenance excess < 25 % of maintenance requirement, halve the largest-σ position before the broker acts; the arm never posts new cash automatically — that is a head decision; CME margin changes refreshed weekly |
| Event guard | no new entries in RATES/USD in the 24 h before FOMC or CPI 8:30 ET; existing positions are kept (trend, not event, is the thesis) but the event is registered in §7 |
| Data guard | signal data older than 2 sessions, or Yahoo/FRED unreachable → no new entries; > 5 sessions → exits only |
| Kill | head `risk.kill` fan-out → cancel all, flatten in RTH; reconciliation mismatch between arm fills and head journal > 1 contract → freeze the arm until manual reconcile; sleeve drawdown from its own HWM > 4 % of E → pause 60 days and written review |

---

## 7. Kalshi macro contracts

Purpose: express **the same gated view as a discrete event bet** with bounded loss, never as a separate alpha engine. Examples: RATES `dir=+1` (yields trending up, mag > 0.5) → consider `KXUST10AD-…-T{strike}` YES above a strike the futures-implied distribution puts at ≥ 60 % when the ask ≤ 50 ¢ after fees; CRUDE `dir=+1` → `KXWTI` daily above-strike or `KXAAAGASM` monthly; INFLATION_ON → `KXCPIYOY` above-strike only when the head's CPI nowcast (not this sleeve's job to build) disagrees with the market by ≥ 8 ¢.

Rules:
1. **Routing and self-match.** tastytrade's Predict tab and IBKR both route into Kalshi's book and Predict is not reachable via the Open API [V brokers.md] → MACRO Kalshi intents go through the **existing Kalshi-direct or Webull arm**, and through the **same head registry** as GRIND-PM: `(kalshi_ticker, side)` ownership lock, `event_id` aggregation (Rule 3.3(b), 5.17(c), 5.19(f)). The MACRO sleeve is a *strategy tag* on the intent, not a new arm.
2. **Aggregation across sleeves.** The head's event registry maps FOMC-2026-10-28 → {KXFED-26OCT-*, /10Y position, /M6E position}; CPI-2026-10-14 → {KXCPI-26SEP-*, KXCPIYOY-26SEP-*, /MGC, /MCL}. Aggregate per-event exposure cap = the smaller of the Kalshi position limit [U per series] and 0.5 % of E across sleeves; GRIND-PM and MACRO cannot be on opposite sides of the same ticker (lock) and their same-side sizes are summed against the limit.
3. **Fees.** taker `ceil(0.07·P·(1−P)·C)`, maker `0.0175·P·(1−P)·C` on `quadratic_with_maker_fees` series (KXFED, KXCPI, KXCPIYOY, KXAAAGASM, KXRATECUTCOUNT), maker ≈ 0 on plain `quadratic` series (KXUST10AD, KXWTI, KXNOTE10) [V flags from API today; formula T — the fee PDF is behind a Vercel checkpoint]. Prefer resting maker orders on the plain-quadratic series; via Webull the flat $0.02/side applies. The router (orchestration §3.1) picks the arm.
4. **Sizing.** Max loss per event ≤ 0.25 % of E and ≤ 20 % of MACRO's capital allocation in aggregate; hold to settlement; no more than 2 open macro events at a time.
5. **Dormant series.** KXGOLD, KXEURUSDW, KXFXEURO, KXNOTE10, KX30YUSTW showed **0 open markets** today [V] → the arm queries `/markets?series_ticker=…&status=open` daily and only the series with a live book are eligible.
6. **Settlement sources differ from the futures:** KXUST10AD settles on the Treasury par-yield table (≈ 3:30 pm close), the 10Y future on BrokerTec 3 pm; KXWTI on the named CL month settlement; KXCPI on SA MoM. The head prices each with its own source, never with the futures mark.

---

## 8. Paper plan and graduation

**Phase 0 — gate validation on history (before any paper order).** Data: Yahoo continuous futures (ratio-adjusted at each roll) cross-checked with ETF total-return proxies, FRED DGS10/CPIAUCNS, 2000–2026. Costs: $3.20 round trip + 1 tick slippage per micro, roll cost each cycle, ETF ER pro-rata. Report per theme: % of months gate-ON (expect ≤ 40 %), net Sharpe of the gated strategy vs (a) 12-month-only and (b) always-on, hit rate and average gain/loss per state, whipsaw count 2012–2019, behaviour in 2008, 2014–15 (crude), 2020, 2022 (rates/USD), max drawdown at 3 % vol (accept ≤ 8 %), and the equity-band feasibility table (§3.3) recomputed on each year's levels. **Acceptance:** net Sharpe ≥ 0.3 over the full sample and ≥ 0 in every 5-year window except at most one; the 5-day confirmation must not cost more than 15 % of gross PnL vs no confirmation.

**Phase 1 — plumbing paper (cert sandbox).** OAuth, instrument discovery, dry-runs, order/replace/cancel, account streamer, 24-h reset handling, symbol-format test (`/MGCZ6`), leg-action test, margin dry-run parse. Exit criterion: 10 consecutive days without an unreconciled order.

**Phase 2 — shadow paper (real data, simulated fills).** DXLink live quotes; fills simulated at mid + 1 tick inside RTH; roll protocol exercised on the next real roll date; Kalshi macro intents through the Kalshi demo env with the shared registry. **The clock does not start until a real gate-ON event occurs** (charter: "paper until a trend regime actually triggers"). Graduate when: ≥ 1 full entry→exit cycle on ≥ 1 theme, ≥ 1 roll executed, ≥ 60 sessions of reconciled journal, realised paper vol within 0.5×–1.5× of budget, and the failover drill has run with the arm in standby.

**Phase 3 — canary.** One contract in the smallest-σ eligible instrument (`/10Y` or `/M6E`) or a ≤ 1 % ETF line; SLEEVE_VOL capped at 1 % until two cycles complete or six months pass, whichever is later; then full budget. Kalshi macro stays at half size until GRIND-PM's own canary is live (no sleeve inherits another's evidence).

---

## 9. Equity-band behaviour

| Band | Futures? | What trades | Sleeve capital | Kalshi macro |
|---|---|---|---|---|
| **< $5k** | no | **off** (a 3 % vol budget is $150/yr of σ — below cost) | 0 % | off |
| **$5k–25k** | no | ETF-only, long-only: GLDM (gold), IEF (rates-down signal), optional UUP (USD-up; K-1 — default skip below $25k). Crude off. Fractional shares at Public, rebalance on state change or > 20 % drift | ≤ 5 % | ≤ $50 max loss per event, ≤ 1 open |
| **$25k–100k** | from ≈ $45k `/10Y`, ≈ $55k `/M6E` (n_target ≥ 0.7) | 10Y and M6E micros once feasible, otherwise IEF/UUP; gold via GLDM; crude off (or Kalshi KXWTI only) | 5–10 % (margin is small; the rest sits in T-bills) | ≤ 0.25 % E per event, ≤ 2 open |
| **$100k–1M** | 10Y, M6E from the start; `/MCL` from ≈ $120–160k; `/MGC` from ≈ $250–330k | Full map; ETF substitution per theme below its feasibility line; max 3 contracts per theme; sleeve still holds 1–3 contracts per theme even at $1M | 5–15 %, regime-gated | as above; IBKR only if router shows cheaper all-in |

---

## 10. Tax

- **Futures (MGC, MCL, 10Y, M6E; MES, MBT):** §1256 — 60 % LT / 40 % ST regardless of holding period, marked to market 12/31, Form 6781, **no wash-sale rules** [V portfolio.md §3.2]; 1256 net losses may be carried back three years against prior 1256 gains (election on Form 6781) [T]. Keep the futures in the taxable tastytrade account; nothing here belongs in the IRA except possibly the equity-index overlay.
- **Gold ETFs (GLDM/IAU/GLD):** grantor trusts; LT gains taxed as collectibles up to 28 %; trust expense sales generate small monthly 1099-B basis adjustments; **wash-sale rules apply and cross accounts** → one arm owns the gold ETF ticker (§3.5).
- **USO:** partnership → **Schedule K-1** (arrives ~March, delays filing), the fund's own 1256 gains pass through 60/40, UBTI exposure in an IRA, possible state filing footprint, plus structural roll drag [T] → crude is futures-only by default.
- **UUP:** commodity pool → **K-1**, 60/40 MTM pass-through [T]; **FXE:** grantor trust, currency gains ordinary under §988 [T]. Both acceptable only in the ETF-only bands and only if the operator accepts the K-1 (UUP).
- **IEF/TLT:** 1099; interest ordinary but state-tax-exempt (Treasury), gains ordinary capital rules; wash sales apply (IEF vs TLT are not substantially identical; IEF vs another 7–10y Treasury ETF is arguable).
- **Kalshi economics contracts:** characterisation unresolved (ops.md); log separately; CPA opinion before scaling (orchestration §4 item 5).
- **§475(f):** not elected in year one; if ever elected, limit it to securities so 1256 keeps 60/40 (portfolio.md §6).

---

## 11. UNVERIFIED items, written as tests

| # | Item | Test |
|---|---|---|
| 1 | tastytrade futures leg `action` vocabulary (`Buy` vs `Buy to Open`) | cert: `POST /orders/dry-run` both variants on `/MESZ6`; keep the one that returns a fee/BP effect |
| 2 | Symbol format for numeric roots and 1-digit year (`/10YV6`, `/MGCZ6`) | `GET /instruments/futures?product-code[]=10Y&product-code[]=MGC`; assert `active-month` symbol matches |
| 3 | `Future` response field types (`first-notice-date`, `stops-trading-at`, `roll`, `tick-size`, `notional-multiplier`) | print one MGC and one 10Y record; assert 10Y `tick-size` = 0.001 and multiplier 1000 (CME) — if 0.005/0.5 appears, Ironbeam was right and the DV01 math must be re-checked |
| 4 | tastytrade per-contract initial/maintenance margins (AMP table is third-party, undated) | `POST /margin/accounts/{acct}/dry-run` for 1 lot of each micro; store weekly |
| 5 | tastytrade forced-liquidation timing before FND/LTD (FM-call article did not render) | support article 43000435245 / ask support; encode as `roll_deadline` |
| 6 | REST rate limit | probe at 2, 5, 10 req/s in cert; record the first 429 |
| 7 | DXLink daily-candle history depth for a specific contract | request `/MGCZ26:XCEC{=d}` with `fromTime` two years back; count bars |
| 8 | Futures bracket (OTOCO) body for futures | cert dry-run on complex-orders |
| 9 | MCL settlement method (financial vs deliverable) | CME spec page (403 to fetchers today) via browser; either way exit before LTD |
| 10 | MGC/M6E/10Y/MCL 2026-27 exact LTD/FND dates | CME product calendars via browser; diff against §4 |
| 11 | Kalshi fee PDF (maker fee on `quadratic_with_maker_fees`, 0.07 multiplier) | fetch in browser; compare with a live fill's fee on KXUST10AD (maker) and KXFED (maker) in demo/live |
| 12 | Kalshi position limits for KXFED/KXCPI/KXUST10AD/KXWTI | contract-terms PDFs (fetched but truncated at "Posi…") — re-extract or read the market's `rules_secondary`/event page; load into the registry |
| 13 | Dormant series cadence (KXGOLD, KXEURUSDW, KXNOTE10, KX30YUSTW) | daily `status=open` poll for 30 days; drop series with < 5 open days |
| 14 | Webull events API lists KXFED/KXCPI/KXUST10AD/KXWTI | query the Webull sandbox event list; otherwise MACRO Kalshi intents go Kalshi-direct only |
| 15 | IBKR: micro exchange fee pass-through amounts; Kalshi contract addressing in TWS API; event contracts in paper | pricing page fee table; `reqContractDetails` on a Kalshi ticker; paper test |
| 16 | Yahoo continuous-futures roll gaps | compare 12m returns from Yahoo GC=F vs GLD TR minus ER; if divergence > 3 % in any year, build a stitched series from per-contract DXLink candles |
| 17 | BLS API v2 key and quota; FRED as primary | register key; assert v1/v2/FRED agree on the latest CPI print |
| 18 | ETF expense ratios and forms (GLDM 0.10 %, USO 0.60–0.70 %, UUP 0.77 %, FXE 0.40 %) | issuer fact sheets; confirm K-1 status for the current tax year |
| 19 | Realised σ assumptions (gold 16 %, crude 35 %, 10Y 95 bp, EUR 8 %) | replace with live `sigma12` on day one; regenerate §3.3 |
| 20 | Charter ladder (−5/−10/−15) vs portfolio.md ladder (−8/−12/−16/−20) | head config must carry one; this spec assumes the charter's |
