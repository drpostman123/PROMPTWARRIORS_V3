# CONVEX sleeve — buildable specification (v0.1, 2026-09-19)

Inputs: `docs/MASTER_SYSTEM_CHARTER.md` (sleeve model, treasury rules, build order),
`docs/PICADOR_ORCHESTRATION.md` (head/arm architecture, intent schema, ownership lock),
`docs/research/portfolio.md` §2 (evidence, structure set, kill rule), §3.2 and §6 (tax),
§4 (broker fit), `docs/research/brokers.md` §2–3 (tastytrade, Public fact sheets).
Tags: **[V]** verified from a primary source (this session or the evidence brief), **[T]** third-party
or SDK-documented only, **[U]** unverified → listed as a test in §10.

Charter position of this sleeve: priority 2, "small options sleeve that hunts asymmetric,
defined-risk 'rocket' payoffs. Its profits are swept into the grind sleeves and, above a
threshold, into the core." Character: "small, defined-risk, expects to lose most months."
Nothing in this spec may increase the sleeve's own budget from its own profits.

---

## 0. Invariants (the head enforces these; the arm cannot override them)

| # | Invariant | Value | Source |
|---|---|---|---|
| I1 | Premium-at-risk opened in any rolling 365-day window | ≤ 3.0 % of `E_ref` | portfolio.md §2.3 |
| I2 | Premium-at-risk of all open positions sharing one expiry cycle | ≤ 1.0 % of `E_ref` | portfolio.md §2.3 |
| I3 | Sleeve delta-equivalent notional, Σ|Δ|·multiplier·spot | ≤ 15 % of `E_ref` | portfolio.md §2.3 |
| I4 | Every position is defined-risk at entry (max loss known and ≤ premium-at-risk booked) | always | charter "defined-risk" |
| I5 | Structures permitted | S1, S2, S3 only (§1) | portfolio.md §2.3 |
| I6 | Budget refills | Jan 1 annual line, released in 4 quarterly tranches; never from sleeve profits | charter "CONVEX refills only from its own budget line each quarter" |
| I7 | Realized gains | swept out on trade close (§5.4) | charter profit-sweep rule |
| I8 | Drawdown ladder | −5 % halve, −10 % pause, −15 % flat except CORE floor | charter treasury rules |
| I9 | Ticker partition | no ticker/underlying traded by CONVEX is traded by any other arm (ownership lock keyed on underlying) | portfolio.md §6 wash-sale rule; orchestration §3.1 |
| I10 | Order type | limit orders only; multi-leg as one order where the venue supports it | §4 |

`E_ref` = total equity across all arms at the previous day's close from the head's journal of
record (orchestration §3.1), **not** the CONVEX sub-account balance.

---

## 1. Permitted structures

### 1.1 S1 — Pre-earnings ATM straddle (the only structure with published positive expectancy)

Evidence: Gao, Xing, Zhang (JFQA 2018): buy ATM straddle 3 days before the earnings
announcement, close on the announcement date, +3.34 % mean; strongest for smaller, higher-vol,
higher-kurtosis, less-liquid names; generic single-stock straddles lose money [V abstract, via
portfolio.md §2.2]. The return is the pre-announcement IV ramp plus pre-announcement drift; the
position is **closed before the release**, so IV crush is never taken. "Hold-through" is a
different trade with weaker evidence (student replication negative on S&P 500 names [T, low
quality]) and is allowed in **shadow only** (§7).

**Universe filter (evaluated nightly, T-5 to T-3):**

| Filter | Rule | Why |
|---|---|---|
| Listing | US common stock or ADR with standard listed options, price 10 ≤ S ≤ 150 | affordable 1-lot straddle; sub-$10 spreads too wide |
| Size | market cap 300 M ≤ cap ≤ 10 B | GXZ: effect lives in smaller names |
| Vol | 30-day IV index ≥ 45 % **or** HV30 ≥ 40 % | GXZ: higher-vol names |
| Kurtosis proxy | mean |earnings-day move| over last 8 reports ≥ 6 % (from `historic-corporate-events/earnings-reports` + daily bars) | GXZ: higher-kurtosis names |
| Liquidity | front-expiry ATM call and put each: OI ≥ 200, 20-day avg volume ≥ 100; **straddle (ask−bid)/mid ≤ 5 %** at the entry quote | GXZ liquidity gate quoted in portfolio.md §2.3 |
| Date certainty | earnings date **and** BMO/AMC flag agree across two sources (§3.1); `hour ∈ {bmo, amc}`; `dmh`/unknown → skip | exit timing depends on it |
| Expiry | first monthly or weekly expiry with DTE ≥ 7 **after** the release date; never the expiry that dies on the release week's Friday if DTE < 5 at entry | avoid pure gamma bleed / pin |
| Exclusions | ticker in any other arm's ownership set; ticker on the CORE universe; hard-to-borrow/halted flags; pending M&A | I9; event risk not earnings |

**Timing (D = release date from the calendar; trading days):**

| Case | Entry | Exit (before release) |
|---|---|---|
| AMC on D | at 15:40–15:55 ET on D−3 | 15:30–15:55 ET on D |
| BMO on D | at 15:40–15:55 ET on D−4 (i.e., 3 sessions before the last pre-release session D−1) | 15:30–15:55 ET on D−1 |

GXZ's "3 days before" is implemented as three full sessions of holding before the last
pre-release close. The T-3/T-4 convention is a parameter (`s1.entry_offset_sessions`, default 3)
and is one of the paper-plan A/B tests (§7).

**Sizing:**
```
per_trade_cap   = min(0.25% * E_ref, cycle_room, rolling_room)      # 4 names fill one 1 % cycle
n_contracts     = floor(per_trade_cap / (100 * straddle_ask))
if n_contracts == 0: skip                                           # equity-band behaviour, §8
max concurrent S1 names = 4; max 1 name per GICS sector per cycle; max 2 per release day
```

**Entry mechanics:** one multi-leg order (call + put, same strike, same expiry, `BUY/OPEN` both
legs), limit at the straddle mid; ladder per §4.3; strike = the listed strike nearest spot at
15:40 ET (if two equidistant, take the one whose straddle has the narrower spread).

**Exit rules (first that fires):**
1. Time exit in the pre-release window above (mandatory; the arm forces a marketable limit at
   mid − 1 tick, then mid − 2 ticks, at 15:50 and 15:55 if unfilled; never leave S1 open through
   a release — if still unfilled at 15:58 send a limit at the bid).
2. Calendar changed: nightly re-check shows the date moved or the BMO/AMC flag flipped →
   close at the next open's mid ladder. Log as `exit_reason=calendar_change`.
3. Straddle mid ≤ 60 % of entry price before exit day (IV collapse, e.g., pre-announcement
   guidance) → close. Defined-risk anyway; this saves the remainder.
