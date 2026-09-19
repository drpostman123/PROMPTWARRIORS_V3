# Picador — quantitative / data / market-structure gaps in PICADOR_WEBULL_PLAN.md

Research date: 2026-09-19 (Saturday), 01:54–02:10 UTC. All Kalshi numbers below were pulled live from
`https://api.elections.kalshi.com/trade-api/v2` (no auth) unless marked. Raw pulls are in this directory
(`kxbtc_*.json`, `kxinxu_*.json`, `books_1822_raw.json`, `scan_KXBTC-26SEP1823.json`, contract-terms
`*.txt`, `cf_rti_methodology.txt`, `todorov_intraday.txt`, `rules1113248*.txt`).

Legend: **[M]** measured by me; **[S]** sourced document; **[U]** could not be verified.

---

## 0. Headline corrections to the plan

| # | Plan says | Reality | Impact |
|---|---|---|---|
| 1 | BTC/ETH hourlies settle on BRTI "value at HH:00" and S4 treats the print as "known to within ticks" | Settlement = **simple average of the 60 one-second BRTI prints in the minute before HH:00** (BTC.pdf contract terms; `rules_primary` of every market). ETH uses ERTI the same way. **[S][M]** | S4 on BTC must forecast a 60-s average, and the settlement number is *partly observable* during the last minute. Kalshi itself streams the running 60-s average. |
| 2 | Resolution feed for crypto = "subscribe to CF Benchmarks" (paid, contact-for-license) | Kalshi's own WebSocket has `cfbenchmarks_value` (1 Hz, with `avg_60s_data` trailing 60-s average) and `cfbenchmarks_value_5hz`; historical ticks via `/cfbenchmarks/` REST passthrough (50 read tokens per call, "appropriate entitlement"). Needs a Kalshi API key = KYC'd Kalshi account (free). **[S]** | The plan's "no Kalshi account, only Webull" architecture is wrong for S2/S4: open a Kalshi account for data even if execution stays on Webull. CF Benchmarks direct API needs a license (no free tier found). |
| 3 | Index contracts: "which print at 4pm?" left open; S1 prices the 4pm contract from SPXW/SPY options | Kalshi's 4pm `expiration_value` is a ~16:00:00 real-time print, **not the official S&P close**: 2026-09-18 Kalshi 7650.12 vs official close 7650.50; 2026-09-17 7637.71 vs 7637.76; 2026-07-17 7457.67 vs 7457.69. Settlement timestamp is close+124–145 s (median 127 s over 21,351 markets). **[M][S]** | SPXW PM options settle to the official close (includes closing-auction prints); the Kalshi 4pm contract does not. Differences of 0.05–0.5 pts; 21% of KXINXU events settle within 0.5 pt of a 5-pt strike boundary, so feed choice flips ~4% of 4pm outcomes. |
| 4 | Index hourlies priced from a 0DTE density | 0DTE options give the density at **16:00** only. Hourly contracts at 11:00–15:00 need the *intraday variance profile*. Measured from 49 days of KXINXU settlement values: share of 10:00–16:00 variance by hour = 21% / 20% / 7% / 6% / 16% / 30% (10-11, 11-12, 12-13, 13-14, 14-15, 15-16). Midday hourly sd ≈ 12 bp vs 26 bp for 15:00–16:00. **[M]** | A flat "σ√t" split of the 0DTE variance mis-prices the 13:00 contract by a factor ~2.7 in variance. The profile must be estimated and re-estimated (Todorov & Zhang 2024 show it drifts year to year). |
| 5 | Index hourlies open "10:00–16:00" | Hourly index events open **the prior day at 16:00 ET** (H1000–H1500) and 03:10 ET (H1600). 4.7% of the 4pm event's volume traded before 09:30 ET; Webull index hours start 08:00 ET. **[M]** | Pre-open trading exists (Webull 08:00–09:30) but there is no 0DTE chain yet: fair value must come from ES futures + overnight vol, or be skipped. |
| 6 | S3 exploits `B = T_low − T_high` inside crypto events | Hourly BTC events contain **only two threshold markets (the two extreme tails)** plus 68–186 $100 ranges. The B–T identity has no interior legs to trade. **[M]** | S3 in crypto reduces to (a) sum-of-ranges vs 1 and (b) monotonicity of the tails; both were clean today (sum of asks 1.25 at T−2 min, sum of bids 0.89). |
| 7 | S4: "take the ask at 90–97¢ in the last minutes" | Markout over 13 settled BTC hourly events (2026-09-18 08:00–21:00 ET): takers who paid 90–98¢ in the last 120 s won 93.0% at an average 93.7¢ → **−2.6¢/contract after the 2¢ fee**; in the 120–300 s window they won 88.4% at 94.5¢ → **−8.1¢**. Takers buying the cheap side (2–10¢) in the last 120 s won 16.3% at 5.3¢ → **+9.0¢**. **[M]** | On a $100-wide range, 120 s before expiry BTC's 2-minute σ is ≈$56 (at DVOL 35%), so the range half-width is only 0.9σ: the "known outcome" premise fails until ~30 s before close, when Webull DAY-limit latency and the 60-s averaging make it a pure speed game. |
| 8 | Graduation gates at N ≥ 30/60 trades | With a 3¢ net edge at 50¢ the per-trade Sharpe is 0.06; t = 2 needs ~1,100 independent trades (5¢: ~390; 8¢: ~150). Same-hour strikes are one bet, not N. **[M]** | Gates need N in the hundreds of *independent events*, or a different statistic (per-event PnL, Wilson bound on event hit-rate). |
| 9 | Quarter-Kelly per market | Kelly with correlated same-event legs: 3 legs at quarter-Kelly ≈ ¾-Kelly on one bet. Monte Carlo: 3¢ true edge, 3 correlated legs, 2,000 trades → median max drawdown 61% (p95 81%). A 5¢ *estimated* edge that is really 0 → median max DD 96%. **[M]** | Size per *event* (worst-case terminal-value scenario), not per market; shrink the edge estimate before Kelly. |

