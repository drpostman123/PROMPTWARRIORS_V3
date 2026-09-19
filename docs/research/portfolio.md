# Design brief: non-prediction-market sleeves for the Picador "master system"

Date: 2026-09-19. Scope: one US individual, accounts at Webull, Robinhood (Crypto API), tastytrade, Public, Kalshi direct (IBKR/Alpaca candidates). Context doc: `docs/PICADOR_ORCHESTRATION.md`. The prediction-market grind is out of scope (already researched); it appears here only as a capital bucket.

Legend: **[V]** = verified from a primary source this session (paper, official fee page, statute summary from practitioner); **[T]** = third-party summary only; **[U]** = could not be verified. Numbers quoted from PDFs were extracted locally with PyMuPDF from the linked files.

---

## 0. One-paragraph verdict

The only sleeve with a century of out-of-sample, net-of-cost evidence is trend / time-series momentum on diversified assets (Sharpe roughly 0.4 net of 2-and-20 fees and simulated costs, positive in 9 of the 10 worst 60/40 drawdowns since 1903) [V]. Vol targeting is well supported for equities and crypto but not for bonds/FX/commodities [V]. Systematic long options are a negative-expectancy budget line, not a return source: 5% OTM 1-month S&P puts lost about 6.4%/yr at 10% vol over 1985–2020 and the protective-put index carried −1.8%/yr alpha [V]. The options sleeve therefore must be capped as a premium budget with a hard kill rule, and it should be biased toward structures with documented positive expectancy (pre-earnings straddles on small/high-vol names, event convexity) rather than "buy cheap OTM calls". Macro should be expressed through micro futures at tastytrade (Section 1256, API, IRA-eligible) when the position is large enough to absorb commissions, and through ETFs otherwise, with a two-of-three trend filter deciding "when it matters". Cross-account wash-sale tracking and a written CPA opinion are the two tax items that can silently wreck a five-broker system.

---

## 1. Core portfolio (stocks + crypto)

### 1.1 Evidence

**Time-series momentum (TSMOM).** Moskowitz, Ooi and Pedersen (2012, JFE) documented TSMOM in 58 liquid futures/forwards 1965–2009: sign of the past 12-month excess return, held one month, each position scaled to 40% ex-ante annualized volatility (position = 40%/σ_t). The diversified strategy earned a gross annualized Sharpe of about 1.1 and performed best in extreme up and down markets (the "TSMOM smile") [V]. Paper: https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf ; SSRN https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2089463

**A Century of Evidence (Hurst, Ooi, Pedersen 2017, JPM).** 67 markets (29 commodities, 11 equity indices, 15 bonds, 12 FX) 1880–2016; equal-weighted combination of 1-, 3- and 12-month trend signals, 10% ex-ante portfolio vol target, monthly rebalance. Results shown net of simulated transaction costs (assumed 6x today's costs 1903–1992, 2x 1993–2002) and net of hypothetical 2-and-20 fees; positive in every decade; best decade 1973–1982; positive in 9 of the 10 largest 60/40 drawdowns (1907 panic through GFC). The authors' own "very conservative" forward assumption is a **0.4 Sharpe net of fees and costs**, under which a 20% allocation to trend leaves a 60/40's return unchanged, cuts vol 11% to 9%, raises Sharpe 0.34 to 0.44 and cuts max drawdown 62% to 53% [V]. PDF: https://www.chesler.us/resources/academia/A_Century_of_Evidence_on_Trend_Following.pdf ; AQR page: https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing ; SSRN https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026

**Realized, investable benchmark.** SG Trend Index Sharpe 0.42 from Jan 2000 to Jan 2023 [T, ReSolve]; 2022 +27.3% [T]. https://investresolve.com/how-to-replicate-trend-following-managed-futures/ ; https://www.alpha-week.com/2022-cta-index-performance-review . This matches the 0.4 planning figure: **do not underwrite the core above Sharpe 0.4–0.5 net.**

**Why the 2010s were poor (whipsaw / crowding).** Babu, Hoffman, Levine, Ooi, Schroeder, Stamelos, "You Can't Always Trend When You Want" (JPM 2020) decompose trend returns into (i) magnitude of market moves, (ii) capture efficiency, (iii) diversification across trends; the 2010s were a low-magnitude-of-moves decade, not a strategy breakdown [V abstract]. Hurst et al. also flag rising average pairwise correlations across markets as a drag [V]. Baltas and Kosowski show post-2008 underperformance is explained by higher pairwise correlations and that better vol estimators + trading rules cut turnover by more than one third without significant performance loss [V abstract]. https://alphaarchitect.com/trend-following-is-everywhere/ ; https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2140091

**Turning points / whipsaw fixes.** Goulding, Harvey, Mazzoleni, "Momentum Turning Points" (JFE 2023) and "Breaking Bad Trends" (FAJ 2024): classify Bull/Correction/Bear/Rebound by agreement of slow (12m) and fast (1m) signals and re-weight; dynamic trend earned 3.4%/yr vs 0.3% for static 12-month trend in the post-GFC expansion [T summary of paper]. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3489539 ; https://www.researchaffiliates.com/publications/journal-papers/837-breaking-bad-trends

**Dual momentum (equities).** Antonacci's GEM: 12-month lookback, relative momentum between US and ex-US equities, absolute momentum filter vs T-bills, else bonds. Third-party backtest 1986-02 to 2026-09: 12.3% CAGR, Sharpe 0.99, max DD −33.7%, vol 14.2% [T, gross of tax; monthly-signal timing luck not accounted for]. https://www.portfoliodb.com/portfolios/gem-dual-momentum ; https://medium.com/@garyantonacci_30463/extended-backtest-of-global-equities-momentum-dual-momentum-eb12902612e0 . Faber (2007, SSRN 962461): 10-month SMA timing across five asset classes 1973–2012, 10.5% vs 9.9% buy-and-hold with equity drawdown cut from 46% to under 10% (paper's own claim; later out-of-sample update 2008–2012 "performed well in real time") [V paper claims / T update]. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461