4. Halt/corporate action on the underlying → close when trading resumes.

No delta re-hedging, no re-striking (GXZ does neither; keep the trade measurable).

**Pseudocode**
```python
def s1_scan(today):
    cal = earnings_calendar(today + 3, today + 6)          # two-source merged, §3.1
    for ev in cal:
        if ev.hour not in {"bmo", "amc"}: continue
        entry_day = sessions_before(ev.release_session_last_close, 3)
        if entry_day != today: continue
        if not passes_universe(ev.symbol): continue
        chain = option_chain(ev.symbol, expiry=first_expiry_after(ev.date, min_dte=7))
        K = nearest_strike(chain, spot(ev.symbol))
        call, put = chain.call(K), chain.put(K)
        mid = call.mid + put.mid; ask = call.ask + put.ask; bid = call.bid + put.bid
        if (ask - bid) / mid > 0.05: continue
        cap = budget.room(structure="S1", cycle=expiry_cycle(chain.expiry), per_trade=0.0025)
        n = floor(cap / (100 * ask))
        if n == 0: continue
        intent = Intent(structure="S1", legs=[Leg(call, BUY, OPEN, 1), Leg(put, BUY, OPEN, 1)],
                        qty=n, limit=mid, max_debit=ask, exit_at=ev.exit_window,
                        premium_at_risk=n * 100 * ask, tif="DAY")
        head.route(intent)                                   # ownership lock + budget reserve inside
```

### 1.2 S2 — Trend-confirmed long-dated call spread / call ratio backspread

Evidence: no peer-reviewed proof that trend-filtered long-dated calls beat the underlying
leverage-adjusted **[U]**; "the rocket is the trend, the option is the financing" (portfolio.md
§2.2). Constantinides–Jackwerth–Savov: OTM legs underperform ATM leverage-adjusted, so the long
strike stays ≤ 15 % OTM [V abstract]. Spreads/backspreads pay less VRP than outright calls.
S2 is therefore a **budgeted leveraged expression of an already-evidenced CORE signal**, not an
independent edge; it is judged on process and on the fat-tail rule (§7), not on N.

**Trigger (all required, checked at the CORE daily signal run):**
1. CORE's 2-of-3 trend signal on the underlying is long (12-month return > 0, 3-month return
   > 0, price > 10-month SMA; ≥ 2 true) **and** the trend-strength gate from portfolio.md §3.3
   (|3-month return| / 12-month vol ≥ 0.5) is met.
2. IV rank < 30: `tw-implied-volatility-index-rank` from tastytrade `/market-metrics` **[T,
   SDK-documented]**, cross-checked against the head's own 252-day rank of the 30-day ATM IV
   built from the Public chain snapshots (§3.2). Both < 30 → go; either missing → skip.
3. No S2 position already open on the same underlying; S2 open premium-at-risk < 1.5 % of `E_ref`
   (S2 sub-cap; half the annual line).
4. Underlying ∈ S2 universe: **XSP** (S&P 500 mini, 1256), **XND** (Nasdaq-100 micro, 1256 **[T]**),
   **MRUT** (Russell 2000 mini, 1256 **[T]**) first; ETF options (GLD/IAU, TLT/IEF, EEM, VEA) only
   when the ETF is **not** in the CORE ticker set and no CORE loss sale in a correlated ETF is
   inside the 30-day wash window (§9). Single stocks: never (Boyer–Vorkink skew penalty).

**Structure choice (deterministic):**

| Condition | Structure | Strikes | Expiry |
|---|---|---|---|
| IV rank < 30 and skew (25Δ put IV − 25Δ call IV) ≥ 0 | **Debit call spread**: buy call Δ 0.40–0.50, sell call at ≤ 15 % OTM | long ≈ 2–5 % OTM, short ≤ +15 % | monthly/LEAPS with 270 ≤ DTE ≤ 540 (nearest to 365) |
| IV rank < 20 and call skew flat/negative (calls cheap) | **Call ratio backspread 1×2**: sell 1 call Δ ≈ 0.50, buy 2 calls Δ 0.25–0.30, same expiry | short ≈ ATM, longs ≤ 15 % OTM | same |

**Sizing (both):**
```
net_debit         ≤ 0.30 % * E_ref                       # portfolio.md §2.3
premium_at_risk   = debit_spread: net_debit * 100 * n
                  = backspread : ((K_long - K_short) * 100 - net_credit_or_plus_debit) * n   # max loss at K_long at expiry
premium_at_risk   ≤ 0.50 % * E_ref  and  ≤ cycle_room (I2) and ≤ rolling_room (I1)
n = floor(min(0.003 * E_ref / (100 * net_debit_ask), premium_at_risk_cap / max_loss_per_unit))
```

**Exit rules (first that fires):**
1. Trend flips: 2-of-3 fails on the underlying at any daily run → close the whole structure next
   session at the mid ladder.
2. Take-profit: structure value ≥ 3× net debit (or backspread longs ≥ 3× the structure's booked
   premium-at-risk) → close **half** (round up) and let the rest run to rule 3/4; when the
   remaining half again reaches 3× the original debit → close all. Realized gains sweep (§5.4).
3. Debit spread at ≥ 70 % of max width → close all (remaining convexity is gone).
4. DTE ≤ 60 → close all (gamma, pin, and for American ETF options assignment).
5. ETF options only: short call ITM **and** ex-dividend date within 3 sessions → close all.
6. Drawdown ladder −10 % pause: no new S2; open S2 keep running rules 1–5. −15 %: close all.
No stop-loss below the debit: max loss is the budgeted premium (I4).

**Pseudocode**
```python
def s2_scan(today, core_signals):
    for u in S2_UNIVERSE:
        sig = core_signals[u]
        if not (sig.two_of_three_long and sig.strength >= 0.5): continue
        ivr = min_or_none(metrics(u).tw_iv_rank, head.own_iv_rank(u))
        if ivr is None or ivr >= 30: continue
        if book.open(structure="S2", underlying=u) or book.open_par("S2") >= 0.015 * E_ref: continue
        exp = pick_expiry(u, dte_min=270, dte_max=540, target=365)
        chain = option_chain(u, exp)
        if ivr < 20 and call_skew(chain) <= 0:
            K_s = strike_by_delta(chain, 0.50); K_l = strike_by_delta(chain, 0.275, max_otm=0.15)
            legs = [Leg(chain.call(K_s), SELL, OPEN, 1), Leg(chain.call(K_l), BUY, OPEN, 2)]
            max_loss_unit = (K_l - K_s) * 100 + net_debit(legs) * 100
        else:
            K_l = strike_by_delta(chain, 0.45); K_s = min(strike_by_delta(chain, 0.20), spot(u) * 1.15)
            legs = [Leg(chain.call(K_l), BUY, OPEN, 1), Leg(chain.call(K_s), SELL, OPEN, 1)]
            max_loss_unit = net_debit(legs) * 100
        n = size_s2(E_ref, net_debit_ask(legs), max_loss_unit, budget)
        if n == 0: continue
        head.route(Intent(structure="S2", legs=legs, qty=n, limit=net_mid(legs), max_debit=net_debit_ask(legs),
                          premium_at_risk=n * max_loss_unit, tif="DAY", exit_rules=S2_EXITS))
```