---

## 1. Settlement mechanics

### 1.1 Index hourlies (KXINXU S&P 500, NASDAQ100I / KXNASDAQ100U Nasdaq-100)

Contract terms **[S]** (`https://assets.kalshi.com/contract_terms/INX.pdf`, extracted to `INX.txt`; Nasdaq identical in
`NASDAQ100I.txt`):
- Underlying: "the price of the S&P 500 Index <on/before> <time> on <date>". **Source Agency: Kalshi** (Nov-2024
  amendment; the redline `rules1113248693.pdf` shows it changed from "Quandl Data" to "Kalshi"; the actual feed is
  "Appendix C (Confidential) – Source Agency" in the CFTC filing `rules1113248695.pdf`). The Google Finance link in
  `settlement_sources` is labelled "For example, Google Finance" and the terms say access instructions are "non-binding
  and provided for convenience only".
- `<time>` "will refer to a set of hours, minutes, seconds, and milliseconds. The only times listed will be for
  traditional market hours (9:30 AM – 4 PM ET), and only prices recorded in those times will be included."
- "If no data is available for <time> on <date>, the Expiration Value will be the value most recently available prior
  to that <time>" (item 6 of the Nov-2024 amendment: last available value rather than 0).
- Expiration time: "the sooner of the first 10:00 AM ET following ... or at least one minute after <time>."
  Settlement "no later than the day after", unless under Market Outcome Review (Rule 6.3(c)/7.1; 24-hour clock).
- Position limit $7,000,000 per member (raised from $25,000 in Nov 2024).
- Strikes are quoted as `T7649.9999` with `strike_type = greater_or_equal` on `floor_strike = 7650`; the 4pm market's
  `rules_primary` reads "If the end-of-day S&P 500 index value on September 18, 2026 is above 7744.9999 ...".

Measured **[M]** (21,351 settled KXINXU markets, 343 events, 2026-07-13 → 2026-09-18, live `/markets?status=settled`):
- `expiration_value` is reported to 2 decimals (e.g. `7650.1200`, `7642.0800`).
- Settlement latency after `close_time`: median 127 s, p99 1,324 s, max 2,512 s (event KXINXU-26AUG17H1000, 42 min).
  540 markets (≈9 events, 2.6%) took > 10 min — consistent with occasional manual review; no reversal evidence found **[U]**.
- **4pm value vs official close**: 2026-09-18 Kalshi 7650.12 vs official 7650.50 (Yahoo/CNBC via search);
  2026-09-17 7637.71 vs 7637.76 (FRED / S&P DJI); 2026-07-17 7457.67 vs 7457.69 (BBN Times). So the "end-of-day" value
  is a ~16:00:00 real-time print captured before closing-auction prints finish updating the index, settled at 16:02.
  Sources: https://finance.yahoo.com/markets/stocks/articles/stock-market-today-sept-18-134541673.html ,
  https://fred.stlouisfed.org/series/SP500 , https://x.com/BBNTimes_en/status/2078259437643485558 .
