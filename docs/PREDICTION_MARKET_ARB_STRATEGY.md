# Weevil — Prediction-Market Arbitrage Strategy & Build Plan (v0.2, 2026-09-18)

> v0.2: Weevil is greenfield and forks `warproxxx/poly-maker` (see `WEEVIL_REPO_SURVEY.md`).
> Nothing in this plan depends on any other code in this repository.

Goal as stated: a 24/7 automated system that makes a couple of dollars per trade,
thousands of times, by finding pricing mistakes in prediction markets that expose
trading APIs. This document is the research-backed plan: what the mistakes actually
are in September 2026, which of them still pay after the 2026 fee changes, what the
open-source ecosystem already provides, and the architecture and phased rollout we
should build. Everything quantitative below is sourced (links at the bottom); the
first build phase exists precisely to replace those third-party numbers with our own.

---

## 0. The one-paragraph verdict

The "risk-free spread between Kalshi and Polymarket" story that most GitHub repos
sell is mostly dead as a *taker* strategy in 2026: fees on both venues now sum to a
~4–4.5 % round-trip floor at mid prices, cross-venue windows have collapsed to about
2.7 seconds, the one public dataset that walks the book shows a **median executable
spread of $0.0006 at $100 size**, and the two venues have already resolved the "same"
event in opposite directions. The money that *is* still being chipped away in cents,
thousands of times a day, is being made by **maker-side set completion inside
Polymarket** (buy YES+NO < $1, or complete a NegRisk basket, then merge/convert
back to USDC immediately), funded on top by Polymarket's maker rebates and liquidity
rewards. The academic Polymarket study puts $39.6 M of realized rebalancing profit in
one year, concentrated in a handful of bot wallets doing 2,000–4,500 fills each. That
is the strategy shape that matches your brief, and it is where we should start.
Cross-venue and Kalshi field-sum stay in the plan as **scanners first, executors
later**, gated on our own recorded data proving they clear fees.

---

## 1. Venue landscape (what has an API, what it costs)

| Venue | Access | Taker fee | Maker fee / rebate | Notes |
|---|---|---|---|---|
| **Polymarket (intl, polymarket.com)** | CLOB REST + WS, Gamma (discovery), Data API; py-clob-client / TS client; gasless via relayer | `shares × rate × p(1−p)`; rate by category: crypto 0.07, sports 0.05, finance/politics/tech/mentions 0.04, econ/culture/weather/other 0.05, **geopolitics 0**. 15-min crypto markets: dynamic, ≈3.15 % at 50¢ | **Makers pay 0** and receive daily rebates of 15–25 % of taker fees they were matched against; separate Liquidity Rewards pay for resting near mid | Rate limits are global Cloudflare buckets (~9,000 req/10 s CLOB; POST /order 3,500/10 s). Geo-blocked in some regions ("IP gate"). Settlement via UMA: ≥2 h, disputes can take days. USDC withdraw ~4 h median |
| **Polymarket US (regulated DCM, polymarket.us)** | Separate REST + WS API, Ed25519 auth, own SDKs | Uniform theta 0.06 (max $1.50 / 100 shares at 50¢); geopolitics & economics 0 | 25 % maker rebate | Separate liquidity pool from intl. Parlays live since Aug 2026. US-only; state-level fights ongoing (NV, TN, NY, MA) |
| **Kalshi (CFTC DCM)** | REST v2 + WS (orderbook_delta, ticker, trade, fill…), FIX for high tiers | `ceil(0.07 × p(1−p) × 100)/100` per contract → 2¢ at 50¢, ~1¢ at the tails, rounded **up per order** | ≈ ¼ of taker (some series exempt); Liquidity Provider / Volume Incentive programs exist but MM agreements are gated | Token-bucket rate tiers: Basic 200 read / 100 write tokens/s, most calls cost 10 → ~10 write ops/s to start; tiers unlock by volume share (halved in June 2026). Exchange is **sharded** (crypto, tennis, baseball, commodities, basketball on separate instances). USD only: ACH 1–3 days, no crypto rails. Position limits per market |
| **Limitless (Base)** | REST + WS, trading | — | — | Fast-settling crypto/macro. Secondary target |
| **Opinion (BNB)** | CLOB + Python SDK | — | — | #3 by volume. Secondary target |
| **Manifold** | Open API | play money | — | Prototyping only |