### 1.3 S3 — Crash convexity: VIX call spread or XSP put spread, only when VIX < 18

Evidence: outright protective puts destroy value (Israelov PPUT −1.8 %/yr alpha; AQR Put
−6.4 %/yr at 10 % vol) [V]; the budgeted, level-conditioned VIX-call overlay (Cboe VXTH: one-month
30-delta VIX calls, weight set at each roll by the forward VIX future level) is the template
[V methodology text; the weight table itself is an image in the PDF — schedule 0 % / 1 % / 0.5 % /
0 % by F ≤ 15 / 15–30 / 30–50 / > 50 is **[T]**]. The brief's constraint, adopted here: enter
only when spot VIX < 18, size 0.25–0.5 % (portfolio.md §2.3). Divergence from VXTH (which buys
0 % below 15) is a paper-plan test (§7, §10).

**Trigger:** on the S3 roll day (the session after the monthly VIX expiry Wednesday for VIX;
the Monday after the third Friday for XSP), spot VIX (Cboe index quote via Public `INDEX` quote
or DXLink `VIX`) at 10:00 ET < 18 **and** no S3 position with DTE > 21 already open.

**Structure and sizing:**

| VIX at 10:00 | Structure | Size (premium-at-risk) |
|---|---|---|
| < 14 | VIX call spread: buy Δ ≈ 0.30 call, sell call at the greater of (long strike + 15 pts, 2× long strike); DTE 30–60 (front or second monthly) | 0.50 % of `E_ref` if S3 YTD spend < 0.5 %, else 0.25 % |
| 14–17.99 | same VIX call spread, or if the VIX spread mid > 45 % of width, XSP put spread: buy put 5 % OTM, sell put 15 % OTM, DTE 45–75 | 0.25 % of `E_ref` |
| ≥ 18 | no new position; open positions run their exits | — |

S3 annual sub-cap 1.0 % of `E_ref` (so 12 monthly 0.25 % entries cannot happen; typical is
4–6 entries a year because VIX < 18 is regime-dependent and DTE-gated).
`n = floor(size / (100 * net_debit_ask))`; if 0 → §8.

**Exit rules (first that fires):**
1. Structure value ≥ 3× net debit → close all (this is the "rocket"; realize and sweep).
2. VIX spread at ≥ 60 % of width, or XSP put spread at ≥ 60 % of width → close all.
3. DTE ≤ 7 → close (VIX options settle AM on Wednesday via SOQ, XSP PM-settled cash; neither can
   assign, but the SOQ print is not tradable — exit before it).
4. Drawdown ladder: −10 % pause blocks new S3 entries only; open S3 stays (it is the crash
   hedge). −15 % "everything flat except CORE": **proposed charter amendment — S3 positions with
   unrealized gain are closed (gain swept), S3 positions with unrealized loss are kept to rule 3
   because their remaining value is already spent premium.** Until the charter is amended the
   head follows the charter literally (close all).

**Pseudocode**
```python
def s3_roll(today):
    if not is_s3_roll_day(today): return
    vix = spot_vix_at(today, "10:00")
    if vix >= 18 or book.open(structure="S3", min_dte=22): return
    size_pct = 0.005 if (vix < 14 and budget.ytd("S3") < 0.005 * E_ref) else 0.0025
    size = min(size_pct * E_ref, budget.room("S3"), 0.01 * E_ref - budget.ytd("S3"))
    vx_chain = option_chain("VIX", expiry=vix_monthly(dte_min=30, dte_max=60))
    K_l = strike_by_delta(vx_chain, 0.30); K_s = max(K_l + 15, 2 * K_l)
    legs = [Leg(vx_chain.call(K_l), BUY, OPEN, 1), Leg(vx_chain.call(K_s), SELL, OPEN, 1)]
    if vix >= 14 and net_mid(legs) > 0.45 * (K_s - K_l):
        xsp = option_chain("XSP", expiry=xsp_expiry(dte_min=45, dte_max=75))
        K_l = nearest_strike(xsp, spot("XSP") * 0.95); K_s = nearest_strike(xsp, spot("XSP") * 0.85)
        legs = [Leg(xsp.put(K_l), BUY, OPEN, 1), Leg(xsp.put(K_s), SELL, OPEN, 1)]
    n = floor(size / (100 * net_debit_ask(legs)))
    if n == 0: return
    head.route(Intent(structure="S3", legs=legs, qty=n, limit=net_mid(legs), max_debit=net_debit_ask(legs),
                      premium_at_risk=n * 100 * net_debit_ask(legs), tif="DAY", exit_rules=S3_EXITS))
```

---

## 2. Forbidden list

| Forbidden | Why (evidence / rule) |
|---|---|
| Short-dated (< 60 DTE) far-OTM (> 15 %) single-stock calls, any "cheap lottery" buying | Boyer–Vorkink (JF 2014): option returns fall steeply in ex-ante skew, 10–50 % **per week** spread between low- and high-skew single-stock options [V abstract] |
| Systematic 1-month protective puts / rolling put ladders | Israelov PPUT −1.8 %/yr alpha; AQR Put −6.4 %/yr, Sharpe −0.61, max DD −92 % [V] |
| Outright long puts as "insurance" at any tenor | same; the charter's crash hedge is S3 only, budgeted and level-gated |
| Any undefined-risk position (naked short options, short straddles/strangles, uncovered ratio spreads where the short side dominates) | charter: "defined-risk"; I4; a single tail event would consume years of budget |
| Premium selling as an income strategy | not a CONVEX character trade (negative skew); if ever wanted it is a different sleeve with its own evidence ladder (charter: "no sleeve inherits another's evidence") |
| 0DTE and same-week expiries for S2/S3 | pure gamma/theta bleed; no evidence; settlement gaps |
| FOMC/CPI-day long options | Lucca–Moench pre-FOMC drift disappeared after 2015 (Kurov et al. 2021) [V abstract]; portfolio.md: express macro-event views in the Kalshi grind |
| Hold-through-earnings straddles in live | only the pre-release close has published positive expectancy; hold-through replication negative [T]; allowed as **shadow** only |
| S1 on large caps (cap > 10 B) or on names failing the 5 % straddle-spread gate | GXZ effect concentrated in small/high-vol names; spreads eat the 3.34 % |
| Options on any ticker another arm trades (CORE ETFs, MACRO ETFs) | I9; §1091 wash sales apply to options to acquire substantially identical stock; ownership lock |
| Adding to a loser, rolling a loser out in time, "repairing" spreads | converts a budgeted loss into an unbudgeted one; violates I1/I2 accounting |
| Re-investing sleeve profits into more premium | charter sweep; portfolio.md §5.3 "the convex sleeve cannot compound its own leverage (the classic blow-up path)" |
| Market orders, and multi-leg structures legged in separately when the venue supports one order | §4; leg risk and slippage; Public multi-leg is limit-only anyway [V] |
| Trading without both earnings-date sources agreeing (S1) | wrong-day exit = uncontrolled IV crush |

