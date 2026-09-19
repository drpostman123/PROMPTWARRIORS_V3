# Sizing & Capacity Design — Evidence Brief (2026-09-19)

Scope: how every sleeve of the master system (CORE, CONVEX, GRIND-PM, GRIND-CRYPTO, MACRO; see `docs/MASTER_SYSTEM_CHARTER.md`) sizes itself from $1 to $1,000,000 of buying power, where each sleeve's minimum ticket clears fees, where each stops scaling, and what "adjustable do-or-die" means in numbers. Tags: **[S]** official/primary source fetched today, **[T]** third-party summary only, **[M]** measured/derived in this repo (`docs/research/quant.md`, `docs/SMALL_ACCOUNTS.md`), **[C]** computed here from tagged inputs, **[U]** could not be verified.

---

## 1. Minimum tradable units and their dollar cost

### 1.1 Equities (fractional)
| Broker | Minimum | Commission | Source |
|---|---|---|---|
| Public | Fractional orders < $1.00 not accepted (disclosure); help center says **$5 minimum** for fractional stock buys/sells (the two documents disagree; assume $5) | $0; regulatory fees on sells | [S] https://public.com/disclosures/public-fractional-shares-disclosure ; https://help.public.com/en/articles/1694953-what-is-the-minimum-amount-to-invest-in-stock |
| Webull | **$5 or 1/100,000 share** | $0 (SEC/FINRA/CAT pass-through on sells: FINRA $0.000195/share max $9.79, SEC $0.0000206) | [S] https://www.webull.com/pricing ; [T] https://www.webull.com/trading-investing/stocks |
| Robinhood | **$1 or 0.000001 share**; stocks > $1 and > $25M cap | $0 | [S] https://robinhood.com/us/en/support/articles/fractional-shares |
| tastytrade | Whole shares only (no fractional program found) **[U]** | $0 | https://tastytrade.com/pricing/ |

### 1.2 Crypto
| Broker | Min order | Cost | Source |
|---|---|---|---|
| Robinhood Crypto (API v1/v2) | **$0.01** (market-maker routing), **$0.03** (smart exchange routing); fractional coins | MM routing: "no commissions" but RH receives $0.95 per $100 notional from MMs (as of 2026-06-15) — i.e. an embedded ~1% spread; Exchange routing tiers (30-day volume, taker/maker): $0–10K **0.95%/0.50%**; $10–50K 0.75/0.35; $50–250K 0.25/0.125; $250–500K 0.15/0.075; $500K–1M 0.125/0.06; $1–5M 0.10/0.04; $5–10M 0.04/0.02; $10–25M 0.03/0.01; $25M+ 0.03/0.00 | [S] https://robinhood.com/us/en/support/articles/crypto-buying-and-selling/ ; https://robinhood.com/us/en/support/articles/crypto-order-routing/ ; https://cdn.robinhood.com/assets/robinhood/legal/rhc-fee-schedule.pdf (2026-06-22) |
| Webull | **$2** ($1 in NY/GU/MP); 1e-8 coin; max $100k/trade, $200k pending buys | **1% spread each side** (2% round trip); stablecoins exempt | [S] https://www.webull.com/help/faq/11091-Fees-Limits |
| tastytrade | not stated | 1% per total purchase (0.75% BTC/ETH), open and close | [S] https://tastytrade.com/pricing/ |
| Public (zerohash) | $0.01 | Non-API: $0.49 on ≤$10 orders … 1.25% above $500; **API accounts: 0.60% (<$10K/mo) → 0.10% ($100M+)** | [S] https://public.com/disclosures/fee-schedule |

### 1.3 Options — one contract
| Item | Fact | Source |
|---|---|---|
| Contract multipliers | SPX $100 × index (~$765k notional at 7,650); **XSP = 1/10 SPX, $100 multiplier** (~$76.5k), cash-settled, European, daily expirations, §1256; SPY ≈ XSP notional, American, physical | [S] https://www.cboe.com/tradable-products/sp-500/xsp-options/ |
| Typical 0DTE ATM premium | ATM 0DTE premium ≈ **18 bp of index at 12:00 ET, ~8 bp by 15:30** (Cboe quote snapshots 2022–Aug 2026) → at SPX 7,650: **SPX ≈ $1,380**, **XSP ≈ $138**, **SPY ≈ $138** per ATM contract at noon [C] | [T] https://concretumgroup.substack.com/p/spx-0dte-options |
| Defined-risk ticket | $1-wide SPY / 1-pt XSP vertical: max risk ≤ $100; 5-pt SPX vertical ≤ $500; measured SPY $2-wide debit verticals risk **$66–$90/contract** at accepted debits | [M] `docs/SMALL_ACCOUNTS.md` §1 |
| Public commissions | Equity options **$0 + rebate $0.06/contract (API)**; index options **$0.35/contract** + exchange pass-through: **XSP $0.00 (≤9 contracts) / $0.07 (>9)**; SPXW $0.50 (prem ≤ $0.99) / $0.59; SPX $0.57/$0.66 | [S] https://public.com/disclosures/fee-schedule ; https://public.com/disclosures/index-options-exchange-fees |
| tastytrade | Equity options **$1 open / $0 close, $10 cap per leg**; broad-based index options $1 open / $0 close; + exchange/clearing/regulatory fees | [S] https://tastytrade.com/pricing/ |
| Webull | Equity options $0; **$0.50/contract "certain index option trades"**; $0.10/contract oversized orders | [S] https://www.webull.com/pricing |
| Spreads | SPX ATM 0DTE $0.50–$2.00 wide (0.013% of notional at $1); XSP quotes in $0.05 increments and is far thinner than SPX/SPY | [T] https://spxtospy.com/articles/spx-0dte-vs-spy-0dte.html ; https://spxytrader.com/content/intro/xsp-vs-spx-options |