Unified layers exist (**pmxt** — 2.1k★, "CCXT for prediction markets", Python/TS, 15+
venues; **dr-manhattan** — Python, 5 venues, WS, mixin architecture). Recommendation:
use them for **discovery and cross-venue matching**, not for the execution hot path.
The documented failure modes (side/index confusion, `1 − yes_ask ≠ no_ask`, fee
rounding, tick sizes, neg-risk flags) are exactly what abstraction layers hide.

---

## 2. Where the "mistakes" are — evidence by category

### 2.1 Polymarket intra-venue set completion (Tier S — build first)

Mechanics. Every binary condition has YES and NO tokens redeemable for exactly $1
total; a full set can be **merged** back into USDC instantly, no waiting for
resolution. NegRisk (multi-outcome) events are protocol-enforced so exactly one
outcome pays; holding a NO in every outcome can be **converted** to collateral.
Therefore:

- `ask_YES + ask_NO < 1 − fees`  → buy both, merge, keep the difference.
- NegRisk: `Σ ask_YES < 1 − fees` → buy all YES ("long"); `Σ bid_YES > 1 + fees` → sell all YES / buy all NO ("short").

Evidence.
- The AFT 2025 study (Apr 2024 → Apr 2025 on-chain data): $10.58 M realized from
  single-condition rebalancing, $28.9 M from NegRisk intra-market, $95 K combinatorial;
  7,051 of 17.2 K conditions and 662 of 1,578 NegRisk markets had ≥1 opportunity;
  **sports had by far the most opportunities; politics had the largest ones**;
  opportunities cluster in volatility bursts; single-condition arbs were all "long"
  (sum < 1); top wallet made $2.0 M over 4,049 successful opportunities (~$500 each),
  #10 made $384 K over 2,720. Median bid on the whole exchange is $8.29 — this is a
  small-ticket game.
- Practitioner reports (2026): an 8-wallet operation running NegRisk harvesting at
  "industrial scale — hundreds of thousands of tiny fills"; 14 of the top 20 wallets on
  the Polymarket leaderboard are bots.

The 2026 catch. Those numbers were earned in a **zero-fee era**. With taker fees now
1–1.8 % at mid, a taker cannot profitably sweep a 1–2 ¢ gap. The strategy that
survives is **maker-side**: rest bids such that any fill leaves you one passive (or
cheap, tail-priced) leg away from a set that costs ≤ $0.985, merge, repeat. Makers pay
nothing, earn 15–25 % rebates on the flow that hits them, and can additionally farm
Liquidity Rewards for resting near mid. That is the "couple of dollars, thousands of
times" engine.

What "a couple of dollars" means in shares: a set completed at $0.985 nets 1.5 ¢/share
→ $2 needs ~135 shares ≈ $133 of capital per cycle, recycled within minutes via merge.
With $3–5 K of working capital and passive fills the binding constraint is **fill
rate**, not capital. Phase 0 measures fill rate; nothing else matters until we have it.

### 2.2 Polymarket maker rebates + Liquidity Rewards (Tier S yield floor)

Independent of mispricing: Polymarket redistributes 15–25 % of taker fees to the
makers who got hit, daily, and pays a separate daily pool for two-sided orders
resting within a per-market spread of the mid. A set-completion quoter is already
resting two-sided near mid, so it earns this for free. Treat it as the base yield
that keeps the bot cash-positive on quiet days; do **not** size up to farm it (that
is how you get picked off by informed flow — see 4.3).

### 2.3 Kalshi field-sum / ladder monotonicity (Tier A — scan, rarely execute)

- Single Kalshi market "YES+NO < 1" arbitrage is structurally impossible: buying NO
  *is* selling YES, the book straddles $1 by construction.