---

## 3. Data

### 3.1 Earnings calendar (S1) — two sources, must agree

| Source | Call | Fields used | Status |
|---|---|---|---|
| **Finnhub** | `GET /calendar/earnings?from=YYYY-MM-DD&to=YYYY-MM-DD[&symbol=]` (python: `earnings_calendar(_from, to, symbol="", international=False)`) [V client README] | `date, symbol, hour ∈ {bmo, amc, dmh}, epsEstimate, revenueEstimate, quarter, year` [T] | free tier ~60 calls/min [T]; page 403 to fetchers → §10 |
| **tastytrade `/market-metrics`** | `GET /market-metrics?symbols=A,B,C` → `earnings.expected-report-date`, `earnings.time-of-day`, `earnings.actual-eps`, `earnings.estimated`; history: `GET /market-metrics/historic-corporate-events/earnings-reports/{symbol}` [V endpoint list; field names T via SDK] | date + BMO/AMC + 8-quarter history for the kurtosis proxy | production only (cert returns 502) [V] |
| Fallback | FMP `stable/earnings-calendar` and legacy `earning-calendar-confirmed` (confirmed vs projected flag) [T; pages 403 today] | confirmation status | §10 |

Merge rule: `(date, hour)` must match between Finnhub and tastytrade; a mismatch or a
`dmh`/null hour disqualifies the name. Re-check nightly while a position is open (exit rule 2).

### 3.2 IV rank (S2) and IV inputs (S1)

- Primary: tastytrade `/market-metrics` → `implied-volatility-index`,
  `tw-implied-volatility-index-rank`, `tos-implied-volatility-index-rank`,
  `implied-volatility-percentile`, `historical-volatility-30/60/90-day`, `liquidity-rating` [T,
  SDK field list; production only].