**Crypto momentum.** 
- Liu, Tsyvinski, Wu, "Common Risk Factors in Cryptocurrency" (JF 2022): a three-factor model (market, size, momentum) prices the cross-section; momentum is a priced factor at 1–4 week horizons [V abstract]. https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.13119
- Zarattini, Pagani, Barbon, "Catching Crypto Trends" (SSRN 5209907, 2025): ensemble of Donchian-channel trend models with different lookbacks + volatility-based sizing on a rotational top-20-liquidity universe (survivorship-bias-free, since 2015): CAGR ~30%, Sharpe 1.58, alpha +10.8–14%/yr vs BTC, **net of 0.10–0.50% fees** [T summary; SSRN blocked direct fetch]. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907 ; https://concretumgroup.com/catching-crypto-trends-a-tactical-approach-for-bitcoin-and-altcoins/
- Kumar and Jenefer (Zenodo, April 2026, not peer-reviewed): vol-scaled TSMOM, monthly rebalance, BTC/ETH/IBIT/FBTC/GLD/SPY 2018–2026-03; Sharpe 0.82 pre-ETF vs 1.22 post-ETF, but the difference is **not statistically significant (p = 0.58)**; transaction costs not discussed [V abstract]. https://zenodo.org/records/19671502
- Huang, Sangiorgi, Urquhart, "Cryptocurrency Volume-Weighted TSMOM" (SSRN 4825389): daily long-short with Sharpe 2.17 [T]; treat as gross, not implementable at retail fee levels. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4825389
- Correlation caveat: BTC 30-day correlation with S&P 500 reached 0.74 in March 2026; return correlation 0.13–0.34 over 30–500-day windows ending 2026-09-04 [T]. Crypto is not a diversifier in a stress month. https://cryptoslate.com/bitcoin-just-hit-a-decade-low-sp-500-correlation-but-the-daily-data-tells-a-different-story/

**Volatility targeting.** Harvey, Hoyle, Korgaonkar, Rattray, Sargaison, Van Hemert (JPM 2018): 60 assets since 1926, 10% target; vol targeting raises Sharpe for risk assets (equities, credit) but is negligible for bonds, FX, commodities; it reduces left-tail severity in all classes because tails happen at high vol when exposure is already small [V abstract]. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538 . Counter-evidence: Cederburg, O'Doherty, Wang, Yan (JFE 2020) show that for 103 equity strategies the Moreira–Muir vol-managed alphas are not implementable in real time; out-of-sample versions earn lower Sharpe than the unmanaged portfolios [V abstract]. https://www.ssrn.com/abstract=3357038 . Implication: use vol targeting for **risk control** (capping crypto's contribution), not as an alpha source.

**Rebalance frequency and timing luck.** Hoffstein, Faber, Braun, "Rebalance Timing Luck" (SSRN 3673910): for a monthly-rebalanced momentum portfolio the choice of rebalance day changed CAGR by up to ~350 bp; tranching into N staggered sub-portfolios reduces timing luck roughly 1/N with similar turnover [V abstract / T]. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3673910 ; https://blog.thinknewfound.com/2020/04/tranching-trend-and-mean-reversion/

**Tax drag.** Israel and Moskowitz, "How Tax Efficient are Equity Styles?": momentum's tax burden is similar to value's despite 5x turnover because it realizes short-term losses and long-term gains; tax-aware trading improves momentum most [V abstract]. https://www.ssrn.com/abstract=2089459 . Sialm and Sosner (FAJ 2017): long positions realize LT gains, shorts realize ST losses that offset [V abstract]. https://www.ssrn.com/abstract=2907195

### 1.2 Default rules (with the citation that justifies each)