### 1.4 Micro futures (CME) — margin per contract
Webull's live margin table (fetched 2026-09-19; CME-derived, tastytrade states it uses standard CME margins with 25% intraday):

| Contract | Intraday | Initial | Maintenance | Notional / tick [C] |
|---|---|---|---|---|
| MES | $100.33 | **$2,866.60** | $2,606 | $5 × 7,650 ≈ **$38k**; tick $1.25; 1% move = $382 |
| MNQ | $164.09 | $4,688.20 | $4,262 | $2 × NDX (~$56k at 28,000); 1% ≈ $560 |
| M2K | $48.40 | $1,210 | $1,100 | $5 × RUT |
| MGC | $243.43 | $2,434.30 | $2,213 | 10 oz; 1% ≈ $370 at $3,700 |
| MCL | $141.08 | $940.50 | $855 | 100 bbl; 1% ≈ $65 |
| MBT | $121.50 | $2,700 | $1,800 | 0.1 BTC |
| 2YY / 5YY / 10Y / 30Y | $181.50 / $170.50 / – / $148.50 | $363 / $341 / $330 / $297 | $330 / $310 / $300 / $270 | $10 per 0.001 yield (1 bp = $10) |

Sources: [S] https://www.webull.com/futures-margin-rates ; tastytrade intraday = 25% of initial [T] https://support.tastytrade.com/support/s/solutions/articles/43000435185 ; tastytrade "no minimum account balance to trade CME futures in a margin account" and **$0.75/contract micro** [S] https://tastytrade.com/pricing/ , https://tastytrade.com/futures/ ; Webull **$0.25 micro / $0.70 mini / $1.25 regular** per contract [S] https://www.webull.com/futures-trading (per-side vs round-turn not stated; exchange/NFA fees extra, amounts **[U]**). tastytrade's own per-product margin table could not be fetched (help-center CSS error) **[U]**.

### 1.5 Event contracts
| Item | Fact | Source |
|---|---|---|
| Unit | 1 contract, price $0.01–$0.99 (sub-cent to $0.0001 on some markets), pays $1/$0 | [S] https://developer.webull.com/apis/docs/broker-api/event-contract-guidance/ |
| Fractional | Webull: "fractional (down to 0.01 contracts)" on some high-volume markets; **Kalshi API v2 `count` accepts 0.01-contract granularity** | [S] same ; https://docs.kalshi.com/api-reference/orders/create-order-v2 |
| Per-order caps (Webull) | **500,000 contracts / $50,000 notional** (broker-API page); trade-API page says 50,000 — treat 50,000 as binding | [S] same ; [M] plan §1.3 |
| Kalshi per-order cap | none stated in create-order-v2 **[U]** | |
| Fees | Webull **$0.02/contract/side** ($0.01 exchange + $0.01 firm), none at settlement; Kalshi direct taker ≈ ceil(0.07·p(1−p)) (1.75¢ max at 50¢), maker ≈ ¼; Kalshi fee-schedule PDF is behind a Vercel checkpoint today — formula from repo docs and [T] | [S] https://www.webull.com/help/faq/11052-Common-Questions-About-Event-Contracts ; [T] https://whirligigbear.substack.com/p/makertaker-math-on-kalshi ; https://kalshi.com/docs/kalshi-fee-schedule.pdf |
| Whether the $0.02 is prorated on a 0.01-contract fill | **[U]** — test in sandbox | |