- Own cross-check: the head stores one daily snapshot per S2 underlying of the 30-day
  constant-maturity ATM IV (linear in variance between the two expiries bracketing 30 DTE, from
  the Public chain's `impliedVolatility`) and computes `rank = (iv − min252)/(max252 − min252)`.
  Until 252 snapshots exist the own rank is "missing" and S2 relies on the tastytrade rank alone
  **plus** a hard floor: skip if `implied-volatility-index` > `historical-volatility-30-day` × 1.3.
- VIX spot: Public quote with `type=INDEX` (symbol form **[U]**, §10) or DXLink `Quote` on `VIX`.

### 3.3 Chains and greeks per broker

| Need | Public | tastytrade |
|---|---|---|
| Expirations | `POST /userapigateway/marketdata/{acct}/option-expirations`, `instrument.type ∈ {EQUITY, UNDERLYING_SECURITY_FOR_INDEX_OPTION}` [V] | `GET /option-chains/{symbol}/nested` (equity + index OCC symbols) [V instruments doc] |
| Chain with quotes + greeks | `POST /userapigateway/marketdata/{acct}/option-chain` `{instrument, expirationDate}` → `calls[]/puts[]` with `bid, ask, last, bidSize, askSize, volume, openInterest, delta, gamma, theta, vega, rho, impliedVolatility, strikePrice, midPrice` (greeks added 2026-03-25) [V] | REST quotes via `/market-data` snapshots; live via DXLink |
| Greeks by OSI list | `POST …/option-greeks` (added 2025-11-24; page 403 today) [V changelog / U schema] | DXLink `Greeks` event: `price, volatility, delta, gamma, theta, rho, vega, time, sequence` [V dxFeed doc]; token `GET /api-quote-tokens` (24 h) → `wss://tasty-openapi-ws.dxfeed.com/realtime`; caps 5 sessions / 25 000 subscriptions [V] |
| Underlying bars (HV, trend) | `GET …/bars` [V endpoint list] | DXLink `Candle` (100 per session cap) |
| Polling budget | 10 req/s per account [V]; S1 nightly scan of ≤ 300 names × 1 chain call = 30 s | REST limits unpublished, bare 429 [V] |

Streaming greeks are used only for the I3 delta-notional monitor and exit triggers; entries use
the REST snapshot taken ≤ 5 s before order submission.

---

## 4. Execution

### 4.1 Broker arm per structure

| Structure | Primary arm | Why | Backup |
|---|---|---|---|
| S1 equity straddles | **Public** (`order/multileg`) | $0 commission + API rebate $0.06/contract [V]; one multi-leg limit order | tastytrade ($1 open / $0 close + $0.10 clearing, $10 cap/leg) |
| S2 XSP / XND / MRUT spreads | **Public** | XSP exchange fee $0.00 for ≤ 9 contracts per execution, $0.07 above [V index-fee page; conflicts with fee-schedule PDF "$0.35/contract" → §10] | tastytrade (XSP $1 + $0.10 + $0.00/0.07) |
| S2 ETF spreads | Public | $0 + rebate | tastytrade |
| S3 VIX call spreads | **tastytrade** (VIX index fee $0.35; DXLink greeks; VX futures quote for the VXTH-style forward level) or Public (VIX $0.10–0.45 pass-through) — decide by preflight `estimatedCommission` vs dry-run `fees` at build time | both are cheap; tastytrade gives the /VX front-month price natively | Webull ($0.50/contract index options) |
| S3 XSP put spreads | Public | same as S2 | tastytrade |
| SPX (≥ $500k equity only, 1 SPX = 10 XSP) | tastytrade for large legs | portfolio.md §4.2 | Public |

Each arm keeps its own credentials, rate limiter and paper mode (orchestration §3.2). The
ownership lock is keyed on the **underlying**: while CONVEX holds XSP the MACRO arm may not
trade XSP/SPX options, and CORE never trades a ticker CONVEX has options on.

### 4.2 Order formats

**Public — one multi-leg limit order** (multi-leg is LIMIT-only [V]):
```json
POST https://api.public.com/userapigateway/trading/{ACCOUNT_ID}/order/multileg
{
  "orderId": "<uuid4, idempotency key>",
  "quantity": 2,
  "type": "LIMIT",
  "limitPrice": "3.45",
  "expiration": { "timeInForce": "DAY" },
  "legs": [
    { "instrument": { "symbol": "ABCD261120C00040000", "type": "OPTION" }, "side": "BUY", "openCloseIndicator": "OPEN", "ratioQuantity": 1 },
    { "instrument": { "symbol": "ABCD261120P00040000", "type": "OPTION" }, "side": "BUY", "openCloseIndicator": "OPEN", "ratioQuantity": 1 }
  ]
}
```
Preflight first: `POST …/preflight/multi-leg` (same body + `validateOrder: true`,
`useMargin: false`) → `estimatedCost, buyingPowerRequirement, estimatedCommission,
marginRequirement` [V]; reject the intent if `estimatedCost` > `max_debit × quantity × 100` or if
`estimatedCommission` differs from the fee table by > $0.10/contract (fee-table drift alarm).
Index legs use the same `OPTION` type with the index OCC root (`XSP…`) **[U → §10]**.
Bracket/OCO exists for orders since 2026-09-10 (`orderClass`) [V] but CONVEX exits are driven
by the head's rules, not resting brackets, because exits depend on the calendar and greeks.

**tastytrade — one order with legs** (dry-run, then submit):
```json
POST /accounts/{ACCOUNT}/orders/dry-run   then   POST /accounts/{ACCOUNT}/orders
{
  "time-in-force": "Day",
  "order-type": "Limit",
  "price": "1.35",
  "price-effect": "Debit",
  "external-identifier": "<intent_id>",
  "legs": [
    { "instrument-type": "Equity Option", "symbol": "VIX   261118C00020000", "quantity": 1, "action": "Buy to Open" },
    { "instrument-type": "Equity Option", "symbol": "VIX   261118C00035000", "quantity": 1, "action": "Sell to Open" }
  ]
}
```
OCC symbol = root padded to 6 chars + yymmdd + C/P + strike×1000 (8 digits) [V]. No idempotency
header: set `external-identifier`; on ambiguous outcome `GET /accounts/{acct}/orders` before
resubmitting [V]. Complex orders (OTOCO/OCO) go to `/complex-orders` and are not used by CONVEX.

### 4.3 Fill-or-cancel and slippage rules (identical on both arms)

```
quote_age_ok      : REST snapshot ≤ 5 s old; bid > 0 on every leg; leg spread ≤ 10 % of leg mid (S1: straddle spread ≤ 5 %)
ladder            : t0   limit = net mid                       (DAY, but cancelled by the arm per below)
                    t0+20s unfilled → cancel/replace at mid + 1 tick (debit) / mid − 1 tick (credit)
                    t0+40s → mid + 2 ticks
                    t0+60s → cancel; re-quote from a fresh snapshot at most 3 ladders per session
max_slippage      : never pay more than max_debit = min(net ask, mid × 1.02) for S1,
                    min(net ask, mid × 1.03) for S2/S3 (wider markets); reject otherwise
partial fills     : accepted; remainder continues the ladder; unfilled remainder is cancelled at ladder end
                    and the premium reservation is released to the cycle budget
time windows      : S1 entries 15:40–15:55 ET; S1 exits 15:30–15:58 ET; S2/S3 entries 10:00–15:30 ET,
                    never in the first 15 min or last 5 min; no opening/closing auctions
exits under stress: exit ladders may go to mid − 3 ticks, then the bid, then repeat next minute;
                    an exit is never abandoned (a stuck exit pages the operator)
self-cancel       : arm loses the head for 30 s → cancel all resting CONVEX orders (orchestration §3.2)
per-order caps    : n ≤ 10 % of ATM open interest and ≤ 5 % of the contract's 20-day ADV; Public XSP: split so
                    that ≤ 9 contracts per execution while the $0.00 tier holds
```

Journal every quote used, every ladder step, and the fill; shadow-price every intent on the
other arm (orchestration §3.5 shadow arms) so the paper plan yields the fee/fill comparison.

---

## 5. Budget accounting

### 5.1 Ledger tables (head's Postgres/SQLite; sleeve = `CONVEX`)

```
convex_position(id, structure S1|S2|S3, underlying, legs[], qty, opened_at, expiry, cycle_id,
                premium_paid, premium_at_risk, fees_open, fees_close, closed_at, realized_pnl,
                exit_reason, arm, e_ref_at_open, intent_id, external_ids[])
convex_budget_event(ts, kind RESERVE|RELEASE|SPEND|REALIZE|SWEEP|TRANCHE|HALVE|PAUSE|KILL,
                    amount, position_id, structure, note)
convex_cycle(cycle_id = expiry year-month, e_ref_snapshot, par_open, par_cap = 1 % e_ref)
```

Definitions:
- `premium_at_risk (PaR)` = net debit paid (S1, debit spreads, S3) or max loss at expiry
  (backspreads) **plus opening fees**. Booked as SPEND at fill; a reservation (RESERVE) is
  taken at intent time and released if unfilled.
- `rolling_12m_spend` = Σ PaR of positions opened in the trailing 365 days, **not reduced by
  wins**. Cap 3 % × `E_ref` (I1).
- `cycle_par` = Σ PaR of open positions with expiry in `cycle_id`. Cap 1 % × `E_ref` (I2). S2
  positions with 6–18-month expiries occupy their own future cycle; S3 occupies the current or
  next one; S1 sits in the front cycle. This is why S1 + S3 rarely collide and why S2 must be
  spaced across expiry months.
- Quarterly tranches (I6): 0.75 % released Jan 1 / Apr 1 / Jul 1 / Oct 1; unused tranche carries
  within the calendar year; the rolling-365 window is the binding cap, the tranche is the
  pacing rule. Structure sub-caps: S1 ≤ 3.0 %, S2 ≤ 1.5 %, S3 ≤ 1.0 % (sub-caps overlap; total is
  the 3 %).
- `E_ref` snapshot per cycle at first entry; caps for that cycle do not grow if equity rises
  (they do shrink if the drawdown ladder fires).

### 5.2 Pre-trade check (pseudocode)
```python
def budget_room(structure, cycle_id, per_trade_pct):
    E = E_ref()
    ladder = head.drawdown_state()             # NONE | HALVED | PAUSED | FLAT
    if ladder in {PAUSED, FLAT} or kill.active: return 0
    scale = 0.5 if ladder == HALVED else 1.0
    annual = 0.03 * E * scale;  cycle_cap = 0.01 * E * scale
    room = min(annual - rolling_12m_spend(), cycle_cap - cycle_par(cycle_id),
               SUBCAP[structure] * E * scale - rolling_12m_spend(structure),
               tranche_available(), per_trade_pct * E)
    return max(room - reserved_pending(), 0)
```

### 5.3 Kill rule (from portfolio.md §2.3, made mechanical)
```
evaluate nightly:
  (a) sum(realized_pnl over positions closed in trailing 365 d) + unrealized < −(0.03 * E_ref)
      AND max(realized_pnl / premium_paid over trailing 365 d) ≤ 3.0            → KILL for the rest of the calendar year
  (b) rolling_12m_spend ≥ 0.03 * E_ref (YTD form: premium spent since Jan 1 ≥ annual line)  → KILL (spend cap; no judgement implied)
  per-structure S1: trailing-40-trade mean net return (after fees and slippage) < 0          → RETIRE S1 until written review
  S2 / S3: NO performance kill (fat-tail rule §7); only (a)/(b), the ladder, and the annual review.
KILL: cancel all resting entries; open positions run their exit rules; no new entries until Jan 1 and a written review of
      realized vs implied move per structure (portfolio.md §2.3). Re-open requires operator ack in the journal.
```

### 5.4 Profit sweep (charter rule, executed on every close with `realized_pnl > 0`)
```
gain = realized_pnl − fees_close
if grind_pm.capital < grind_pm.cap: to_grind = min(gain, grind_pm.cap − grind_pm.capital); gain −= to_grind
to_core = gain
journal SWEEP events; the CONVEX sub-account's budget line is NOT increased by the win
(config flag convex.sweep_split = "charter" | "50_50" — portfolio.md §5.3's 50/50 variant is
available but the charter's grind-first rule is the default; the "3 % reset after a > 3× winner"
in portfolio.md is disabled by default because the charter says refills come only from the
budget line).
```
Cash mechanics: the sweep is a journal transfer; physical cash moves happen in the monthly
inter-broker rebalance (ops), not per trade.

---

## 6. Risk hooks

| Hook | Behaviour |
|---|---|
| **Drawdown ladder** (charter, total equity vs running 12-month high-water mark, evaluated daily) | −5 %: all CONVEX caps × 0.5 (annual, cycle, per-trade); open positions unchanged. −10 %: no new entries (S1/S2/S3); open positions run their exit rules; S3 hedge stays. −15 %: close S1 and S2 at the next mid ladder; S3 per §1.3 rule 4 (charter literal until amended). Re-risk one rung per new 3-month equity high, at most one rung per 20 trading days (portfolio.md §5.5). **Note:** portfolio.md §5.5 proposes a −8/−12/−16/−20 ladder; the charter's −5/−10/−15 is the treasury rule and is implemented; the difference is logged as an open item for the operator. |
| **Max delta-notional 15 %** (I3) | Every 60 s from streamed/polled greeks: `Σ|Δ_leg|·qty·multiplier·spot_underlying` (S2 backspreads and ITM S3 spreads are the usual offenders). Breach → trim the position with the largest contribution by the smallest lot that restores compliance; alert. Also a pre-trade check on the marginal position. |
| **Assignment / expiry** | Index options (XSP, SPX/SPXW, VIX, XND, MRUT): European, cash-settled — no assignment; exit by DTE ≤ 7 to avoid the SOQ/PM settlement print. Equity/ETF options (S1, S2-ETF): American — S1 is long-only, so only exercise risk: on the exit day the arm sells; if an S1 leg cannot be sold (no bid) and is ITM by more than fees, submit exercise, else let expire. S2-ETF short legs: closed by DTE ≤ 60 (rule 4) and by the ex-div rule; hard stop: no equity option position may exist at 14:00 ET on its expiry day (auto-flatten). If an overnight assignment does happen (stock appears in the account), the arm flattens the stock at the open with a limit at the mid ladder and pages the operator; the stock never enters CORE. Pin-risk: no equity option with strike within 2 % of spot may be held into the last session. |
| **PDT** | FINRA Rule 4210 amendments eliminated the pattern-day-trader designation and the $25k minimum effective **2026-06-04** (Regulatory Notice 26-10; SEC approval 2026-04-14; firms may phase in until 2027-10-20) [V]. S1's same-day exits are therefore not blocked by PDT at compliant brokers, but each broker's house rule may lag → per-broker test (§10). In margin accounts the arm tracks the new intraday margin deficit (options are 100 % premium anyway). In a cash account, options settle T+1: the arm may not spend unsettled proceeds on a new S1 entry the same day (good-faith violation guard: `settled_cash ≥ premium` check). |
| **Event overlap** | The head's ownership lock treats "FOMC/CPI decision" as one event across arms (portfolio.md §5.4): CONVEX does not open S3 in the 24 h before an FOMC statement if GRIND-PM or MACRO holds a Fed/CPI contract; it may hold an existing S3. |
| **Liquidity/venue** | If the primary arm rejects (BP, hours, cap) the router tries the backup arm at the same limit; never legs a structure across two arms. |
| **Staleness** | No entry if the earnings calendar is > 24 h old, the chain snapshot > 5 s old, or the greeks feed > 60 s old for the I3 monitor (block new entries, not exits). |
| **Error-trade guard** | Per-leg price band: reject any leg whose limit is > 25 % away from the last NBBO mid; per-order notional cap = per-trade cap; the head refuses intents that would push any cap over 100 %. |

---

## 7. Paper plan and graduation

**Environments.** tastytrade cert (`api.cert.tastyworks.com`): synthetic fills (market fills at
$1; limit < $3 fills immediately; ≥ $3 never fills), `/market-data` and `/market-metrics` return
502, resets every 24 h [V]. It is useful **only** for order-format, dry-run, lifecycle and
account-streamer tests, not for fill realism. Public has no paper environment [V]. Therefore the
paper mode is the **head's replay/shadow executor**: real-time Public chain snapshots (and DXLink
greeks) drive the strategy; fills are simulated at `mid + k·halfspread` with `k` drawn from
{0.25, 0.5, 1.0} and the paid price recorded for all three; all three arms are shadow-priced.
The tastytrade production account is used read-only for `/market-metrics`.