| Rule | Default | Why |
|---|---|---|
| Universe (equity) | 6–10 liquid ETFs: SPY/VOO or IVV, QQQ, IWM, VEA or ACWX, EEM, plus TLT/IEF and GLD/IAU as absolute-momentum alternatives | Antonacci GEM, Faber GTAA breadth; ETF for tax lots and $0 commissions |
| Universe (crypto) | BTC, ETH; optionally the next 3–5 by 30-day median volume ≥ $2M, max 5 names | Zarattini et al. liquidity filter; Liu–Tsyvinski–Wu size factor |
| Signal | Average of three binary signals: 12m return > 0, 3m return > 0, price > 10-month SMA (equivalently 1/3/12-month blend) | Hurst et al. equal-weighted 1/3/12; Faber 10-month SMA; Goulding et al. slow/fast disagreement as a de-risk cue |
| Dual momentum | Among equity ETFs with positive absolute momentum, hold the top 2–3 by 12m return; if none, hold T-bills/short Treasuries | Antonacci; Faber |
| Position sizing | Target each position at (asset vol budget)/σ_60d, σ from EWMA or Yang–Zhang on daily bars; total core target vol 10–12% | MOP 40%/σ per instrument scaled down; Harvey et al. 10% target; Baltas–Kosowski efficient estimators |
| Max weights | Equity ETF ≤ 40% each, equity total ≤ 100% (no margin); crypto total ≤ 20% notional and ≤ 35% of core risk budget | Risk-parity logic (Asness–Frazzini–Pedersen) applied to a 60–80% vol asset; BTC–SPX correlation 0.3–0.7 |
| Rebalance cadence | Signals evaluated daily, **trades only when the target weight drifts by more than 20% relative or a signal flips**; portfolio split into 4 weekly tranches | Hoffstein tranching; Baltas–Kosowski turnover cut |
| Crypto trade sizing floor | Never trade crypto lots under $500 notional; minimum hold 7 days unless stop | Fee stack: 0.50–0.95% RH taker / 1% Webull spread each side |
| Expected outcome | Net Sharpe 0.4–0.6, worst drawdown 25–35% (equities-heavy) and up to 40% if crypto at the cap | AQR 0.4 net; SG Trend 0.42; GEM −33.7% |
| Failure-mode monitors | (a) Whipsaw counter: ≥4 signal flips in 6 months on an asset → halve its budget; (b) crowding: average pairwise 60d correlation of core assets > 0.6 → cut total core vol target 25%; (c) tax: harvest ST losses only into non-identical ETFs and log every lot across brokers | Babu et al.; Hurst et al. correlation warning; Israel–Moskowitz |

---

## 2. Options "convexity" sleeve

### 2.1 Evidence on drag

- **Volatility risk premium.** Index implied vol exceeds subsequent realized on average; systematic long-option strategies pay it. AQR, "Understanding the Volatility Risk Premium" (2018): https://www.aqr.com/Insights/Research/White-Papers/Understanding-the-Volatility-Risk-Premium [V page].
- **Israelov, "Pathetic Protection" (JAI 2019).** Cboe 5% Put Protection Index (PPUT) realized **−1.8%/yr alpha**; a simple ex-ante divested (equity + cash) portfolio earned 5.9% vs PPUT's 3.2% with ~10% more vol, a 60% better Sharpe; over 250-day windows the 1st-percentile drawdowns were −33.7% (protected) vs −32.9% (divested), and in the realistic-VRP simulation −34.2% vs −13.4%. Conclusion: "reducing their equity position is significantly more effective than buying protection" [V, local PDF]. https://images.aqr.com/-/media/AQR/Documents/Journal-Articles/Pathetic-Protection-JAI-Wint19.pdf ; SSRN https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2934538
- **AQR, "Tail Risk Hedging: Contrasting Put and Trend Strategies" (2020).** Jan 1985–Mar 2020, both scaled to 10% vol: Put (long 5% OTM 1-month S&P puts) geometric mean **−6.4%/yr, Sharpe −0.61, max DD −92%, skew +3.5, equity correlation −0.64**; Trend (1/3/12-month, 4 asset classes) +8.7%/yr, Sharpe 0.84, max DD −35%, equity correlation 0.08. Puts win in fast crashes (1987, 2020 Q1), trend wins in slow drawdowns (2000–02, 2007–09, 2022) [V, local PDF]. https://images.aqr.com/-/media/AQR/Documents/Insights/White-Papers/AQR-Tail-Risk-Hedging-Contrasting-Put-and-Trend-Strategies.pdf ; "Chasing Your Own Tail (Risk), Revisited": https://www.aqr.com/Insights/Research/White-Papers/Chasing-Your-Own-Tail-Risk-Revisited
- **Lottery-like calls.** Boyer and Vorkink (JF 2014): option returns fall steeply in ex-ante skewness; the spread between low- and high-skew single-stock options is 10–50% **per week**, i.e., far-OTM short-dated single-stock calls are the worst expected-return instruments in the market [V abstract]. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1787365 . Constantinides, Jackwerth, Savov (RAPS 2013): leverage-adjusted S&P 500 call and put portfolio returns decrease in strike/price over 1986–2010, so OTM index calls also underperform ATM on a leverage-adjusted basis [V abstract]. https://www.ssrn.com/abstract=1491469 . Coval and Shumway (2001): zero-beta ATM straddles earn negative returns [T]. Bali and Murray (JFQA 2013): no significant skew–return relation for a hedged CALL asset, negative for daily-rebalanced calls [T].

### 2.2 Evidence of positive expectancy (where a small sleeve can live)