### 1.6 Bonds / Treasuries (Public)
- Fractional bonds (Treasury, muni, corporate) from **$100**, any dollar increment; transaction fee **$0.10 per $100 par (Treasuries 0–1y), $0.25 (1y+)**; corporates $0.35–$0.50 per $100. [S] https://public.com/disclosures/fee-schedule ; [T] https://medium.com/the-public-blog/introducing-fractional-bond-trading-exclusively-on-public-e8c8da948dd5
- 6-month T-bill "Treasury Account" (Jiko): **$100 minimum and increments**, 0.05%/month management fee. [S] https://help.public.com/en/articles/6997498-what-is-the-minimum-and-maximum-funding-requirement-into-a-treasury-account ; fee schedule (Jiko section).
- Bond Account (ten-bond ladder): $1,000 minimum. [T] https://www.prnewswire.com/news-releases/public-launches-bond-accounts-302220248.html

---

## 2. Fee floors vs edge — where the minimum viable ticket clears its fees

Method: per-ticket friction (commission + half-spread + regulatory) versus the sleeve's expected gross edge per ticket; the "viable" equity band is where (a) one minimum ticket ≤ the sleeve's per-trade risk budget and (b) friction ≤ ~⅓ of expected gross edge.

| Sleeve | Broker | Minimum sensible ticket | Friction per ticket | Break-even condition | Viable from (equity) |
|---|---|---|---|---|---|
| CORE equities | Robinhood / Public / Webull | $5 fractional ETF buy | ~$0 commission; SPY spread ≈ 1 bp; sells pay ≤ $0.01–0.05 regulatory | any positive drift; monthly turnover | **$5** (all three) — practically $100+ so 1% rebalances round to ≥ $1 |
| CORE crypto | Robinhood (exchange routing, limit/maker) | $1 | 0.50% maker (<$10K/30d) → 1.0% round trip; 0.125% at $50–250K | Holding-period expected return ≫ 1% → monthly/quarterly rebalance only | **$1**; choose Robinhood exchange routing over Webull's 2% round trip at every size |
| CORE crypto | Webull | $2 | 1% each side = 2% RT | same, worse | use only as backup |
| GRIND-PM | Webull events | 1 contract at 50¢ ($0.50) | $0.02 flat (4% of stake at 50¢; 20% at 10¢) | need p_true − ask ≥ 2¢; measured study-grade edges 3–8¢ [M] | **$1** mechanically; **$500–5k as a measurement budget** (a 3¢ edge needs ~1,100 independent trades for t = 2 [M]) |
| GRIND-PM | Kalshi direct (maker) | 1 contract | ~0.44¢ at 50¢ (maker), 1.75¢ taker | maker strategies only viable here; 4–8× cheaper than Webull at tails | **$1**; needs API key + Rule 3.3(b) disclosure |
| CONVEX | Public (SPY/XSP one-lot) | 1 vertical, risk $60–$150 | Public: ~$0.05–$0.10 regulatory + spread (SPY $0.01, XSP $0.05 = $5); tastytrade-style $2.60/contract RT [M] | Friction ≤ 3–4% of risk; hit rate ≥ 0.59–0.61 at +65%/−50% targets [M] | **$1.7–2.3k one-lot floor at 4% cap; $3.3–4.5k at 2%/trade** [M]; **SPX ≥ $33–45k** |
| CONVEX | tastytrade | same | $1 open per contract ($2 per vertical) + fees | worse than Public for 1-lots | as above + $2 |
| MACRO via ETFs | Public / Robinhood / Webull | $5 fractional (GLD, TLT, USO-type, UUP) | ~0 | trend edge > 1–2 bp | **$5** |
| MACRO via micro futures | Webull ($0.25) / tastytrade ($0.75) | 1 contract | RT $0.50–$1.50 + exchange/NFA **[U]** ≈ 1 tick | trivially cleared; **risk granularity binds, not fees**: 1 MES 1% move = $382 → at 2% risk & 1.5% stop ≈ **$29k**; 2YY/MCL 1% ≈ $10–65 → ~$5k; overnight margin must stay ≤ 25% of equity → MES ≥ $11.5k | 2YY/5YY/MCL ≈ **$5–10k**; MES/MGC ≈ **$25–30k**; MNQ ≈ $40k |
| GRIND-CRYPTO | Robinhood exchange routing | — | maker 0.50% → 0.04% only at $1–5M 30-day volume | basis/funding edges are 5–30 bp; RT cost must be < 10 bp → tier ≥ $1M/30d | **not viable below ~$250k equity** turning over 4×/month; stays 0% per charter |

Key computations: at ask a = 0.50 with fee 0.02, Kelly f* = (p − 0.52)/(0.48): 3¢/5¢/8¢ net edge → 6.3% / 10.4% / 16.7% of bankroll; quarter-Kelly 1.6% / 2.6% / 4.2% [M]. One 50¢ contract is 1.6% of a **$31** bankroll — so the granularity floor for quarter-Kelly single-contract Picador trades is ~$30–60, but the *evidence* floor (§6) is hundreds of independent events.