**Calendar (minimum):**
1. One full earnings season: 2026-10-12 → 2026-11-27 (Q3 reports; ≥ 6 weeks) — S1 live in paper,
   both exit variants (pre-release close = primary; hold-through = shadow) and both entry
   offsets (3 vs 4 sessions) recorded per trade.
2. One FOMC: the next scheduled meeting after paper start (October 2026 per the Fed calendar;
   verify on federalreserve.gov) — S3 held across the decision; event-overlap hook exercised.
3. S2: at least two entries and one forced exit of each kind (trend flip, DTE ≤ 60, take-profit
   half-close) — if the market does not supply a trend flip inside the window, inject one via the
   replay executor from 2022 data to test the code path.
4. Failover drill in paper with CONVEX positions open (orchestration §3.4).

**Graduation gates (trade counts), per structure — no sleeve inherits another's evidence:**

| Structure | Paper → canary (¼ size) | Canary → 1× | 1× → 2× (max) |
|---|---|---|---|
| S1 | ≥ 40 paper trades passing the 5 % spread gate; mean net return (k = 0.5 fill) > 0; simulated fills within 1 % of recorded prints on the 10 trades that were also placed 1-lot live for calibration; zero calendar-error exits caused by the merge logic | ≥ 40 live canary trades; trailing-40 mean net return > 0; bootstrap 90 % lower bound of the mean > −1 % | ≥ 40 further live trades; trailing-80 mean > 0; profit factor ≥ 1.2 |
| S2 | ≥ 2 positions opened and every exit rule executed correctly once (real or injected); budget/cycle accounting reconciled to the cent; greeks-based I3 monitor validated | ≥ 4 positions through their full life **or** 12 months, whichever first; decision on expectancy and process, **never on win rate or N** | operator review only; 2× never before 24 months of live history |
| S3 | ≥ 3 monthly roll decisions (including at least one "VIX ≥ 18 → no entry") and one FOMC held | ≥ 6 live roll decisions; process clean | never above 1× (it is a hedge budget) |