- Field-sum across a `mutually_exclusive` event (buy NO on every leg; pays ≥ n−1) and
  **ladder monotonicity** (P(BTC > 60 K) must be ≥ P(BTC > 65 K)) are real
  inconsistencies. Two independent exact-arithmetic scans in Sept 2026 report:
  12,119 events → 79 pass top-of-book → 8 positive LP → **0 survive exact fee
  rounding + whole-contract depth**; and 543 mutually-exclusive events → 3 gross →
  **all negative after taker fees**. Fee dome (2 ¢ at mid, per leg, rounded up per
  order) plus the narrowest leg capping size kills nearly everything.
- Keep it because it is cheap (public REST, no auth) and because volatility events
  (macro prints, in-game sports) are when the exact-verify step will finally return
  something. Use maker orders on the thickest legs to cut the fee to ~¼.

### 2.4 Cross-venue Polymarket ↔ Kalshi (Tier B — statistical, not risk-free)

- Fee floor as taker/taker: e.g. Kalshi YES at 52 ¢ (fee 2 ¢) + Polymarket NO at
  45 ¢ (fee ≈ 1 ¢) = $0.9999 → zero. You need ≥ 4–5 ¢ gross. Maker/maker cuts that
  to ~½ ¢ but introduces legging risk.
- Observed spreads 2026: 0.5–1.5 ¢ daily on liquid markets, 5–8 ¢ documented on
  World Cup team contracts; windows ~2.7 s. Public dataset (Aug 2026, 800 markets per
  venue): 17 verified pairs out of 312 candidates, **47 % of spreads negative after
  fees + depth, median executable spread $0.0006**.
- Non-price risks are the real killers: (a) **resolution divergence** — Kalshi binds
  a named source agency and morning-ET deadlines, Polymarket often uses "consensus of
  credible reporting" to 11:59 PM ET; the 2024 shutdown and the Cardi B halftime
  markets settled opposite ways; (b) **capital is trapped to resolution** on both
  legs (no merge across venues); (c) Kalshi ACH vs Polymarket USDC — rebalancing
  inventory between venues takes days; (d) matching is unsolved — token overlap
  produces mostly false pairs.
- Where it can work: markets whose settlement is the **same official number** on
  both venues (sports final scores, CPI/NFP/Fed decision, election calls) and where
  we verify rule text, deadline, and source programmatically + LLM before
  whitelisting the pair. Execute maker on the thicker venue, hold to resolution,
  size as trapped capital.

### 2.5 Event-driven convergence ("resolution sniping") (Tier B — same infra)

Not arbitrage, but near-deterministic: outcomes already decided (final score posted,
FRED number out, Chainlink/CF Benchmarks tick crossed) still offered < 99 ¢ for 2–15 s.
Requires reference feeds that **match the market's resolution oracle** (Polymarket
15-min crypto resolves on Chainlink; Kalshi crypto on CF Benchmarks — a documented
source of "fade" edges when scanners use Binance instead). Polymarket's dynamic fee on
15-min crypto was introduced specifically to tax this; sports and macro remain.
Build after the core engine; it reuses the executor unchanged.

### 2.6 Explicitly deprioritized

- Taker-sweep intra-venue arb at 1–2 ¢ (fees eat it).
- 15-min BTC Up/Down latency arb (now taxed ≈3 % at mid, heavily botted).
- Combinatorial cross-market logic (paper found ~$95 K total, 13 valid pairs in a year).
- UMA dispute betting (documented ~40 % win rate unfiltered).
- Anything that needs us to be faster than co-located Rust bots. We win on
  coverage (every book, all night) and on being a maker, not on microseconds.

---

## 3. Templates worth borrowing from (and what to take)