---

## 3. Capacity ceilings — where each sleeve stops scaling

| Sleeve / venue | Measured or documented ceiling | Implication |
|---|---|---|
| Kalshi BTC hourlies | median **34,111 contracts and 1,031 trades per hour**; only 10–40 of 188 markets ever trade; ~26% of contracts print in the last 5 min; touch depth ≤ 300 contracts in the first hour, ~1,500 at best bid near expiry; notional at risk ≈ **$8.3k/hour** on one costed event [M] | Taking ≤ 10% of touch depth → **15–150 contracts (~$5–75) per market per shot**; realistic deployable **$500–3k per hour** across an event without moving the book |
| Kalshi INX hourlies | median contracts/event: 10:00 171k … 15:00 133k, **16:00 358k**; top strike 114k on 2026-09-18 [M] | 4pm event can absorb thousands of contracts per strike; 10:00–15:00 a few hundred per strike at touch |
| Kalshi position limits | **$7,000,000 per member (INX)**; **$1,000,000 per strike per member (BTC)**; limits aggregate across all accounts one person controls (Rule 5.19(f)) | Not binding below $1M equity; the ownership lock and disclosure (Rule 3.3(b)) are the binding constraints. [S] https://kalshi-public-docs.s3.amazonaws.com/contract_terms/INX.pdf ; https://kalshi-public-docs.s3.amazonaws.com/contract_terms/BTC.pdf |
| Webull per-order | 50,000 (or 500,000) contracts / **$50,000 notional** per order; 600 orders/60 s | $50k/order is above any sane hourly ticket; rate limit allows 10 orders/s |
| Kalshi error trades | ±20¢ no-cancel range; a cancelled error costs the trader **$3,000**, no malfunction exception for FCM customers (Rule 5.11) | A price band of ±10¢ from mid and a per-order notional cap are survival rules, not tuning |
| SPX 0DTE options | SPX ADV record **5.1M/day, 0DTE 3.1M/day (Q2 2026)**; 0DTE share 62.4% (Aug 2025), ~63% (Feb 2026); retail ≈ 53% of 0DTE volume; ATM spreads $0.50–$2.00; displayed size can be 5 contracts with hidden liquidity behind | CONVEX at ≤ 10% of equity is ≤ $100k at $1M → ~70 SPX or 700 XSP contracts/day: **not capacity-bound below $1M**; XSP is the thin one — move XSP→SPX around **$35–45k** (SPX one-lot floor) [S] https://www.cboe.com/insights/posts/spx-0-dte-options-jump-to-record-62-share-in-august/ ; [T] https://www.streetinsider.com/Corporate+News/Cboe+options+volume+hits+records+in+June,+Q2+2026/26736026.html |
| Micro → full futures | 10 MES = 1 ES; commission 10 × $0.25 = $2.50 vs $1.25 (Webull), 10 × $0.75 vs $1.00 (tastytrade) | Switch to the full contract when the *target* size is consistently ≥ 10 micros — at 2% risk / 1.5% stop that is ≈ **$290k equity for ES**; keep micros for the remainder (granularity) |
| Crypto depth | BTC 1% depth on US exchanges ATH **$290M** (Kaiko, "early May"); Binance > $600M (Oct 2025); Robinhood exchange routing goes to EDX/Bitstamp | BTC/ETH CORE at ≤ $700k is < 0.3% of US 1% depth: not binding; alts are — cap non-BTC/ETH at 1% of 1% depth [T] https://research.kaiko.com/insights/a-summer-of-uncertainty-ahead ; https://www.kaiko.com/resources/the-crypto-liquidity-concentration-report |
| PDT rule | **Eliminated effective 2026-06-04** (SEC approval 2026-04-14, Release 34-105226; FINRA RN 26-10); replaced by intraday margin-deficit standards; **firms may phase in until 2027-10-20**, so some brokers still count day trades. Event contracts "not subject to PDT rules", but Webull limits event BP to the excess over $25k if the linked margin account is PDT-flagged | Do not assume PDT is gone at a given broker until its own notice says so; run CORE/CONVEX in cash accounts below $25k (T+1 settled-funds rule instead) or verify the broker's status. [S] https://www.finra.org/rules-guidance/notices/26-10 ; https://www.sec.gov/files/rules/sro/finra/2026/34-105226.pdf ; https://www.webull.com/learn/courseware/fZidum/Trading-Event-Contracts |
| Cash-account settlement | Stocks/ETFs/options settle **T+1**; buying then selling before paying with settled funds = good-faith violation; 3 in 12 months → 90-day settled-cash restriction | Small cash accounts must size CONVEX from *settled* cash and not recycle same-day proceeds. [S] https://www.fidelity.com/learning-center/trading-investing/trading/avoiding-cash-trading-violations |