**Fat-tail rule (binding).** S2 and S3 are low-hit-rate, high-payoff strategies. They are
**not** killed, demoted or judged on N < 30, on win rate, or on aggregate PnL over a short
window. The prediction-market alpha playbook (`AKCodez/prediction-market-alpha-playbook`,
METHODOLOGY.md, the repo's graduation reference in `docs/PREDICTION_MARKET_ARB_STRATEGY.md`
and `docs/WEEVIL_REPO_SURVEY.md`) states the rule directly: "10% WR on 5 trades is NOT evidence
of failure if mean PnL is positive and distribution is fat-tailed"; "Before ANY graduation
decision, decompose the win distribution" (is the top 1 % of trades > 50 % of PnL?); "Retarget,
don't amputate" (4 of 4 "killed" strategies had hidden alpha; killing on aggregate was
systematically wrong); and "Wilson lower bound, not point WR… 90% WR on N=5 is noise; 60% WR on
N=100 is tradeable." The playbook's ≥ 30 post-deploy trades gate for scaling is adopted for S1
(a hit-rate strategy) and is explicitly **not** used as a kill trigger for S2/S3. The only things
that stop S2/S3 are the spend cap, the ladder and the annual written review. Consistent with the
playbook's "Data API > journal", every graduation statistic is computed from broker
fills/history endpoints reconciled nightly, never from the head's own journal alone.

---

## 8. Equity-band behaviour

Arithmetic: annual line = 3 % E, cycle cap = 1 % E, S1 per-trade 0.25 % E, S2 debit 0.3 % E,
S3 0.25–0.5 % E. One XSP put spread 5 %/15 % OTM at 60 DTE costs roughly $40–120; one 30-delta VIX
call spread $60–200; an ATM straddle on a $30 small-cap with 60 % IV and ~10 DTE ≈ $250–400;
an 18-month XSP call spread ≈ $150–400 (all order-of-magnitude, to be replaced by the paper log).

| Total equity | S1 | S2 | S3 | Notes |
|---|---|---|---|---|
| **< $5k** | off (cycle cap ≤ $50 cannot buy one straddle) | off | **off by default**; optional one XSP put spread per quarter ≤ $50 if `convex.tiny_mode = true` (buys process evidence, not returns) | 3 % of $5k = $150/yr; fees dominate; the sleeve's job here is to run the code path in paper only |
| **$5k–25k** | off below $15k; from $15k one name at a time, stock price ≤ $40, 1 contract | off below $20k; from $20k one XSP debit call spread at a time (0.3 % = $60–75 → 1 contract, narrow width) | XSP put spread only (VIX spreads too lumpy), 1 contract, ≤ 0.25 % | cycle cap $50–250; index-only means everything is 1256 and there is no wash-sale bookkeeping |
| **$25k–100k** | 1–4 names per cycle, 1–2 contracts each | 1–2 open positions (XSP, XND); ETF spreads allowed if outside CORE tickers | VIX call spreads enter (1 contract); XSP put spread as the alternative | cycle cap $250–1,000; Public XSP ≤ 9 contracts/execution stays free |
| **$100k–1M** | full spec: ≤ 4 concurrent names, per-order liquidity caps bind before budget caps do (10 % OI / 5 % ADV) | full spec; 2–4 positions across different expiry months; SPX instead of 10× XSP above ~$500k on tastytrade | full spec; split XSP executions into ≤ 9-lots or accept $0.07 | cycle cap $1k–10k; annual $3k–30k; at $1M consider a separate 1256-only account for S2/S3 to keep the 6781 statement clean |

Below the band where a structure can size to ≥ 1 contract the head logs `skip_reason=band` and
does nothing; it never rounds up.

---

## 9. Tax

- **Section 1256 instruments** (XSP, SPX/SPXW, VIX, NDX, XND, MRUT, options on micro futures):
  60 % long-term / 40 % short-term regardless of holding period, marked to market on 12/31,
  Form 6781, **wash-sale rules do not apply** [V practitioner, portfolio.md §3.2]. S2 and S3 default
  to these; the design makes them the only S2/S3 instruments below $25k (§8). Cash settlement
  also removes assignment risk (§6).
- **Equity and ETF options** (all of S1; S2-ETF): 1099-B, short-term ordinary rates, §1091 wash
  sales across **all** accounts including IRAs (Rev. Rul. 2008-5 → permanent disallowance if the
  replacement is in an IRA) [V practitioner]. Brokers report wash sales only within one account;
  the head's cross-broker lot ledger is the source of truth.
- **Wash-sale interaction with CORE:** §1091 is triggered when, within ±30 days of a loss sale,
  the taxpayer acquires substantially identical stock **or an option to acquire it**. Hence:
  (i) CONVEX never buys calls on a ticker CORE holds or has sold at a loss in the last 30 days
  (I9, enforced by the ownership lock with a 31-day cooling period after any CORE loss sale);
  (ii) S1 single stocks are outside the CORE ETF universe by construction; (iii) XSP/XND vs
  SPY/QQQ: cash-settled index options are generally not treated as substantially identical to the
  ETF [T] but the CPA opinion required by the orchestration compliance checklist (§4 item 5) must
  cover this; until then S2 does not open XSP within 30 days of a CORE SPY/IVV/VOO loss sale
  (belt and braces, costs nothing). (iv) CONVEX's own losses: closing one leg of an S1 straddle at
  a loss and re-entering the same strike/expiry within 30 days is itself a wash; the ladder never
  re-enters the same contract within 31 days.
- **§1092 straddle rules:** a long call + long put on the same underlying are not offsetting
  positions (both are long volatility), so S1 is not a §1092 straddle; S2 spreads and S3 spreads
  in 1256 contracts are exempt (1256 MTM); S2 ETF spreads (long + short call) may be a §1092
  straddle for loss-deferral purposes **[U]** → CPA question.
- **TTS / §475(f):** not elected in year one (portfolio.md §6); if later elected, limit it to
  securities so 1256 keeps 60/40.
- **Reporting:** one consolidated export across arms; CONVEX's 1256 activity lands on the 6781
  statement of whichever broker holds it (Public and tastytrade both issue one) — keep S2/S3 on
  one arm per calendar year where possible to avoid two 6781s.