| Repo | Take | Leave |
|---|---|---|
| **AKCodez/prediction-market-alpha-playbook** | The 20-edge catalog, the 27 antipatterns, the paper→shadow→live-canary→scale graduation ladder with Wilson lower-bound gates, "PnL only from the exchange Data API" | It's a doc, not code |
| **ImMike/polymarket-arbitrage** (Python, 281★) | Gamma + CLOB WS + Kalshi REST plumbing, bundle-arb + NegRisk detection, dry-run mode, 1 % min-edge config | Text-similarity matching at 0.6 threshold (false pairs), in-memory state, taker execution |
| **vaibhavraj-0906/kalshi-arbitrage-scanner** | LP over settlement states, integer-cent fee rounding, depth-sized baskets, discovery/screen/confirm cadence tiers | Kalshi-only |
| **nanare-sudo/kalshi-polymarket-spreads** | 3-stage matcher (blocking → IDF token overlap → rule verification + optional LLM), executable-spread calc that walks both books per level with fees | — |
| **pmxt-dev/pmxt**, **guzus/dr-manhattan** | Market discovery across venues, reference implementations of each venue's quirks | Hot-path execution |
| **braedonsaunders/homerun** (Python) | Strategy plugin interface, backtest → paper → live pipeline, dashboard | Monolith |
| **warproxxx/poly-maker** (MIT, Python 3.12, 7.3k LOC, 113 tests) | **The fork base.** Maker-only CLOB V2 quoter whose BUY-YES + BUY-NO pair merges to USDC at locked edge; regime machine, heartbeat dead-man, WS-staleness halts, daily-loss kill, on-chain merge for EOA/Safe/Deposit wallets, WS + order journal | Political-only market selection, `py-clob-client-v2` pin, no NegRisk convert, no replay backtester |
| **Polymarket/py-sdk** (official, MIT) | Execution primitive: CLOB + Gamma + Data + relayer, WS streams, fee schedule, batch merge/split/redeem | — |
| Historical data: Telonex, PolymarketData, Marketlens, Lychee (Kalshi 36 GB), Kalshi `/historical/*` | L2 replay for backtests before our own recorder has depth | Paid; buy only what Phase 0 shows we need |

---

## 4. Architecture

```
                    ┌────────────────────────────────────────────────────────┐
                    │  recorder (always on)                                  │
   Polymarket WS ──▶│  L2 books → Parquet/DuckDB (per token, per second)     │
   Kalshi WS ──────▶│  trades, fills, our orders, fee rates, neg_risk flags  │
                    └──────────────┬─────────────────────────────────────────┘
                                   │ same in-memory book objects
      ┌────────────────────────────┴───────────────────────────────┐
      │ scanners (async tasks, one per opportunity class)          │
      │  S1 set-completion (binary)      S2 negrisk basket          │
      │  A1 kalshi ladder/field-sum      B1 cross-venue whitelist   │
      │  B2 event convergence (feeds)                               │
      │  each emits Opportunity{legs, net_edge_after_fees, depth}   │
      └────────────────────────────┬───────────────────────────────┘
                                   ▼
      ┌────────────────────────────────────────────────────────────┐
      │ RiskGovernor  (only minter of ApprovedOrder)               │
      │  kill switch · per-venue & global exposure · per-market cap│
      │  inventory-imbalance cap · daily loss lock (persisted)     │
      │  data-staleness gate · min net edge per class              │
      └────────────────────────────┬───────────────────────────────┘
                                   ▼
      ┌────────────────────────────────────────────────────────────┐
      │ executors: PaperExecutor(book-replay fill model) |          │
      │            PolymarketExecutor | KalshiExecutor              │
      │  maker-first quoting, hedge-leg state machine, merge/convert│
      └────────────────────────────┬───────────────────────────────┘
                                   ▼
      journal (SQLite/Postgres) ──▶ daily reconciliation from venue Data APIs
                                 ──▶ graduation gates ──▶ Prometheus/Grafana + Telegram
```

### 4.1 Design rules (non-negotiable, lifted from the antipattern list and poly-maker's hardening tests)

1. **Maker-first.** Default order type is post-only limit. A taker leg is allowed only
   when the governor confirms `net_edge ≥ min_edge` *including* that leg's fee at that
   price, computed with the venue's exact formula and rounding (Kalshi: integer cents,
   ceil per order; Polymarket: 5-decimal USDC, rate from the market's fee field).
2. **Exact money.** All prices in integer ticks / centi-cents; never compare floats.
3. **Side by index, never by string.** Polymarket `outcomeIndex`, Kalshi yes/no
   explicit. `NO_ask = 1 − YES_bid`, not `1 − YES_ask`.
4. **Depth-walked edge.** Edge is computed on the fill you would actually get for the
   size you would actually send, per level, narrowest leg caps size.
5. **PnL comes only from the exchange.** In-process journal is a hint; daily job pulls
   fills/positions from Polymarket Data API and Kalshi `/portfolio` + `/historical`
   and overwrites. Rows without a real fill price are `suspect`, excluded from gates.