- Boundary proximity: of 343 hourly settlements, 33 were within 0.25 pt of a 5-pt strike boundary, 72 within 0.5 pt,
  141 within 1.0 pt. With feed differences of 0.05–0.5 pt, roughly 1 in 25 4pm outcomes depends on which feed you look at.
- Early closes: Kalshi listed only H1000–H1300 on 2025-11-28 and 2025-12-24 (1pm closes; `KXINXU-25NOV28H1300`
  settled 6849.09, `KXINXU-25DEC24H1300` 6932.05); no H1400/H1600 events exist for those dates and none for 2026-07-03
  (holiday). So the "last available value" clause has not been exercised on hourlies; Kalshi simply does not list them.
- Trading windows: H1000…H1500 events `open_time` = previous day 20:00Z (16:00 ET); H1600 event opens 07:10Z (03:10 ET).
- Nasdaq: `NASDAQ100I` had **no open markets** on Saturday and the settled list was empty in the live tier; the current
  Nasdaq hourly series appears to be `KXNASDAQ100U` (series list also shows `KXINX15M`, `KXNDQ15M`-style 15-minute
  products and `KXINXHUD` up/down). Verify the exact Nasdaq hourly ticker before coding **[U]**.
- Volume (median contracts per event, by ET hour): 10:00 171k, 11:00 153k, 12:00 144k, 13:00 122k, 14:00 113k,
  15:00 133k, **16:00 358k**. 2026-09-18 4pm event: 409,553 contracts, top strike (T7634.9999) 114,730.

### 1.2 Crypto hourlies (KXBTC, KXETH)

- Contract terms **[S]** (`BTC.pdf`): "spot price of one Bitcoin in U.S. dollars at <time>, according to a simple
  average of the CF Bitcoin Real-Time Index ("BRTI") for the minute (60 seconds) prior to <time>". Source Agency CF
  Benchmarks. "If no data is available on the Expiration Date at the Expiration Time, then the market resolves to No."
  Position limit **$1,000,000 per strike per member**. Every market's `rules_primary`: "If the simple average of the sixty
  seconds of CF Benchmarks' Bitcoin Real-Time Index (BRTI) before 10 PM EDT is above 85799.99 ...". ETH: same with ERTI.
  Kalshi help: https://help.kalshi.com/en/articles/13823838-crypto-markets . Third-party confirmation that all
  frequencies use the same 60-s BRTI average (the "BRRNY for hourlies" claim is wrong):
  https://predictionmarketspicks.com/articles/how-kalshi-settles-bitcoin .
- Event structure **[M]**: each hourly event opens exactly one hour before close (e.g. KXBTC-26SEP1823 open 02:00Z
  close 03:00Z); 68–188 markets: `between` ranges $100 wide plus exactly **two** threshold markets (`less` at the bottom
  tail, `greater` at the top tail). Daily (17:00 ET) and weekly events live in the same series with the same shape.
  `expected_expiration_time` = close + 5 min; `can_close_early: true`.
- BRTI methodology **[S]** (`docs.cfbenchmarks.com/CME CF Real Time Indices Methodology.pdf`, extracted): order-book
  based (consolidated bid/ask price-volume curves across Constituent Exchanges, mid price-volume curve averaged over a
  dynamic order-size cap), Deviation-from-Mid threshold 0.5% for BRTI, "Effective Time every 200 milliseconds",
  Retrieval Lag Threshold 10 s; contingency rules for delayed/erroneous books and Expert Judgement. Published once per
  second (200 ms on request), 24/7. https://www.cfbenchmarks.com/data/indices/BRTI .