---

## 10. UNVERIFIED items, written as tests

| # | Item | Test (pass criterion) |
|---|---|---|
| U1 | Public multi-leg endpoint accepts **index** options (XSP/VIX) with `type: "OPTION"` and OCC root `XSP` | `preflight/multi-leg` with a 1-lot XSP put spread returns 200 with `estimatedCost`; then a 1-lot live order fills; if 400, try `type: "INDEX_OPTION"` and the `optionDetails` form |
| U2 | Public index-option fee: fee-schedule PDF says "$0.35/contract + pass-through" (brokers.md) vs the index-options exchange-fee page "XSP $0.00 ≤ 9 contracts" (portfolio.md) | `estimatedCommission` on the U1 preflight = $0.00 for ≤ 9 XSP contracts; then the confirm/history record shows the same |
| U3 | Symbol form for index underlyings in `option-expirations` / `option-chain` (`XSP`, `$XSP`, `SPX`) and for the VIX spot quote (`type: INDEX`) | expirations call returns a non-empty list for each candidate; VIX quote returns a value within 0.05 of Cboe's |
| U4 | Public `option-greeks` request/response schema (page 403 today) | POST a list of 5 OSI symbols, receive delta/gamma/theta/vega/IV per symbol |
| U5 | DXLink `Greeks` events are delivered for index options (SPX/XSP/VIX) on the API token (GitHub issue #142 reports licensing gaps) | subscribe to `Greeks` on `.XSP…` and `.VIX…` streamer symbols; events arrive within 5 s during RTH |
| U6 | tastytrade `/market-metrics` field names and definitions (`tw-` vs `tos-` IV rank; `earnings.time-of-day` values) | GET for 20 symbols in production; compare `tw-implied-volatility-index-rank` to the head's own 252-day rank once 60 snapshots exist (Spearman ρ > 0.8) |
| U7 | Finnhub `/calendar/earnings` exact fields, `hour` values, free-tier limit, and small-cap accuracy | 60-day backfill: date+hour agreement with tastytrade ≥ 95 % on the S1 universe; log every disagreement |
| U8 | FMP `earnings-calendar` / `earnings-confirmed` availability on the free plan | 200 response with `when` ∈ {bmo, amc} on ≥ 90 % of S1 names |
| U9 | VXTH weight schedule (0 % ≤ 15 / 1 % 15–30 / 0.5 % 30–50 / 0 % > 50) — the PDF table is an image | OCR the table or Cboe's VXTH fact sheet; then run the paper season with both the brief's `< 18` rule and the VXTH schedule in shadow and compare cost/payoff |
| U10 | Broker-side PDT / intraday-margin implementation after Notice 26-10 at Public and tastytrade | five S1 same-day round trips in a week in a sub-$25k margin account produce no PDT flag or restriction; in a cash account no good-faith violation with the settled-cash guard on |
| U11 | GXZ exit convention (announcement-day close vs pre-release last session) and entry offset (3 vs 4 sessions) | paper-season A/B per trade; choose the variant with the higher mean net return only if the difference has a bootstrap 90 % interval excluding zero; otherwise keep the pre-release close (defined-risk reasoning) |
| U12 | XND and MRUT are 1256 contracts and have usable liquidity (spread ≤ 10 % of mid on 12-month calls) | CPA confirmation + 20-day quote log |
| U13 | Public per-account throttle behaviour at the 10 req/s cap during the nightly S1 scan (300 chains) | no 429s at 8 req/s sustained; backoff tested |
| U14 | tastytrade cert accepts multi-leg **index** option orders in dry-run (for lifecycle tests) | dry-run returns a `buying-power-effect` for a VIX call spread |
| U15 | Whether S3 should be exempt from the −15 % "everything flat" rung (charter amendment proposed in §1.3) | replay 2020-02/03 and 2022 with both behaviours; present to the operator with the charter change request |
| U16 | Reconciliation of the sweep: GRIND-PM cap value and the physical cash-move cadence | monthly inter-broker rebalance runbook entry exists and one dry run is journaled |
| U17 | Nanos (NANOS) still listed and tradable on Public for the < $5k band | expirations call returns non-empty; otherwise the band stays XSP-only |

---

## Appendix A — Intent schema additions (orchestration §3.3)

```
Intent += { sleeve: "CONVEX", structure: "S1"|"S2"|"S3", legs: [Leg{occ_symbol, side, open_close, ratio}],
            qty, limit, max_debit, tif: "DAY", premium_at_risk, cycle_id, exit_rules: [...], shadow_arms: [...] }
Fill   += { net_price, per_leg_prices[], fees, arm, ladder_step, quote_snapshot_id }
Position += { greeks{delta,gamma,theta,vega}, delta_notional, dte, unrealized, exit_due_at }
```

## Appendix B — Config defaults

```yaml
convex:
  annual_budget_pct: 0.03      # I1
  cycle_cap_pct: 0.01          # I2
  delta_notional_cap_pct: 0.15 # I3
  tranche_pct: 0.0075          # quarterly
  sweep_split: charter         # charter | 50_50
  reset_on_3x_winner: false
  tiny_mode: false             # < $5k XSP-only experiment
  s1: {per_trade_pct: 0.0025, max_names: 4, entry_offset_sessions: 3, spread_gate: 0.05,
       price_min: 10, price_max: 150, cap_min: 3e8, cap_max: 1e10, iv_min: 0.45, hv_min: 0.40,
       avg_abs_move_min: 0.06, oi_min: 200, vol_min: 100, exit_variant: pre_release_close,
       shadow_variants: [hold_through, entry_offset_4], retire_after_n: 40}
  s2: {debit_pct: 0.003, par_pct: 0.005, subcap_pct: 0.015, iv_rank_max: 30, backspread_iv_rank_max: 20,
       dte_min: 270, dte_max: 540, dte_exit: 60, long_otm_max: 0.15, tp_multiple: 3.0, width_exit: 0.70,
       universe: [XSP, XND, MRUT, GLD, IAU, TLT, IEF, EEM, VEA]}
  s3: {vix_max: 18, vix_big_size_below: 14, size_pct: 0.0025, big_size_pct: 0.005, subcap_pct: 0.01,
       vix_dte: [30, 60], xsp_dte: [45, 75], tp_multiple: 3.0, width_exit: 0.60, dte_exit: 7}
  execution: {quote_max_age_s: 5, ladder_steps: [0, 1, 2], ladder_step_s: 20, ladders_per_session: 3,
              max_slip_s1: 0.02, max_slip_s2s3: 0.03, max_oi_frac: 0.10, max_adv_frac: 0.05,
              xsp_lot_split: 9, self_cancel_s: 30}
  kill: {trailing_loss_pct: 0.03, rocket_multiple: 3.0}
```