6. **Governor token.** Scanners emit plain `Opportunity` data; only
   `RiskGovernor.evaluate()` can construct an `ApprovedOrder`; executors refuse
   anything else (a frozen dataclass whose constructor checks a module-private token).
   poly-maker's `RiskManager.evaluate()` is the seam where this goes.
7. **Fail closed.** Corrupt state → locked. WS gap → cancel all resting quotes for
   that market until the snapshot is rebuilt. Stale reference feed → class disabled.
8. **One process per venue shard, one writer per journal.** Prefix IDs per process.
9. **Paid RPC** (Alchemy/QuickNode) for Polygon merge/convert and resolution events.
10. **Trim every env var**, pin the proxy wallet address (not the EOA), floor share
    sizes to 4 dp — the boring bugs are the ones that burn weeks of paper evidence.

### 4.2 The set-completion state machine (core of S1/S2)

```
IDLE ──rest bid on leg A and leg B at pA+pB ≤ 1 − target──▶ QUOTING
QUOTING ──fill on A──▶ ONE_LEGGED(A)
ONE_LEGGED(A):
    if ask_B ≤ 1 − pA − fee_B(ask_B) − target_min:  take B  → COMPLETE
    else: re-rest bid on B at ≤ 1 − pA − target; hold ≤ T_max;
          if B fills → COMPLETE
          if T_max or adverse move > X: unwind A (sell at bid, maker if possible) → IDLE (log loss)
COMPLETE ──merge/convert → USDC──▶ IDLE
```

The whole edge lives in the `ONE_LEGGED` branch: how often the second leg fills, and
what the unwind costs when it doesn't. That is an empirical number per category and
hour, which is why Phase 0 is a recorder and Phase 1 is a replay simulator with a
conservative queue-position model, not a live bot.

### 4.3 Risk model specifics

- **Adverse selection** is the primary risk of a maker strategy: your bid gets hit
  precisely when informed flow knows the price is going lower. Mitigations: skew
  quotes by inventory, widen or pull on trade-rate spikes, pull entirely around
  scheduled events for that market (game start, data release), never quote a market
  whose resolution is inside the next N minutes unless you are the convergence
  scanner.
- **Trapped capital**: cross-venue and any unhedged leg are sized as hold-to-resolution.
- **Venue risk**: UMA dispute (Polymarket) and rule-interpretation risk (both) —
  whitelist pairs by settlement-source identity, cap per-pair exposure.
- **Regulatory/geo risk**: Polymarket intl is geo-gated; Polymarket US and Kalshi
  require KYC and are subject to live state litigation. Decide venue set by where the
  operator legally is (see §7 decisions).
- Hard caps, config can only tighten: per-market notional, per-class notional, total
  deployed, daily loss → flatten + persisted lockout, max one-legged exposure.

### 4.4 Stack

- Inherited from poly-maker: Python 3.12, `uv`, asyncio + uvloop, `httpx`, `websockets`,
  `pydantic` config, `structlog`, SQLite state, typer CLI, ruff + mypy strict.
  Added: `polymarket-client` (official py-sdk) replacing `py-clob-client-v2`,
  `kalshi_python_async` + own Kalshi WS client, DuckDB + Parquet for book recordings,
  Prometheus client + Grafana, Telegram alerts, systemd units. Python is fast enough:
  a maker strategy is bound by fill rate and correctness, not by microseconds.
- VPS in US-East (where both matching engines are commonly reported to live — verify
  with our own RTT tests before committing), 2 vCPU / 4 GB is plenty for the recorder
  and scanners; keep it separate from the 0DTE box.
- Repository: Weevil lives in its own fresh repository, forked from poly-maker and
  renamed (`src/weevil/…`). Layout grows from poly-maker's: `catalog/` (discovery, +
  sports/NegRisk/Kalshi), `marketdata/` (+ recorder), `strategy/` (+ set-completion
  state machine, NegRisk basket), `scanners/` (new: kalshi ladder/field-sum,
  cross-venue, convergence), `risk/` (+ governor token, breaker persistence),
  `execution/` (gateway on py-sdk, Kalshi gateway, merge/convert), `journal/` +
  `reconcile/` (new), `replay/` (new backtester over journals).