- **Pre-earnings straddles.** Gao, Xing, Zhang (JFQA 2018): buying ATM straddles 3 days before an earnings announcement and closing on the announcement date returned **+3.34%** on average (highly significant), stronger for smaller, higher-vol, higher-kurtosis, less-liquid names; general single-stock straddles lose money [V abstract]. https://doi.org/10.2139/ssrn.2204549 . Caveats: a student replication reports negative returns for S&P 500 names 2011–2021 [T, low quality]; wide retail spreads on exactly the small names where the effect lives; ORATS reports large quarter-to-quarter swings (2026 Q1 average +45%) [T]. https://bsic.it/straddling-outside-and-into-earnings-part-ii-2/ ; https://orats.com/blog/earnings-straddles-strong-season-2026
- **FOMC.** Lucca–Moench pre-FOMC drift (+49 bp in the 24h before announcements, 1994–2011) **disappeared after 2015** (Kurov, Wolfe, Gilbert, Finance Research Letters 2021) [V abstract]. Do not build an FOMC-day long-option play on this literature; if traded at all, express FOMC/CPI views in the Kalshi grind, not options. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3134546
- **Budgeted, always-on tail hedges.** Bhansali and Davis, "Offensive Risk Management" (JPM 2010): tail hedges as a strategic budget; expected net benefit 0.15%–2.2%/yr across 80–100% strikes and 1–5% ERP assumptions, conditional on price paid [T]. https://www.ssrn.com/abstract=1573760 . Cboe VXTH: S&P 500 plus 0–1% in 1-month 30-delta VIX calls, sized by VIX level [V methodology]. https://cdn.cboe.com/api/global/us_indices/governance/Cboe_VIX_Tail_Hedge_Index_Methodology.pdf . Universa's published pitch is 96.67% S&P / 3.33% tail fund [T]; Taleb barbell 85–90% safe / 10–15% speculative [T]. https://bsic.it/wp-content/uploads/2020/09/Tail-risk-hedging_BSIC.pdf
- **Trend + convexity.** No peer-reviewed paper found that shows long-dated OTM calls filtered by trend signals beat the underlying on a leverage-adjusted basis **[U]**. The honest reading: a call bought on a trend-confirmed asset is a leveraged trend position with a VRP tax; the "rocket" is the trend, the option is the financing.

### 2.3 Recommended sleeve design

- **Size cap:** premium budget **≤ 3% of total equity per rolling 12 months**, with **≤ 1% at risk in any single expiry cycle** and sleeve notional (delta-equivalent) ≤ 15% of equity. Rationale: Universa 3.33%, VXTH ≤ 1%, Bhansali "budgeted" framing; at 3%/yr a five-year dry spell costs ~14% cumulative [T], survivable for the core.
- **Structure set (in priority order):**
  1. *Event straddles/strangles* on small/mid caps with high past-surprise vol, bought T-3, closed at the announcement, only when the bid-ask on the straddle is < 5% of its price (Gao–Xing–Zhang) — this is the only structure with published positive expectancy.
  2. *Long-dated (6–18 month) call spreads or call ratio backspreads* (sell 1 near-ATM, buy 2 OTM for ≤ 0.3% of equity net debit) on assets where the core's 2-of-3 trend signal is long and IV rank < 30 — uses the trend edge and buys convexity while paying less VRP than outright calls. Constantinides et al. shows OTM legs underperform, so keep the long strikes ≤ 15% OTM.
  3. *Crash convexity* only as VIX call spreads (30-delta, 1–2 months) sized 0.25–0.5% when VIX < 18, per VXTH, or XSP put spreads ≤ 5% below, because Israelov shows outright protective puts destroy value.
  4. **Never:** short-dated far-OTM single-stock calls (Boyer–Vorkink), systematic 1-month protective puts (Israelov, AQR Put strategy −6.4%/yr).
- **Prefer 1256 instruments** (XSP, SPX, VIX, NDX, options on micro futures) for anything index-level: 60/40 tax, no wash-sale bookkeeping.
- **Kill rule:** stop the sleeve for the rest of the calendar year when either (a) cumulative sleeve P&L over the trailing 12 months < −(annual budget) **and** no single trade returned > 3x its premium, or (b) premium spent YTD reaches the budget. Re-open only after a written review of realized vs implied move by structure. Per-structure: retire event straddles if the trailing-40-trade average return < 0 after costs.

---

## 3. Macro sleeves (commodities, Treasuries/yields, FX)

### 3.1 Instruments and costs

| Exposure | ETF (expense ratio, tax form) | Micro future (CME) | Notes |
|---|---|---|---|
| Gold | GLD 0.40%, IAU 0.25%, GLDM 0.10%; grantor trust, 1099-B, **LT gains taxed as collectibles up to 28%** [T/V practitioner] | MGC = 10 troy oz [V] | Futures roll ≈ carry (rates minus lease), currently a cost; 1256 |
| Crude | USO 0.60% ER, partnership **K-1**, 1256 60/40 passed through; **contango roll drag: lagged spot by roughly half since 2014** [T] | MCL = 100 bbl, monthly roll [V] | Use MCL for trades under 2 months; USO only if you need the ETF wrapper |
| Long Treasuries | TLT 0.15%, interest ordinary (state-exempt), capital gains normal [T] | 10Y micro yield future (ticker 10Y) [V spec exists]; /ZN standard | tastytrade lists 2YY/5YY/10Y/30Y yield futures [V]; the CME yield-future multiplier must be confirmed on the spec page (search summaries conflicted: "$10 × yield, tick 0.005 = $0.50" vs $1,000 × index) **[U]** https://www.cmegroup.com/markets/interest-rates/us-treasury/micro-10-year-yield.contractSpecs.html |
| US dollar | UUP 0.75–0.77%, **K-1**, 60/40 MTM [T] | M6E = €12,500 (short M6E ≈ long USD vs EUR) [V] | Micro FX at tastytrade; Webull futures API also lists currencies [T] |
| Equity index | SPY/IVV 0.03–0.09% | MES = $5 × S&P, M2K = $5 × Russell [V] | MES quarterly roll |
| Bitcoin | IBIT/FBTC ~0.25% (crypto ETFs are property, not 1256) | MBT = 0.1 BTC [V], 1256 | MBT is the only 1256 route to BTC |