- Access **[S]**: CF Benchmarks' own REST/WebSocket require an API key "obtained by contacting CF Benchmarks for a
  license" (https://docs.cfbenchmarks.com/api/websocket/intro/ , https://docs.cfbenchmarks.com/api/websocket/value/);
  public `GET /api/v1/values?id=BRTI` returns `{"error":"Unknown id"}` without a key **[M]**. **Kalshi passthrough**:
  WebSocket channel `cfbenchmarks_value` (≈1 Hz; message carries raw CF frame, `avg_60s_data` trailing-60-s average,
  and `last_60s_windowed_average_15min` in the final minute before :00/:15/:30/:45) and `cfbenchmarks_value_5hz`
  (BTC/ETH/SOL/XRP/DOGE); auth via Kalshi API key (https://docs.kalshi.com/websockets/cfbenchmarks-value ,
  https://docs.kalshi.com/asyncapi.yaml). REST `/cfbenchmarks/values` and `/cfbenchmarks/history/values` forward to
  `https://www.cfbenchmarks.com/api/v1/`, 50 read tokens per call, "available only to accounts with the appropriate
  entitlement" (https://docs.kalshi.com/cfbenchmarks/rest-passthrough). Whether a fresh retail key has the entitlement
  and whether the websocket channel needs it: **[U]** — test on day one. Kalshi API keys are free for KYC'd accounts
  (https://docs.kalshi.com/getting_started/api_keys ; https://www.allium.so/blog/kalshi-api-what-kyc-and-fees-mean-for-access/).
- Reviews/disputes on these series: no documented case found **[U]**. General process: Rule 6.3/7.1, 24-hour review
  clock (https://help.kalshi.com/en/articles/13823826-market-outcomes ,
  https://www.oddsshopper.com/articles/prediction-markets/kalshi-settlement-review-window). Kalshi now refunds fees on
  disputed settlements (gamingamerica.com, 2026).

---

## 2. Historical data for backtesting

- Kalshi live/historical partition **[S][M]**: `GET /historical/cutoff` → all four cutoffs = **2026-07-20T00:00:00Z**
  today. Live `/markets?status=settled`, `/markets/trades`, candlesticks cover only after the cutoff (~2 months);
  older data via `/historical/markets`, `/historical/markets/{ticker}/candlesticks`, `/historical/trades`
  (https://docs.kalshi.com/getting_started/historical_data). Observed quirks: `/historical/markets` **ignored
  `min_close_ts`/`max_close_ts`** (returned July-2026 markets for a Nov-2025 query) but honours `event_ticker`;
  `/historical/trades?ticker=` returned 0 rows for a July-2026 KXINXU market that has 44 one-minute candles — trade
  history may be thin or missing in the archive **[U]**.
- Candlesticks **[M]**: 1-minute candles exist for hourly BTC markets (60 per market) with yes_bid/yes_ask OHLC,
  volume and OI — enough for a bid/ask replay, not depth. Periods 1/60/1440 min. Batch endpoint exists.
- Order-book history: **not offered by Kalshi**. Vendors: DepthFeed (full-depth polls, 100 levels/side, adaptive
  interval, Parquet; https://depthfeed.com/resources/kalshi-api-guide , https://kalshibacktesting.com/),
  cryptostruct (KXBTC15M tick trades + L2 books Feb–Sep 2026, 66.8 GB;
  https://cryptostruct.com/prediction-markets/kalshi-btc-15m ; KXBTCD page exists), Lychee Data (36 GB+ trades/books),
  Predexon (tick-level order-book Parquet), pmxt free hourly order-book snapshots (https://archive.pmxt.dev/Kalshi),
  Jon Becker's open dataset (https://github.com/jon-becker/prediction-market-analysis). Depth for KXINXU/KXBTC hourly
  L2 specifically: only DepthFeed/Predexon/cryptostruct claim it, back to early/mid 2026 at best **[U on exact start dates]**.
- Practical implication: Phase 0's own recorder is the only way to get *depth* for these series; start it immediately,
  and buy one vendor month for a cross-check.

---

## 3. Options-implied digital pricing (S1)

- Stevens FSC "Event Contract Mispricing via Options-Implied Probabilities": page is 404; no SSRN/arXiv/archive mirror
  reachable; only the search-engine summary survives (SPXW chain + Kalshi books 2022–2024, **year-end S&P buckets**,
  Breeden–Litzenberger vs GBM/ATM-IV, B–L "strongest"). It is a student project on **year-end** contracts, not hourlies,
  with no published returns, fees or sample sizes. **Treat as anecdote, not evidence** **[U]**.
- Related academic work actually on short-horizon binaries: MSc thesis "btc-prediction-market-efficiency" (Polymarket
  19,649 + Kalshi 3,135 KXBTC contracts + Deribit DVOL, Mar 2024–Jun 2026; B-S d2 benchmark; results not published in
  README) https://github.com/giannandreadestefano/btc-prediction-market-efficiency ; "Decomposing Crowd Wisdom"
  (353 M trades; horizon/size-dependent calibration) https://arxiv.org/abs/2602.19520 ; Bartlett & O'Hara "Adverse
  Selection in Prediction Markets: Evidence from Kalshi" (41.6 M trades) https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6615739 .
- Digital replication: P(S_T > K) = −∂C/∂K ≈ (C(K−h) − C(K+h))/2h. Error terms: (i) strike discretisation,
  (ii) bid/ask noise on each leg divided by 2h, (iii) skew term (digital = call-spread limit + vega × dσ/dK).
  Todorov & Zhang (2024) quantify B–L truncation/Riemann error on SPX with 5-pt strikes at ~2% (truncation) and
  discuss the coarse-grid discretisation (`todorov_intraday.txt`).
- **SPY vs SPX for an hourly horizon** **[M analytic]**: at S = 7650 and 12–25% annualised vol, the 1-hour σ is 23–47
  index points and the ATM digital slope is 0.8–1.8 ¢ per index point. Consequences:
  - 1 bp of SPY/SPX basis error (0.77 pt) = 0.6–1.3 ¢; the SPY×10 ratio wanders 9.95–10.05 across the quarter
    (dividend accrual, expense ratio, ETF premium) and "a few tenths of a point intraday"
    (https://spxtospy.com/articles/spx-to-spy-conversion). Calibrating the ratio live off the index quote is necessary,
    and the residual intraday noise of 1–3 bp already eats 1–4 ¢ of a 3–8 ¢ edge.
  - SPY strikes are $1 = **10 index points**; Kalshi strikes are 5 points. h = 10 pts moves the digital by 8–18 ¢ at
    ATM, so butterflies straddling a Kalshi strike are impossible; you must fit a smile and differentiate it. SPX/XSP
    (5-pt / 0.5-pt strikes, cash, PM-settled to the official close) is the natural instrument; the plan's "upgrade to an
    SPX feed only if needed" should be reversed to "SPX from day one, SPY only as a sanity check". Webull SDK does not
    expose index options (plan §1.5), so this is an external data cost.
  - Horizon mismatch: 0DTE options expire 16:00 and (SPXW PM) settle to the **official close**; Kalshi 4pm settles to a
    16:00:00 print (see §1.1). Same-day density must be time-scaled to 11:00…15:00 with the measured intraday profile
    (§0 item 4). Intraday U-shape literature: Andersen & Bollerslev 1997; Todorov & Zhang 2024 (option-based diurnal
    estimator, pattern varies by year) https://www.kellogg.northwestern.edu/faculty/todorov/htm/papers/odp.pdf ;
    0DTE rough-vol evidence https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6802138 .
  - Risk-neutral vs physical: hourly RN density carries a variance-risk premium; at 1-hour horizon the drift is
    irrelevant but the vol premium is not (RN vol > realised on average) — B–L gives a *conservative* tail probability.
    Measured realised vol from Kalshi 4pm→hourly prints, Jul–Sep 2026: 8.0% annualised (quiet regime); DVOL-style
    0DTE implieds usually sit above that.

---

## 4. Crypto hourly fair value (S2)

- Deribit public API **[M][S]**: reachable, no key. `public/get_instruments` (978 BTC options), `get_book_summary_by_currency`,
  `get_index_price`, `get_volatility_index_data` (DVOL, 35.3 on 2026-09-19). Rate limits: non-matching-engine requests
  cost 500 credits, refill 10,000/s (≈20 req/s sustained, burst 100); public calls rate-limited per IP without a
  published number (https://docs.deribit.com/articles/rate-limits ; https://support.deribit.com/hc/en-us/articles/25944617523357-Rate-Limits).
  Use the WebSocket subscriptions rather than REST polling.
- Term-structure problem: Deribit's nearest expiry is **daily at 08:00 UTC** (6.1 h away at 02:00 UTC; 30 h the next).
  There is no 1-hour option; the hourly density must be scaled from a 6–30 h smile (ATM mark IV 22–35% today, wide:
  BTC-19SEP26-81000-C bid 0.0040 / ask 0.0050 BTC ≈ 20% relative spread). Simpler and arguably better for 1 h:
  realised-vol (e.g. 1-s BRTI EWMA / HAR) with a jump adjustment, cross-checked to DVOL.
- Efficiency / competition evidence:
  - Polymarket 15-minute crypto: on 2026-01-07 Polymarket introduced dynamic taker fees (up to ≈3.15% on a 50 ¢
    contract) explicitly to kill latency arbitrage after wallets ran thousands of trades/month against lagging books
    ("a bot that turned $313 into $414k in one month") — Finance Magnates
    https://www.financemagnates.com/cryptocurrency/polymarket-introduces-dynamic-fees-to-curb-latency-arbitrage-in-short-term-crypto-markets/ ;
    taker delay cut 250 ms → 50 ms on 2026-08-17 (dev.to, buvei.com summaries). This is direct evidence that
    sub-minute crypto binaries are a latency game with professional bots on both venues.
  - Kalshi market makers: Susquehanna is the flagship designated MM (https://kalshi.com/blog/article/kalshi-kit-liquidity-sig-market-makers ,
    https://sig.com/predictions/); Jump Trading and Wintermute quote both venues (CNBC 2026-08-19; DeFi Rate). Kalshi runs
    a Designated Liquidity Provider auction program (https://help.kalshi.com/en/articles/15410219-liquidity-provider-program).
    The other side of a Picador take is usually one of these.
- Measured BTC hourly activity **[M]** (13 events, 2026-09-18 08:00–21:00 ET, `/markets/trades`): median **34,111
  contracts and 1,031 trades per hour**, 10–40 of 188 markets ever trade, ~26% of contracts print in the last 5
  minutes. Notional at risk ≈ $8.3k per hour on the one event I costed. Position limit $1 M per strike is not binding.

---

## 5. Live microstructure measurements (Kalshi public API, 2026-09-19 01:56–02:02 UTC)

### 5.1 KXBTC-26SEP1822 (closing 02:00Z), snapshot at T−2 min, top of book (yes bid / yes ask, contracts)
| range | bid | ask | volume |
|---|---|---|---|
| B81050 (81,000–81,099.99) | 0.00 | 0.06 | 1,964 |
| B81150 | 0.02 | 0.05 | 2,695 |
| B81250 | 0.20 | 0.23 | 6,397 |
| **B81350** | **0.47** | **0.48** | 4,635 |
| B81450 | 0.18 | 0.22 | 5,794 |
| B81550 | 0.02 | 0.05 | 973 |
| B81650…B82050 | 0.00 | 0.01 | 200–410 |
Depth on B81250 at that moment: yes bids 0.28×1,500, 0.13×45, 0.10×100; yes asks (from no bids) 0.23×? … 0.60×105,
0.63×30 etc. (`books_1822_raw.json`). Sum of range asks = 1.25, sum of range bids = 0.89 → **no executable ladder
violation** after 2 ¢/leg; the ATM spread is 1 ¢, neighbours 3–4 ¢, far tails 0/1 ¢ (the "1 ¢ bid vs 8 ¢ ask" tail
shape of the plan is not there two minutes out).

### 5.2 KXBTC-26SEP1823 (opened 02:00Z), snapshot 68 s after open
Only 9 ranges quoted two-sided: B80950 0.04×301 / 0.09×130; B81050 0.08×200 / 0.13×100; B81150 0.15×101 / 0.21×300;
B81250 0.18×100 / 0.19×4.81; B81350 0.15×161 / 0.19×100; B81450 0.09×1 / 0.12×100; B81550 0.07×1 / 0.08×100;
B81650 0.03×1 / 0.05×125; B81750 0.00 / 0.04×30. Every other range and both tails showed a lone 0.96×12 ask (a
placeholder quote). Sum of asks across ranges 55.01 (meaningless because of the placeholders), sum of bids 0.79.
Spreads at open: 1–6 ¢ near the money; the first hour's liquidity is thin (≤300 contracts at touch) and arrives late.
No monotonicity or range-vs-threshold inconsistency was executable after fees in either snapshot **[M]**.

### 5.3 KXINXU Monday 10:00 (weekend book)
All 200 open markets carry quotes: e.g. T7769.9999 0.00/0.46, T7754.9999 0.06/0.42, T7724.9999 0.07/0.51 — 30–45 ¢
wide, zero volume. Weekend/overnight index books are not tradeable; do not count them as S1 opportunities.

### 5.4 Near-expiry taker markouts (evidence for S4 and adverse selection)
BTC, 13 hourly events, taker price band × seconds-to-expiry, PnL per contract after 2 ¢ fee (win-rate − avg price − 0.02):

| band | 0–120 s | 120–300 s | 300–900 s | 900–3600 s |
|---|---|---|---|---|
| 90–98 ¢ | −2.6 ¢ (n=4,505) | −8.1 ¢ (3,524) | +3.7 ¢ (10,367) | −0.2 ¢ (25,139) |
| 80–90 ¢ | −10.3 ¢ (3,454) | −1.8 ¢ (1,915) | +4.4 ¢ (7,043) | −10.5 ¢ (28,078) |
| 60–80 ¢ | −5.0 ¢ (5,181) | −8.2 ¢ (7,450) | −7.1 ¢ (14,610) | +11.4 ¢ (15,848) |
| 40–60 ¢ | −1.6 ¢ (4,717) | −11.8 ¢ (11,220) | +2.1 ¢ (14,978) | +24.8 ¢ (1,333) |
| 2–10 ¢ | **+9.0 ¢** (4,721) | **+6.9 ¢** (2,836) | −5.5 ¢ (7,338) | −6.2 ¢ (23,055) |

KXINXU-26SEP18H1600 (top-10 strikes, 377k contracts; 50% of volume in the last 30 min, 10% in the last 5 min):
takers at 90–98 ¢ in the last 120 s: 74% win at 91.6 ¢ → **−19.5 ¢**; 80–90 ¢ takers at 120–300 s: 21,119 contracts,
3.4% win → −80 ¢ (the index settled at 7650.12 against the 7649.9999 strike, 0.12 pt inside). One day, so these are
illustrations not estimates — but they show (a) last-minute "sure thing" buyers were the losers, (b) a single boundary
event can dominate a day, (c) near-expiry price ≠ probability.

---

## 6. Sizing and risk under fees

- Kelly for a YES bought at ask a with fee c (cost k = a + 0.02) and true p: f* = (p − k)/(1 − k). At a = 0.50 and 3/5/8 ¢
  net edge f* = 6.3% / 10.4% / 16.7% of bankroll (quarter: 1.6% / 2.6% / 4.2%). At a = 0.90 with 3 ¢ edge f* = 37.5%
  (quarter 9.4%) — the high-price regime is where Kelly over-sizes on tiny edge estimates.
- Estimation error: Meister (arXiv 2412.14144) — growth loss is *first-order* in the probability misjudgement and
  second-order in the fraction error; over-estimating p by 5–10 pts at 60–80 % turns an optimal bet into a ruinous one.
  Practical rule: shrink the model edge toward zero (e.g. Bayesian shrinkage with the Phase-0 calibration slope) before
  Kelly, and cap fraction ≤ ¼ of the shrunk Kelly. https://arxiv.org/abs/2412.14144 ;
  https://matthewdowney.github.io/uncertainty-kelly-criterion-optimal-bet-size.html .
- Sample sizes (per-trade Sharpe = edge/√(p(1−p)) at price ≈ 0.5): 3 ¢ → 0.06 → **1,100 trades** for t = 2 (2,475 for
  t = 3); 5 ¢ → 392; 8 ¢ → 150; at 90 ¢ with 3 ¢ → 211; at 20 ¢ with 5 ¢ → 315. The plan's N ≥ 30/60 gates cannot
  distinguish a 3 ¢ edge from zero.
- Drawdown Monte Carlo (2,000 trades, quarter-Kelly on the *estimated* edge): 3 ¢ edge, independent → median max DD 34%
  (p95 53%); 3 ¢ edge with 3 perfectly correlated legs per event → 61% (81%); 5 ¢ → 74% (93%); 8 ¢ → 83% (96%);
  estimated 5 ¢ / true 0 → 96%; estimated 5 ¢ / true −2 ¢ → 99%. Quarter-Kelly on correlated legs is not quarter-Kelly.
- Correlation structure: (i) strikes in one event are functions of one terminal value — size by *event* using the
  worst-case scenario PnL over the strike grid (long YES above K1 and long NO below K2 is a range bet; long YES at K1
  and K2 is 2× one bet); (ii) consecutive hours have ≈ uncorrelated returns but **perfectly correlated model error**
  (vol/profile misestimate, feed basis) — cap total exposure per *sign of vol view* (net long-tail vs net short-tail)
  across the day, and cap per day; (iii) the 60-s-average settlement makes adjacent BTC hourly ranges disjoint but the
  *daily* and *weekly* markets in the same series share the underlying — treat the whole series as one risk bucket.

---

## 7. Adverse selection: who is on the other side

- Makers: SIG (designated flagship MM), Jump, Wintermute, plus the DLP program (see §4). Their quotes are what Picador
  lifts; their edge is speed and the same options/BRTI feeds.
- Bartlett & O'Hara (2026, 41.6 M Kalshi trades): informed price impact (Kyle λ, Glosten–Harris) is higher in
  "single-name" than "broad-based" markets, VPIN-style one-sided flow predicts maker losses only in single-name
  markets; makers earn ~2× per contract there because retail systematically over-buys YES in markets that settle NO.
  Index/crypto hourlies are "broad-based" in their taxonomy (the full paper is paywalled on SSRN; the category
  definitions could not be confirmed **[U]**) https://law.stanford.edu/publications/adverse-selection-in-prediction-markets-evidence-from-kalshi/ .
- Measured flow (§5.4): near expiry the aggressive buyers of 80–98 ¢ contracts lost after fees on both BTC and S&P,
  while aggressive buyers of the 2–10 ¢ side in the last 2 minutes won. That is the signature of informed sellers
  (makers with the live BRTI/index feed) against convergence-chasing takers. Implication for S4: taking the ask at
  90–97 ¢ is negative-expectation unless the bot's feed/clock is better than the makers', which through Webull's REST
  → Kalshi path it is not. The profitable side, if any, is *providing* the 93–97 ¢ offers (a maker strategy the plan
  already rules out on Webull fees) or buying the mispriced cheap side only when the 60-s running average makes the
  outcome mechanically certain.
- Overnight/weekend index books are 30–45 ¢ wide (§5.3); an uninformed fill there is pure adverse selection.

---

## 8. Kalshi's "modified Source Agency" for index markets — what feed?

- Facts **[S]**: Nov-13-2024 CFTC 40.6 filings (`rules1113248693/95/01.pdf`, extracted) changed the INX and NASDAQ100
  Underlying from "day close price for $INX according to Quandl Data" to "price of the S&P 500 Index <on/before>
  <time>" and the Source Agency from "Quandl Data" to **"Kalshi"**, effective 2024-11-28. The feed identity is in
  "Appendix C (Confidential) – Source Agency". Access links (Google Finance; Nasdaq's own page for NDX) are
  "non-binding, for convenience only". S&P 500 / Nasdaq-100 trademark notices appear but no licence terms are public **[U]**.
- Inference from data **[M]**: values carry 2 decimals and are captured ~1 min after `<time>`, settled ~2 min after; the
  4pm value is 0.02–0.5 pt away from the official close in three checked days. That behaviour matches a real-time
  consolidated index feed sampled at HH:00:00 (not Google Finance's delayed page and not the S&P DJI official close).
- What the bot must do: (a) use a real-time SPX/NDX index quote (Webull L1 index quote, or Cboe/Nasdaq feed) as the
  reference for S1/S4; (b) never treat the official close, ES futures, or SPY×ratio as the settlement value; (c) refuse
  S4 when |index − K| < max(0.5 pt, 2σ_feed) — 21% of settlements land within 0.5 pt of a boundary; (d) log Kalshi's
  `expiration_value` against your own feed every hour to estimate the feed basis and its distribution.

---

## 9. What could not be verified
- Stevens FSC study contents beyond the search summary (page 404, no mirror).
- Full text of Bartlett & O'Hara (SSRN 403) — category definitions and time-to-expiry results.
- Whether a plain retail Kalshi API key carries the CF Benchmarks entitlement (REST passthrough and/or websocket).
- Depth/start dates of vendor L2 archives for KXINXU/KXBTC specifically.
- Any documented reversal of an index/crypto hourly settlement.
- The exact Nasdaq-100 hourly series currently listed (NASDAQ100I had no open markets; KXNASDAQ100U exists).
- Kalshi `/historical/markets` date filters (appeared to be ignored) and `/historical/trades` completeness.

## 10. Source list (in addition to URLs inline)
- Kalshi API: https://docs.kalshi.com/getting_started/historical_data · https://docs.kalshi.com/getting_started/rate_limits ·
  https://docs.kalshi.com/websockets/cfbenchmarks-value · https://docs.kalshi.com/cfbenchmarks/rest-passthrough ·
  https://docs.kalshi.com/asyncapi.yaml
- Contract terms: https://assets.kalshi.com/contract_terms/INX.pdf · …/BTC.pdf · …/NASDAQ100I.pdf · …/ETH.pdf ·
  CFTC filings https://www.cftc.gov/sites/default/files/filings/orgrules/24/11/rules1113248693.pdf (redline), …8695.pdf, …8701.pdf
- CF Benchmarks: https://docs.cfbenchmarks.com/CME%20CF%20Real%20Time%20Indices%20Methodology.pdf · https://docs.cfbenchmarks.com/api/websocket/value/
- Deribit: https://docs.deribit.com/articles/rate-limits
- Polymarket fee episode: Finance Magnates 2026-01-07 (link above); Unchained; dev.to 50 ms note
- Academic: Todorov & Zhang 2024 (Kellogg PDF); Meister arXiv 2412.14144; arXiv 2602.19520; arXiv 2608.00666 (Polymarket
  executable arbitrage: median violation life 8–16 s); Bartlett & O'Hara SSRN 6615739; Alfeus & Mokone SSRN 6802138
- Vendors: depthfeed.com, cryptostruct.com, lycheedata.com, predexon.com, archive.pmxt.dev