---

## 4. Equity-band ladder

Sizing vocabulary used below:
- **Fixed fraction** — risk r% of current equity per trade (Vince "fixed fractional"; optimal-f is the growth-maximal r but "like plutonium" — never used at its peak). [T] https://bettersystemtrader.com/011-ralph-vince/ ; https://quantpedia.com/beware-of-excessive-leverage-introduction-to-kelly-and-optimal-f/
- **Shrunk fractional Kelly** — f = c · Kelly(edge_shrunk), c ≤ ¼, edge shrunk toward 0 by the calibration slope. Justification: growth loss is **first-order in the probability error, second-order in the fraction error** (Meister 2024: D(k/N‖p+ε) − D(k/N‖p) = (p−k/N)/(p(1−p))·ε + O(ε²) vs −ε²/(4p(1−p)) for the fraction) → mis-estimated edge is the dominant risk and the fraction should be cut, not tuned. [S] https://arxiv.org/abs/2412.14144 ; Thorp: "overbetting is much more severely penalized than … underbetting"; half-Kelly keeps ¾ of the growth rate; **P(ever falling to fraction x of bankroll) = x^(2m/s²) = x for full Kelly** and x^(2/c − 1) for fraction c → half-Kelly x³, quarter-Kelly x⁷ (P(−50%) = 50% / 12.5% / 0.8%). [S] https://gwern.net/doc/statistics/decision/2006-thorp.pdf §3.2, §7 ; MacLean–Thorp–Ziemba "good and bad properties" and "quick bankruptcy for the aggressive overbet strategies". [S] https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1797366 ; https://webhomes.maths.ed.ac.uk/mckinnon/blackouts/StochOptFinanceAndEnergySpringer/Chap1_KellyZiemba.pdf
- **Vol targeting** (CORE/MACRO) — scale exposure to target σ; raises Sharpe for equities/credit, cuts left tails across asset classes (Harvey et al. 2018; Moreira–Muir 2017). [S] https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538 ; https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12513
- **Drawdown control** — Grossman–Zhou (1993): with a floor W ≥ αM (M = running max), invest in proportion to the surplus W − αM; not exactly optimal in discrete time (Klass–Nowicki 2005) but the right shape for a ladder. [S] https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-9965.1993.tb00044.x ; https://www.sciencedirect.com/science/article/abs/pii/S0167715205001641
- **Correlation** — same-event strikes are one bet; quarter-Kelly on 3 correlated legs ≈ ¾-Kelly (median max DD 61%, p95 81% in MC) [M]. Size per *event* and per *sign of vol view*.