Sources: GLD fee/collectible: https://finance.yahoo.com/markets/commodities/articles/gld-0-40-fee-quietly-220414319.html , https://greentradertax.com/gld-tax-treatment-revisited-wash-sales-section-475-and-the-section-1256-debate/ ; USO roll drag: https://247wallst.com/investing/2026/05/26/usos-front-month-oil-strategy-has-lagged-crude-oil-itself-by-half-since-2014-and-the-roll-cost-is-the-reason/ ; USO K-1/1256: https://tradelog.com/education/etfs-etns/ ; UUP K-1: https://www.invesco.com/us/en/accounts/tax-center/etf-tax-center.html ; micro specs: https://www.cmegroup.com/markets/equities/micro-emini-equity.html , https://www.cmegroup.com/markets/energy/crude-oil/micro-wti-crude-oil.contractSpecs.html , https://www.cmegroup.com/markets/cryptocurrencies/bitcoin/micro-bitcoin.contractSpecs.html , https://www.cmegroup.com/markets/fx/g10/e-micro-euro.contractSpecs.html ; tastytrade product list: https://tastytrade.com/learn/trading-products/futures/available-futures-products/

**Futures commissions (official tastytrade page, "Last updated July 30, 2026") [V, local PDF]:** futures $1.00/contract per side + $0.30 clearing + exchange + NFA; **micro futures $0.75/contract per side + $0.30 clearing + exchange + NFA**; options on futures $1.25, on micro futures $0.75, both + $0.30 clearing; NFA fee $0.02 per round turn. https://tastytrade.com/commissions-and-fees/ . **Webull futures:** third-party sites quote $0.25/side on micros plus ~$0.55/contract exchange+NFA for MES; the official pricing page fetched this session does not list futures commissions **[T/U]**. https://brokerchooser.com/broker-reviews/webull-review/micro-emini-sp500-futures-fees ; https://www.webull.com/pricing

**Break-even ETF vs micro future (rule of thumb).** A round trip in MGC at tastytrade is about $2.10 commission/clearing + ~$1.00 exchange/NFA ≈ $3.10 on ~$35k notional (0.009%), versus GLD at 0.40%/yr. Futures win for any hold at any size once the account can margin one contract; the ETF wins only when the required exposure is smaller than one micro contract or the account cannot carry futures (Webull IRA, Public). For crude, MCL's monthly roll costs the same commission again each month but avoids USO's structural drag and K-1.

**Kalshi economics/financial contracts** (KXFED, KXCPI, etc.) exist and are liquid for Fed decisions [V listing]; fee formula and usage were covered by the earlier prediction-market research and are not redone here. https://kalshi.com/category/economics/fed

### 3.2 Section 1256 vs ETF capital gains

- 1256 contracts = regulated futures, options on futures, and cash-settled broad-based index options (SPX, SPXW, XSP, NDX, RUT, VIX): 60% long-term / 40% short-term regardless of holding period, marked to market at 12/31, Form 6781, **wash-sale rules do not apply** [V practitioner]. https://support.tastytrade.com/support/s/solutions/articles/43000561348 ; https://www.irs.gov/forms-pubs/about-form-6781
- ETF options (SPY, QQQ, GLD, TLT) are **not** 1256 [V practitioner]. https://staxinvesting.com/blog/section-1256-and-the-6040-tax-treatment-of-index-options
- For a high-turnover sleeve, 60/40 lowers the top blended federal rate from 37% to ~26.8% [T]. The macro and crash-convexity sleeves should therefore default to XSP/SPX/VIX options and micro futures rather than SPY/GLD/TLT options.

### 3.3 "When it matters" rule (regime/trend filter)

Trade a macro market only when **at least 2 of 3** hold: (1) 12-month excess return sign, (2) 3-month sign, (3) price vs 10-month SMA agree (Hurst et al. 1/3/12; Faber). Add a **magnitude gate**: the asset's trailing 3-month absolute return divided by its trailing 12-month vol must exceed 0.5 (a trend-strength z-score), because Babu et al. show trend returns come from large moves; skip small-magnitude signals. Add a **regime overlay** for commodities: Man Institute (Neville, Draaisma, Funnell, Harvey, Van Hemert, "The Best Strategies for Inflationary Times", 2021) found trend was the most reliable active strategy in the eight US inflation surges since 1926 and a diversified commodity basket had a 100% hit rate of positive real returns in those episodes [T]; so when trailing 12-month CPI is rising and above 3%, allow the commodity budget to double (still inside the sleeve cap). https://man.com/maninstitute/best-strategies-for-inflationary-times . Position sizing: vol-scale to a **sleeve target of 3% of portfolio vol**, one micro contract minimum, no pyramiding within a month. Exit when the 2-of-3 condition fails or the contract nears roll with the signal weakening.