---

## 5. Phased plan with exit criteria

| Phase | Build | Exit criterion (measured, not assumed) |
|---|---|---|
| **0. Fork & record** (week 1–2) | Fork poly-maker → weevil, read every module, run its test suite, migrate the gateway to py-sdk; extend its journal into a recorder on the Polymarket market channel for top-N markets by 24 h volume + every NegRisk event + Kalshi orderbook_delta for the matching series; fee-rate and neg_risk metadata; offline notebooks that count set-completion opportunities net of fees by category × hour × size, one-legged fill probability proxies, Kalshi ladder/field-sum survivors after exact verification | A written "category report": opportunities/day, median net ¢/share, depth, persistence, by category. Go/no-go per class. |
| **1. Replay + paper** (week 2–4) | Book-replay simulator with conservative maker fill model (fill only when price trades through, not at); full governor; paper executors; journal + reconciliation code paths exercised against real Data API reads | ≥ 200 simulated set completions; paper net ¢/share within the recorded distribution; zero governor bypass paths (tests). |
| **2. Live canary** (week 4–6) | Polymarket only, S1 + S2, $300–500 deployed, sizes floor-min, everything else identical to paper | ≥ 100 live completions; live/paper degradation measured (expect 20–40 % worse); rebates observed arriving daily; no reconciliation drift. |
| **3. Scale ladder** | Wilson-lower-bound gates per the playbook: 1× → 2× → 5× only on N ≥ 30/60 fresh live trades, profit factor ≥ 1.5, auto-demote on 3 bad windows or −3 % deployed | Capital scales with evidence, never enthusiasm. |
| **4. Kalshi + cross-venue whitelist** | Kalshi executor (maker), ladder/field-sum executor for exact-verified survivors only, cross-venue for settlement-identical pairs with LLM rule diff + human approval per pair | Each new class repeats phases 1–3 independently. |
| **5. Event convergence** | Sports final-score, FRED, CF Benchmarks/Chainlink feeds with oracle-matched sources | Same graduation gates. |

Budget reality: sources converge on **$2–5 K split across venues** as the floor where
fees don't dominate; below ~$1 K minimum sizes and fixed costs (VPS, RPC, data) win.
Expect the honest steady state to be tens of small fills a day at a few cents/share
plus rebates, compounding via reinvested size — not a few hundred dollars a day on day one.

---

## 6. Open-source components to evaluate in Phase 0 (in priority order)

1. `Polymarket/py-clob-client` + CLOB market/user WS docs — execution primitive.
2. Kalshi `docs.kalshi.com` (REST v2, AsyncAPI, `/markets/orderbooks`, `/historical/*`,
   `mutually_exclusive`, `settlement_sources` field) — hand-roll client.
3. `vaibhavraj-0906/kalshi-arbitrage-scanner` — port the exact-cent LP verifier.
4. `nanare-sudo/kalshi-polymarket-spreads` — port matcher + executable-spread calc.
5. `ImMike/polymarket-arbitrage` — reference for NegRisk detection and Gamma discovery.
6. `pmxt` — market matching engine only.
7. Paid L2 history (Telonex / PolymarketData / Lychee) — only if Phase 0 recordings
   are too short to estimate one-legged fill rates for the best categories.

---

## 7. Decisions needed before Phase 0 code

1. **Jurisdiction.** US operator → Kalshi + Polymarket US (flat theta 0.06, 25 % maker
   rebate, separate API and liquidity, no NegRisk-style on-chain merge; check).
   Non-US → Polymarket intl (deepest books, on-chain merge/convert, category fees,
   geo-gate) ± Limitless/Opinion; Kalshi requires US KYC. This changes the Tier S
   engine, so it is decision #1.
2. **Capital and risk tolerance.** Starting working capital and the daily-loss lock
   (proposal: $3–5 K, −3 % day lock, 25 % max per market, 40 % max one-legged).
3. **Repository.** Proposal: a new repository for Weevil (fork of poly-maker), separate
   from anything you already have. Language is Python by inheritance.