| Band | Sleeves ON | Sleeves OFF / paper | Sizing rule | Max concurrent positions | Do-or-die constraints |
|---|---|---|---|---|---|
| **A: $1–$500** | CORE (fractional index ETF + BTC/ETH DCA, Robinhood/Public; monthly); GRIND-PM as **calibration trades only** (1 contract, ≤ $1 each, ≤ $25/month budget, Webull or Kalshi) | CONVEX, MACRO-futures, GRIND-CRYPTO all paper; MACRO-ETF paper | CORE: fixed 100% of sleeve cash, no vol targeting (too small to rebalance); PM: fixed 1 contract | CORE ≤ 4 ETFs/coins; PM ≤ 5 open contracts | Daily loss lock **2%**; no trade whose fee > 5% of stake except the calibration budget; **cash floor 20%**; a $3,000 Rule 5.11 error would be ruinous → Kalshi/Webull orders capped at 1 contract and price band ±5¢ |
| **B: $500–$5k** | CORE (vol-targeted 60–70%); GRIND-PM measurement budget **10–20% of equity**, quarter-Kelly on shrunk edge, per-event cap 1%; MACRO via ETFs (regime-gated, ≤ 10%) | CONVEX paper until ≥ $2.3k, then one-lot rule at 4% cap (SMALL_ACCOUNTS §1); micro futures OFF (MES 1% move = 76% of $500); GRIND-CRYPTO off | CORE: 10% vol target on the risk sleeve; PM: f = ¼·Kelly(shrunk), min 1 contract, per-event worst-case PnL ≤ 1% equity; CONVEX: 1 contract, risk ≤ 4% | PM ≤ 10 events/day, ≤ 3 same-sign vol bets; CONVEX ≤ 1; MACRO ≤ 2 ETFs | Daily loss **−3%** halts everything but CORE; charter ladder −5/−10/−15%; cash floor 15%; PM per-order price band ±10¢ from Kalshi mid, notional cap $50/order |
| **C: $5k–$25k** | CORE; GRIND-PM (cap 20%); CONVEX SPY/XSP one-lot, 2%/trade (≥ $3.3–4.5k), 5–10% sleeve; MACRO: ETFs + **2YY/5YY/MCL micros** (≥ $5–10k), 1 contract each; Kalshi direct arm for maker-side PM once disclosed | MES/MGC/MNQ micros until $25k+; GRIND-CRYPTO off; SPX off (< $33k) | CORE 10–12% vol target; PM quarter-Kelly shrunk with **Grossman–Zhou scaling**: risk budget × (E − 0.85·HWM)/E; CONVEX fixed 2% (3% at score ≥ 97 per SMALL_ACCOUNTS Phase B only after 200 outcomes); MACRO fixed 1% risk per contract with ATR stop | PM ≤ 20 events/day; CONVEX ≤ 2; MACRO ≤ 3 contracts | Daily loss −3%; **rolling 20-day loss −6% halves PM and CONVEX**; cash floor 10%; PDT: use cash accounts or verify broker phase-in; settled-funds sizing for options |
| **D: $25k–$100k** | All of C + **MES/MGC** micros (1–3), SPX for CONVEX from ~$35–45k, XSP→SPX switch; PM router across Webull/Kalshi; CONVEX 5–10% | GRIND-CRYPTO still paper (fee tier); full-size futures off | PM: quarter-Kelly shrunk, per-event cap 0.5%, per-hour cap 2%, depth rule ≤ 10% of touch; CONVEX fixed 1.5–2%; MACRO 0.5–1% risk/contract, vol-scaled contract count | PM ≤ 40 events/day; CONVEX ≤ 3; MACRO ≤ 6 micros | Daily −2.5%; charter ladder; **max DD 15% → flat except CORE floor**; cash floor 10%; one Kalshi disclosure email per new arm before first live trade |
| **E: $100k–$1M** | Everything; ES/GC full contracts when target ≥ 10 micros (~$290k for ES); GRIND-CRYPTO canary only if 30-day volume reaches the 0.10%/0.04% tier and a measured edge > 20 bp exists | — | PM: capacity-bound, not Kelly-bound — hourly deployable capital ≈ $500–3k (BTC) / $5–20k (INX 4pm) regardless of equity, so **PM share falls from 20% to ~2–5% by $1M**; CONVEX ≤ 5% (≤ $50k), SPX; CORE/MACRO vol-targeted 10–12% with GZ scaling | PM ≤ 10% of touch depth per market; CONVEX ≤ 5; MACRO ≤ 10 full-equivalents | Daily −2%; DD ladder; cash floor 10%; per-broker concentration ≤ 40% of equity (SIPC/FCM segregation); leader-lease + kill switch drills quarterly |

Band-independent rules: (1) size from *settled, sleeve-assigned* cash, never from instant buying power; (2) a sleeve that has not passed its paper → canary gate trades at the band below its equity; (3) every sizing input (edge, vol, depth) is shrunk toward the conservative side — Meister's first-order result means a 5-pt probability error at 60–80% turns an optimal bet into a ruinous one [M].

---

## 5. Practical scaling mechanics — moving capital between brokers

| Broker | Deposit (ACH) | Instant buying power | Withdrawal | Holds / quirks | Source |
|---|---|---|---|---|---|
| Webull | ACH settles in ~4 business days (FAQ: 3–5) | up to **$1,000** (stocks only; not options; event BP = settled cash − provisional cash) | ACH ~2 business days; wire 1–2 days ($8 in) | new bank: 60-day wait; daily ACH caps $200k in / $50k out [T] | [S] https://www.webull.com/help/faq/1020-Instant-Buying-Power ; https://www.webull.com/blog/41-Deposit-and-Withdrawal-FAQs ; https://www.webull.com/help/faq/11053-Trading-Event-Contracts ; [T] https://wealthvieu.com/webull-deposit-limits/ |
| Robinhood | up to 5 business days | **$1,000 or 2× portfolio** (standard); **Gold: $5,000 or 3× portfolio**; options not tradable on instant funds | instant to debit card **1.75% ($1–$150)**; ACH free 2–5 days [T]; $50k/day [T] | proceeds of instant-funded buys restricted until ACH clears | [S] https://robinhood.com/support/articles/360026474671/bigger-instant-deposits/ ; https://robinhood.com/us/en/support/articles/instant-deposits-and-options/ ; https://robinhood.com/us/en/support/articles/instant-bank-transfers/ |
| tastytrade | ACH free, no minimum | **up to $10,000 per deposit, one per day** (Plaid-linked); manual-linked deposits held 5 business days; 4-day hold for 30 days after any chargeback | ACH free | cutoff times apply | [S] https://support.tastytrade.com/support/s/solutions/articles/43000502737 ; https://support.tastytrade.com/support/s/solutions/articles/43000435202 |
| Public | ACH up to $250k/day (may be $50k); debit $2,500 | instant BP by approval; 3-business-day hold; > $55k in 6 days → 6-day settlement | standard free 3–5 days, $50k/day; instant ≤ $5k/week, fee up to **3.5%** (min $1) | | [S] https://help.public.com/en/articles/9042678-what-are-the-limits-for-withdrawals-and-deposits ; fee schedule |
| Kalshi | min $10; ACH pulls in 1–4 business days, settles ~5, partial immediate credit; card/PayPal/Venmo/crypto instant (card fee ≤ 2% [T]); wire min $1,000 | n/a (funds credited as settled) | debit card instant (**$2,500/day** [T]); ACH free 1–4 business days | **Holds**: card/ACH deposits withdrawable once settled (+2 days if to a different method); PayPal/Venmo/wire/crypto **no hold**; **earnings withdrawable immediately once settled cash** | [S] https://help.kalshi.com/en/articles/13823798-bank-deposits ; https://help.kalshi.com/en/articles/13823801-security-holds ; https://help.kalshi.com/en/articles/13823791-transfers-faq ; [T] https://oddsassist.com/prediction-markets/kalshi-deposits-withdrawals-payout-speed/ |