---

## 4. Broker fit per sleeve

### 4.1 Verified fee facts

**tastytrade (official schedule dated 2026-07-30) [V]:** stocks/ETFs $0 + $0.0008/share clearing; stock options $1.00 to open, $0 to close, $0.10 clearing per contract, ORF $0.02, **$10 cap per leg**; broad-based index options $1.00 open/$0 close + $0.10 clearing + proprietary index fee (SPX $0.60, RUT $0.18, VIX $0.35, XSP $0.00/$0.07, NDX $0.25, CBTX $0.50, MBTX $0.25), **no cap**; futures $1.00/side, micro $0.75/side, both + $0.30 clearing + exchange + NFA; crypto **1.00% per side (BTC/ETH 0.75%)**, $1 minimum; event contracts $0 commission + $0.01 clearing + $0.01 regulatory/exchange; margin base rate 10%. https://tastytrade.com/commissions-and-fees/

**Robinhood Crypto (fee schedule dated 2026-06-22) [V, local PDF]:** market-maker routing: no commission (spread only); exchange routing (EDX, Bitstamp) tiered on trailing 30-day volume: $0–10K taker 0.95% / maker 0.50%; $10–50K 0.75% / 0.35%; $50–250K 0.25% / 0.125%; $250–500K 0.15% / 0.075%; $500K–1M 0.125% / 0.06%; $1–5M 0.10% / 0.04%; $5–10M 0.04% / 0.02%; $10–25M 0.03% / 0.01%; $25M+ 0.03% / 0.00%. Only API **v2 ("place crypto orders with fee tiers")** orders count toward tier volume [V support page]. https://cdn.robinhood.com/assets/robinhood/legal/rhc-fee-schedule.pdf ; https://robinhood.com/us/en/support/articles/crypto-api

**Public (rebate terms and index fee schedule fetched 2026-09) [V, local PDFs]:** stocks/ETFs $0; equity/ETF options rebate: platform $0.06–$0.18 per contract by tier (SPY/QQQ/IWM $0.06–$0.10), **API $0.06 (Tiers 1–3) / $0.10 (Tier 4)**; 1,000–4,999 contracts/month lifts you to Tier 2 temporarily; **index options pay only the exchange pass-through: XSP $0.00 for executions ≤ 9 contracts, $0.07 above; SPX $0.57/$0.66; SPXW $0.50/$0.59; NDX $0.25–$0.75; VIX $0.10–$0.45; XND and NANOS $0.00; CBTX $0.50**. API supports EQUITY, OPTION (single and multi-leg), INDEX options, CRYPTO, BOND (corporates and treasuries); API crypto fees are volume-tiered "as low as 0.10%" (base tier **[U]**). https://public.com/disclosures/rebate-terms ; https://public.com/disclosures/index-options-exchange-fees ; https://public.com/api/docs

**Webull [V official pages]:** stocks/ETFs/equity options $0 commission; **index options $0.50 per contract** ("certain index options"); $0.10 per contract on oversized option orders; **crypto: 1% spread (100 bp) on both buy and sell**, stablecoins exempt, max $100k per trade; futures commissions not on the pricing page (third-party: $0.25/side micro) **[T]**. OpenAPI covers stocks, options, futures (indices, rates, FX, ags, metals, energies, crypto; no OTO/OCO combos), crypto and event contracts, with paper trading for all since July 2026 [V docs/press]. https://www.webull.com/help/faq/11091-Fees-and-Limits ; https://www.webull.com/pricing ; https://developer.webull.com/apis/docs/trade-api/futures/

**Crypto cost comparison for the core (round trip, $5k lot):** Webull spread 2.0% ($100); tastytrade BTC/ETH 1.5% ($75); Robinhood exchange routing at base tier as maker 1.0% ($50), as taker 1.9% ($95); once trailing 30-day volume passes $50k, maker 0.25% ($12.50). **Robinhood v2 maker limit orders are the cheapest official-API crypto route at every volume level**; Webull is a backup only.

### 4.2 Mapping

| Sleeve | Primary arm | Why | Backup |
|---|---|---|---|
| Core equity ETFs | **Public** (API, $0, fractional, up to 16h/day) | Cheapest and API-complete; bonds/T-bills for the absolute-momentum leg in the same account | Webull OpenAPI |
| Core crypto | **Robinhood Crypto API v2, post-only limit orders** | 0.50% maker at base tier, 0.125% above $50k/30d; no spread mark-up | Webull (1% spread) |
| Options: event straddles, equity call spreads | **Public** (rebate $0.06–0.10 via API; $0 commission) | Positive per-contract economics | tastytrade ($1 open, $10/leg cap makes large multi-contract legs cheap) |
| Options: XSP/SPX/VIX convexity | **Public for XSP (free ≤ 9 contracts) and VIX**; tastytrade for large SPX legs | Public XSP $0 vs tastytrade $1.10+; SPX similar at both (~$0.57–0.66 vs $1.70) | Webull $0.50/contract index options |
| Macro futures (MGC, MCL, 10Y, M6E, MES) | **tastytrade** ($0.75 + $0.30 micro, mature Open API with OTO/OCO, IRA-eligible) | Only arm with futures + futures options + IRA | Webull futures API (possibly cheaper commission, unverified; no combo orders) |
| Macro via ETFs (small size) | Public or Webull | $0 | — |
| Grind (Kalshi) | per earlier research | — | — |