4. **Data spend.** Are we willing to pay ~$50–200/mo for L2 history + paid Polygon RPC
   + VPS from day one? (Recommended yes for RPC and VPS; history only if needed.)

---

## 8. Sources

- Saguillo, Ghafouri, Kiffer, Suarez-Tangil, *Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets*, AFT 2025 — https://arxiv.org/abs/2508.03474
- Polymarket fee docs — https://docs.polymarket.com/trading/fees ; maker rebates — https://docs.polymarket.com/programs/maker-rebates ; liquidity rewards — https://help.polymarket.com/en/articles/13364466-liquidity-rewards ; gasless/relayer — https://docs.polymarket.com/trading/gasless ; WS market channel — https://docs.polymarket.com/market-data/websocket/market-channel
- Polymarket rate limits (2026) — https://agentbets.ai/guides/polymarket-rate-limits-guide/ ; API guide incl. IP gate — https://dev.to/will_c38674673aba82fa4cbe/polymarket-api-guide-2026-clob-gamma-websockets-rate-limits-and-the-ip-gate-51mp
- Polymarket 15-min crypto dynamic fees — https://www.financemagnates.com/cryptocurrency/polymarket-introduces-dynamic-fees-to-curb-latency-arbitrage-in-short-term-crypto-markets/
- Polymarket US fee schedule — https://docs.polymarket.us/fees ; US launch/state litigation — https://startpolymarket.com/countries/united-states/
- Kalshi rate limits & tiers — https://docs.kalshi.com/getting_started/rate_limits ; changelog (sharding, WS, fees) — https://docs.kalshi.com/changelog ; fee guide — https://marketmath.io/blog/kalshi-fees-guide-2026 ; WS quick start — https://docs.kalshi.com/getting_started/quick_start_websockets ; LP program — https://help.kalshi.com/en/articles/15410219-liquidity-provider-program
- Cross-venue executable spreads dataset — https://github.com/nanare-sudo/kalshi-polymarket-spreads
- Kalshi exact-arithmetic arbitrage scanner — https://github.com/vaibhavraj-0906/kalshi-arbitrage-scanner ; Kalshi arbitrage real vs myth — https://www.botforkalshi.com/blog/kalshi-arbitrage-guide
- Settlement divergence cases — https://www.oddsshopper.com/articles/prediction-markets/kalshi-vs-polymarket-settlement-rules ; https://defirate.com/prediction-markets/how-contracts-settle/
- Window collapse 12.3 s → 2.7 s — https://www.turbinefi.com/blog/prediction-market-arbitrage-latency-speed-2026 ; bots on leaderboard — https://www.financemagnates.com/trending/prediction-markets-are-turning-into-a-bot-playground/
- Practitioner view of the three real Polymarket arbs — https://polyflux.io/blog/polymarket-arbitrage/ ; NegRisk convert mechanics — https://startpolymarket.com/learn/converting-negative-risk/
- Playbook (edges, antipatterns, architecture, methodology) — https://github.com/AKCodez/prediction-market-alpha-playbook
- Repo audit and fork decision — `docs/WEEVIL_REPO_SURVEY.md`; base — https://github.com/warproxxx/poly-maker ; official SDKs — https://github.com/Polymarket/py-sdk , https://docs.kalshi.com/sdks/overview ; NautilusTrader Polymarket adapter — https://nautilustrader.io/docs/latest/integrations/polymarket/ ; bot malware campaigns — https://www.stepsecurity.io/blog/malicious-polymarket-bot-hides-in-hijacked-dev-protocol-github-org-and-steals-wallet-keys , https://www.cryptopolitan.com/defi-polymarket-users-targeted-npm-package/
- Other template repos — https://github.com/ImMike/polymarket-arbitrage ; https://github.com/pmxt-dev/pmxt ; https://github.com/guzus/dr-manhattan ; https://github.com/braedonsaunders/homerun ; https://github.com/verixiaapps/awesome-prediction-market-apis
- Historical data vendors — https://telonex.io/ ; https://www.polymarketdata.co/ ; https://marketlens.trade/ ; https://lycheedata.com/kalshi-historical-data ; https://docs.kalshi.com/getting_started/historical_data