Settlement of trades: equities and options **T+1** [S] Fidelity above; event contracts: Kalshi credits $1/$0 to cash at settlement (INX median 127 s after close, p99 22 min [M]); Webull "proceeds … available for withdrawal on the same day" [S] FAQ 11052; futures: daily variation margin, cash same day; crypto: immediate.

How the head should schedule rebalancing:
1. **Weekly cadence, 5-business-day horizon.** ACH is the only free rail everywhere and takes 3–5 days to settle; instant buying power is capped ($1k Webull/Robinhood, $5k Gold, $10k tastytrade) and unusable for options at Robinhood. Initiate transfers Monday so funds are settled by the following Monday's rebalance.
2. **Keep a float per arm** = 2 weeks of that arm's expected deployment (PM: hourly capacity × hours × 10 days; CONVEX: 3 one-lots) so no intent is ever refused for buying power while cash is in transit.
3. **Sweep profits out on the rail with no hold**: Kalshi earnings above deposits are withdrawable immediately (debit card, instant, $2.5k/day); route the CONVEX→GRIND→CORE sweep monthly, not daily.
4. **Never rebalance through a hold window**: Public's 6-day rule above $55k/6 days, Webull's 60-day new-bank rule, Robinhood's 5-day instant-deposit restriction — the head's transfer planner must carry these as calendar constraints, exactly like category hours.
5. **Cash accounts below $25k**: size CONVEX and any intraday equity trade from settled cash only (GFV rule); the T+1 lag means at most ~50% of the sleeve can be recycled per day.
6. **Withdrawal rails as the emergency brake**: on a −15% ladder hit, PM capital leaves Kalshi/Webull by instant debit the same day; brokerage cash sits as T-bills (Public $100 minimum) rather than moving.

---

## 6. Small-account realities ($1–$500) and small-account evidence

What is executable at $1–$500:
- CORE: yes — $1 fractional ETF/crypto at Robinhood, $5 at Webull/Public; commissions zero; the only cost is the crypto spread (0.5–1% per side).
- GRIND-PM: mechanically yes from $1 (one contract at 1–99¢; fractional 0.01 contracts on Kalshi API and some Webull markets), but a 50¢ contract carries a 4% fee and a $500 account can hold ~1,000 such contracts — enough for **data**, not for statistically meaningful PnL (t = 2 on a 3¢ edge ≈ 1,100 independent events; same-hour strikes are one event) [M].
- CONVEX: no — one SPY/XSP vertical risks $60–$150; one-lot floor $1.7–2.3k, and below ~$3.3k "it is a data logger with a login" [M].
- Micro futures: no — MES overnight margin $2,867 > account; even 2YY ($363 initial) exposes 1 bp = $10 = 2% of a $500 account per basis point.
- GRIND-CRYPTO: no — fee tier 0.95%/0.50%.

What the system should do instead at this band:
1. **Run the Kalshi depth recorder and every paper arm** — order-book history is not offered by Kalshi and vendors start mid-2026 at best; Phase 0's own recorder is the scarcest asset [M] `quant.md` §2.
2. **Calibration trades**: ≤ 1 contract per event, ≤ $25/month, chosen to test the fair-value engine across price buckets (not to earn); log fee, fill vs Kalshi mid, settlement latency.
3. **CORE DCA** of all remaining cash; no rebalancing below $500 (a 1% adjustment is < $5).
4. Graduate to band B only by deposit, never by expectation of compounding: at +7.8%/yr $5k → $25k takes 21 years [M].