---

## 5. Portfolio-level plumbing

### 5.1 Allocation (barbell)

Default split of total equity: **Core 80%** (equity trend/dual momentum ~60% of equity by capital, crypto trend ≤ 20% by capital and ≤ 35% of core risk), **Macro 5%** capital (3% vol budget, futures margin is small so most of the 5% sits in T-bills), **Convex options 3%/yr premium budget** (capital parked in T-bills), **Grind box 10%** (cap, fed by sweeps), cash/T-bill buffer 2%+. This is Taleb's barbell shape (85–90% safe/robust, 10–15% convex) with the "safe" leg replaced by a trend-filtered core, which AQR's evidence says is the better slow-crash hedge; the fast-crash hedge is the small VIX/XSP budget.

### 5.2 Risk budgeting by volatility contribution

- Portfolio target vol 10–12% (Harvey et al.; AQR century paper's 10%). Allocate risk, not dollars: crypto at 70% vol gets ~1/6 the notional of an equity ETF at 12% vol for the same risk share (Asness, Frazzini, Pedersen, "Leverage Aversion and Risk Parity", FAJ 2012). https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1990493
- Sizing law: use **fractional Kelly at one-half or less**. MacLean, Ziemba, Blazenko (1992) and MacLean–Thorp–Ziemba: half-Kelly gives ~75% of full-Kelly growth with ~half the variance; full Kelly's drawdowns are intolerable with estimated (not known) edges. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1797366 . With Sharpe 0.4–0.5, full-Kelly leverage on the core is ~3–4x vol-units, so half-Kelly caps total portfolio vol near 15–20%; the 10–12% target is ~one-third Kelly, deliberately conservative given parameter uncertainty and fee drag.
- Options and grind are **budgets, not Kelly bets**: their edge estimates are too noisy; size by max loss.

### 5.3 Profit sweep

1. Options sleeve: on any closed trade with realized profit, sweep **50% to the grind box (until the grind box is at its 10% cap), 50% to core**. The sleeve's own capital is only ever refilled to the annual budget on January 1 (or on a 3% budget reset after a > 3x winner if the kill rule is not tripped).
2. Grind box: monthly, sweep everything above the 10% cap to core. Grind never refills options.
3. Core never funds options beyond the annual budget; macro margin is drawn from the T-bill buffer.
4. Rationale: profits flow from the least-reliable sleeve to the most-reliable, and the convex sleeve cannot compound its own leverage (the classic blow-up path).

### 5.4 Correlations to expect

Trend vs equities ≈ 0 (0.08 in AQR 1985–2020) but positive in equity bull markets and negative in slow bears [V]; long puts −0.64 [V]; BTC vs S&P 0.13–0.74 depending on window [T]; commodities trend vs equities low, positive in inflation shocks [T]; grind vs everything **[U]**, assume 0 but watch for macro-event days when the grind holds CPI/Fed contracts and the macro sleeve holds 10Y micros (same bet twice; the head's ownership lock should treat "Fed decision" as one event across arms).

### 5.5 Drawdown de-risking ladder

Grossman and Zhou (1993) formalize keeping wealth above a fraction α of its running maximum; AQR notes that all such rules (drawdown control, vol targeting, CPPI) sell after losses and therefore cost return in V-shaped recoveries [V]. https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-9965.1993.tb00044.x . Default ladder, measured on total equity vs 12-month high-water mark, evaluated daily, changes applied at the next tranche:

| Drawdown | Action |
|---|---|
| −8% | Options premium budget cut 50% for the year; macro vol budget halved |
| −12% | Core gross exposure × 0.75; crypto cap 20% → 10%; grind new-position size halved |
| −16% | Core × 0.50; macro, options and grind paused (existing positions run to exit rules) |
| −20% | Core × 0.25 unlevered, everything else flat; manual review before any re-risk |
| Re-risk | One rung per new 3-month equity high; never more than one rung per 20 trading days |

The ladder is a survival constraint, not an alpha rule: with a 0.4–0.5 Sharpe core at 10–12% vol, a −20% drawdown is a roughly once-per-decade event (GEM −33.7% unmanaged; AQR trend −35% at 10% vol).

### 5.6 Published multi-sleeve retail frameworks (for reference)

Return Stacked (Newfound/ReSolve) 100/100 stock + managed-futures products (RSST, RSBT) show the institutional version of "core + trend overlay" packaged for retail [T]. https://www.returnstackedetfs.com/ ; Taleb barbell and Universa 3.33% tail sleeve [T]; Faber GTAA/Trinity [T]. No peer-reviewed study of a retail core + options-convexity + prediction-market structure was found **[U]**; the sweep and ladder above are engineering choices justified by the component evidence, not a tested system.

---

## 6. Tax and account structure (US individual, high turnover, five brokers)

- **Wash sales cross all accounts.** §1091 applies to substantially identical stock/securities bought within 30 days before or after a loss sale in **any** account, including IRAs; Rev. Rul. 2008-5 makes a loss washed by an IRA purchase **permanently** disallowed (no basis step-up) [V practitioner]. https://www.kitces.com/blog/irs-shuts-down-wash-sale-evasion-technique/ . Brokers report wash sales only within one account, so the head's journal must run a cross-broker lot ledger and the ETF universe must be partitioned: **the same ticker is never traded in two arms**, and loss-harvest swaps go to non-identical ETFs (SPY→IVV is widely treated as risky; use SPY→VTI or a different index).
- **Crypto:** as of September 2026 §1091 does not apply to digital assets (property, not securities); H.R. 9172 (introduced 2026-06-08) would extend wash-sale and constructive-sale rules and is drafted to apply to dispositions after its introduction date, so a 2026 harvest could be caught retroactively **[T, pending legislation]**. https://gordonlaw.com/learn/crypto-wash-sale-rule-in-2026-is-the-loophole-finally-closed/ ; https://chainwisecpa.com/crypto-wash-sale-2026/
- **1256 vs 1099-B:** futures, futures options and SPX/XSP/NDX/VIX options come on a 1256 statement (Form 6781, 60/40, MTM, no wash sales); stocks, ETFs and ETF/equity options come on 1099-B with wash-sale adjustments; K-1s from USO/UUP arrive in March and delay filing [V practitioner]. Event contracts: forms and characterization unresolved (see `docs/research/ops.md`).
- **Trader Tax Status / §475(f).** TTS is a facts-and-circumstances test; Green's planning benchmarks: ~720 trades/yr counting buys and sells, ~4 trading days/week, average holding period under 31 days, ~4 hours/day [T]. A monthly-rebalanced core will **not** qualify on its own; the options and grind sleeves might. §475(f) MTM must be elected by **April 15** of the election year for existing individuals (with the prior-year return or extension), no late relief; it converts gains/losses to ordinary, removes wash-sale and $3,000 loss-limit problems, and forfeits LTCG rates; it can be limited to securities so that 1256 contracts keep 60/40 [T]. https://greentradertax.com/trader-tax-center/trader-tax-status/how-to-qualify/ ; https://greentradertax.com/trader-tax-center/trader-tax-status/section-475-mtm-accounting/ ; https://terms.law/Trading-Legal/guides/section-475f-election.html . Recommendation: do not elect 475 in year one; revisit after a full year of logs shows whether TTS is defensible and whether ordinary-loss treatment is worth losing 60/40 on the sleeves that make money.
- **IRA use.** tastytrade IRAs are "limited margin" and allow futures, futures options and defined-risk option spreads with "IRA The Works" approval [V help pages]; Webull IRAs allow options (covered calls, cash-secured puts) but **no futures or crypto** [T]; Public IRAs allow options up to Level 2 only [V FAQ]. https://tastytrade.com/learn/accounts/retirement-accounts/what-is-an-ira/ ; https://help.public.com/en/articles/11879527-can-i-trade-options-in-my-ira . Kalshi/event contracts in an IRA: no offering found **[U]**. Best use: put the **high-turnover equity core (dual momentum with monthly flips)** in a tastytrade or Public IRA where turnover is tax-free and wash-sale bookkeeping vanishes, keep crypto, futures and options in taxable (1256 already efficient; crypto has no wash-sale rule today), and never hold the same ticker in the IRA and a taxable arm.
- **Get a written CPA opinion** before scaling on: (i) characterization of event contracts; (ii) whether 475 should be elected; (iii) the cross-account wash-sale ledger design. This was already in the orchestration compliance checklist (§4 item 5).

---

## 7. Top risks (ranked)

1. **Trend drought / whipsaw decade.** 2009–2019 style low-magnitude markets can leave the core near zero real return for years while paying costs (Babu et al.; SG Trend 0.42 lifetime Sharpe).
2. **VRP drag turns the options sleeve into a permanent −3%/yr tax.** AQR Put −6.4%/yr at 10% vol; PPUT −1.8% alpha. Only the budget cap and kill rule bound this.
3. **Crypto fee stack.** 1–2% round trips (Webull/tastytrade) on a 60–80% vol asset traded monthly can erase the whole momentum premium; only Robinhood v2 maker orders keep it under 1%.
4. **Correlation spikes.** BTC–S&P 0.74 in March 2026; pairwise correlations rising within the core; every sleeve except the VIX budget can lose on the same day.
5. **Tax leakage across five brokers.** Cross-account wash sales (permanent with IRAs), K-1 ETFs, pending crypto wash-sale law, unresolved event-contract characterization.
6. **Pro-cyclical risk controls and leverage.** Vol targeting and the drawdown ladder sell after losses (Cederburg et al. show vol-managed equity fails out of sample); futures margin plus a −20% rung can lock in the bottom. Mitigate by slow re-risking and by keeping total portfolio vol at one-third Kelly.

## 8. Items not verified this session

- Webull official futures commission per contract (only third-party $0.25/side).
- Public crypto fee at the base API tier; Public bond commissions.
- CME Micro 10-Year Yield future multiplier/tick (spec page not fetched; summaries conflicted).
- Whether any broker offers event contracts inside an IRA.
- Any peer-reviewed evidence that trend-filtered long-dated OTM calls beat the underlying leverage-adjusted.
- Zarattini et al. numbers were read from secondary summaries (SSRN blocked the fetch).
- Correlation of the prediction-market grind with the other sleeves.