Evidence on small-account expectancy:
- Prediction markets: Citizens JMP (via CoinDesk, Mar 2026): median trader staking < $100 per market **−26.8%**; > $500k stakers +2.6%; "the meaningful breakpoint is roughly **$10K** — below that, the median user loses money even before accounting for bots"; 823 wallets staking > $100k netted +$131M while the rest lost $131M (Bloomberg, Apr 2026); bots trade 89×/day vs 2.2 for humans; 0.1% of Polymarket accounts took 67% of profits (WSJ, May 2026). [T] https://www.turbinefi.com/blog/why-prediction-market-trades-get-picked-off-2026 ; https://www.turbinefi.com/blog/affordable-prediction-market-bots-small-accounts-2026 ; https://finance.yahoo.com/markets/crypto/articles/two-thirds-polymarket-profits-just-100500195.html
- Adverse selection: Bartlett & O'Hara (41.6M Kalshi trades) — makers earn ~2× per contract in single-name markets; retail over-buys YES [S] https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6615739 ; repo markouts: last-2-minute takers at 90–98¢ lost 2.6¢/contract after fees on BTC and 19.5¢ on the INX 4pm event [M].
- Equities: Barber, Lee, Liu, Odean (Taiwan, 1992–2006): < 1% of day traders predictably profitable, ~3% positive net of fees. [S] https://faculty.haas.berkeley.edu/odean/papers/Day%20Traders/Day%20Trade%20040330.pdf ; Chague, De-Losso, Giovannetti (Brazil, 2013–15): of those persisting > 300 days, **97% lost money**, < 1% earned more than minimum wage. [T] https://curvedtrading.com/articles/en/trading/day-trading-statistics/ (paper: SSRN 3423101, not fetched)
- Turbine's own small-account guide: a $99/month tool needs 238%/yr on a $500 account just to pay for itself; recommended "free tools and DIY Python ($5/month VPS)" until proven, paid infrastructure only at **$2,000–2,500+**. [T] https://www.turbinefi.com/blog/affordable-prediction-market-bots-small-accounts-2026

---

## 7. Top survival rules (with sources)

1. **Never exceed quarter-Kelly on a shrunk edge, sized per event** — full Kelly has a 50% chance of a 50% drawdown (P = x), quarter-Kelly 0.8% (x⁷); correlated legs multiply the effective fraction. Thorp §3.2/§7 https://gwern.net/doc/statistics/decision/2006-thorp.pdf ; MC [M].
2. **Shrink the probability, not just the fraction** — growth loss is first-order in probability error, second-order in fraction error. Meister 2024 https://arxiv.org/abs/2412.14144
3. **Grossman–Zhou floor at 85% of high-water mark**: risk ∝ (W − 0.85·HWM), which reproduces the charter's −5/−10/−15% ladder continuously. https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-9965.1993.tb00044.x
4. **Vol-target CORE and MACRO (10–12%)** — cuts left tails in every asset class, raises Sharpe in equities/credit. Harvey et al. 2018 https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538
5. **Price band ±10¢ and per-order notional caps on every event arm** — a cancelled error trade costs $3,000 with no malfunction exception for FCM customers (Kalshi Rule 5.11) [M] `docs/research/ops.md`; Webull's own cap is $50,000/order https://developer.webull.com/apis/docs/broker-api/event-contract-guidance/
6. **Size from settled cash, plan transfers a week ahead, treat sub-$10k PM as a measurement budget** — ACH settles in 3–5 days at every broker (Webull https://www.webull.com/help/faq/1020-Instant-Buying-Power ; Kalshi https://help.kalshi.com/en/articles/13823798-bank-deposits ); median sub-$10k prediction-market accounts lose (Citizens JMP via https://www.turbinefi.com/blog/why-prediction-market-trades-get-picked-off-2026 ).

---

## 8. Unverified / open items
- Whether Webull's $0.02 fee is prorated on fractional (0.01) contract fills; whether Kalshi rounds fees per order or per contract for fractional counts.
- Kalshi's official fee-schedule PDF (blocked by a Vercel checkpoint today); formula taken from repo docs and third-party write-ups.
- Kalshi maximum contracts per order (none stated in create-order-v2).
- tastytrade's own per-product futures margin table (help center returned a CSS error); CME margin page returned 503; the Webull table is used as the CME-derived reference.
- Exchange/NFA fees per micro contract at Webull and tastytrade; whether Webull's $0.25 is per side.
- Public's fractional minimum ($1 per disclosure vs $5 per help article).
- Webull daily ACH caps ($200k/$50k) and Kalshi debit-withdrawal cap ($2,500/day), card deposit fee (≤ 2%): third-party only.
- Whether each broker has finished its PDT phase-in (allowed until 2027-10-20).
- Robinhood market-maker-routing effective spread to the customer (only RH's $0.95/$100 receipt is documented).
- Chague et al. SSRN paper not fetched directly.
