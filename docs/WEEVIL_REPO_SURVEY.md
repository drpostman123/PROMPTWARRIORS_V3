# Weevil — Open-Source Base Selection (audited 2026-09-18)

Weevil is a greenfield bot. It does **not** build on any code already in this
repository. This document records which other people's open-source prediction-bot
repos were evaluated, how, what was found in the actual code, and which one Weevil
forks. Everything here comes from shallow clones inspected locally today, not from
READMEs or star counts.

## 1. How the survey was done

- Candidate list from GitHub search (topics `polymarket`, `kalshi`, `prediction-market`,
  `arbitrage`), the AFT 2025 arbitrage paper's tooling references, the
  `prediction-market-agent-atlas` safety rankings, and the official Polymarket and Kalshi
  SDK pages.
- 25 repos cloned. For each: language, lines of code, test files and test functions,
  license file, last commit date, and grep-based capability detection (websockets,
  exact fee formula, kill switch / daily loss, paper mode, NegRisk handling, on-chain
  merge/redeem, Kalshi, order-book maintenance, backtest/replay, metrics, LLM use).
- Supply-chain scan on every clone: npm `preinstall`/`postinstall` hooks, `curl | sh`,
  base64 decode + `eval`/`exec`, and any code path that puts a private key next to a
  network call to a non-venue destination. All hits were reviewed by hand; every one
  was benign (auth-secret decoding, pagination cursors, documented installers).
- Core code of the top four read directly: strategy, risk, execution, merge.

## 2. Threat model first: this niche is booby-trapped

- July 2026: a "polymarket-arbitrage-bot" repo (36 stars, 53 forks) shipped a typosquat
  dependency `clob-client-math` that stole wallet keys, browser credentials, SSH and
  cloud tokens on `npm install`; 30 malicious npm packages in the same campaign.
- Feb 2026: a hijacked GitHub org (`dev-protocol`) was flooded with Polymarket bot repos
  carrying key-stealing dependencies.
- Wallet-drainer web "bots" (PolyArb) flagged by ZachXBT.
- Dozens of keyword-stuffed clones with purchased stars ("polymarket polymarket
  polymarket…" descriptions, 2016 creation dates on 2025 code, identical READMEs under
  different owners).

Weevil rules that follow from this: never `pip install`/`npm install` an unvetted repo on
a machine that holds keys; read `pyproject`/`package.json` scripts and every dependency
name before installing; pin and hash dependencies; run on a dedicated VPS with a
dedicated hot wallet holding only working capital; official SDKs only for signing.

## 3. Scorecard

LOC = source lines excluding vendored/generated. Tests = test files (functions where read).

| Repo | License | Lang | LOC | Tests | Last commit | What it really is | Verdict |
|---|---|---|---|---|---|---|---|
| **warproxxx/poly-maker** | MIT | Py 3.12 | 7.3k | 16 files / 113 fns, ruff+mypy strict | 2026-07-09 | Maker-only Polymarket CLOB V2 quoter. Quotes BUY-YES + BUY-NO as a pair that **merges to USDC at locked edge 1−p−q**; depth-weighted microprice + signed-flow FV; inventory skew; vol/toxicity (markout) EWMAs; regime machine QUIET/TRENDING/EVENT/REDUCE_ONLY/HALTED; reward-band + maker-rebate-aware market selection; heartbeat dead-man switch; WS-staleness halts; daily-loss kill; SQLite state; WS + order journal for replay; native on-chain **merge** for EOA / Safe / Deposit-wallet via relayer, verified live 2026-07-09 | **FORK THIS** |
| **Polymarket/py-sdk** (official) | MIT | Py | 85.6k | 196 | 2026-09-19 | Unified official SDK: CLOB + Gamma + Data + relayer, sync/async, WebSocket streams (CLOB, RTDS, sports), `fee_schedule`, heartbeat, post-only, batch orders, **batch merge / split / redeem**, `neg_risk` throughout. Polymarket now recommends it over `py-clob-client-v2` | **Execution primitive** (migrate poly-maker's gateway to it) |
| Polymarket/py-clob-client-v2 | MIT | Py | 10.6k | 22 | 2026-07-17 | Previous official CLOB client; what poly-maker pins today | Transitional |
| Kalshi official `kalshi_python_sync` / `_async` | — | Py | — | — | weekly | REST only, RSA-PSS auth, generated from OpenAPI; WS/FIX not covered | **Kalshi REST**; write our own WS client |
| TexasCoding/kalshi-python-sdk (`kalshi-sdk`) | MIT | Py | 94k | 161 | 2026-09-13 | Community SDK with WS support | Reference for WS client; don't depend on it |
| NautilusTrader Polymarket adapter | LGPL-3 | Rust/Py | (large) | CI | active | Production-grade: L2 MBP deltas, batch orders, cancel-replace, multi-wallet, `fee_schedule` + rebate model, backtest engine; Kalshi adapter in open PR | Fallback platform if poly-maker's single loop hits limits; heavy |
| **vaibhavraj-0906/kalshi-arbitrage-scanner** | **none** | Py 3.12 | 6.3k | 39 (incl. property tests) | 2026-09-13 | Integer-cent Kalshi fees, LP over settlement states, depth-sized baskets, discovery/screen/confirm cadences. Sept-13 scan: 12,119 events → 0 verified survivors | Re-implement the design (no license = no copying) |
| **nanare-sudo/kalshi-polymarket-spreads** | MIT | Py | 0.6k | 0 | 2026-08-21 | 3-stage cross-venue matcher (blocking → IDF token overlap → rule check) + executable-spread calc walking both books with per-level fees. Dataset: 47 % of spreads negative after costs | **Graft** matcher + spread calc |
| AKCodez/prediction-market-alpha-playbook | MIT | docs | — | — | 2026-04-30 | 20 edges, 27 antipatterns, graduation ladder with Wilson bounds | Methodology reference |
| agent-next/polymarket-paper-trader | MIT | Py | — | — | 2026 | Paper fills against live books; S-tier in the agent atlas | Evaluate for Phase 1 paper engine |
| YichengYang-Ethan/oracle3 | Apache-2 | Py | 59k | 47 files (633 fns claimed) | 2026-05-07 | Wang-transform fair value, 8 "constraint arb" strategies, correlation risk manager, Kalshi + Polymarket + Solana traders (FOK via legacy client). Event-sum arb uses a **flat 0.5 % fee guess and top-of-book only** | Ideas (calibration, correlation risk); not a base |
| braedonsaunders/homerun | **AGPL-3** | Py + React | 418k backend | 254 | 2026-08-21 | Full platform: 35 strategies (ctf_basic_arb, negrisk with the "by-date markets are not exclusive" guard, cross_platform, vpin_toxicity, holding_reward_yield…), live execution adapters, latency-injected backtests. Strategies are DB-stored "seed templates" | Read for ideas. AGPL + 418k LOC = do not fork |
| pmxt-dev/pmxt | MIT | TS | 118k | 95 | 2026-07-18 | Unified 15-venue API + cross-venue matching engine; 1.3k open issues | Discovery/matching sidecar only |
| guzus/dr-manhattan | **none** | Py | 36k | 31 | 2026-07-13 | CCXT-style 5 venues; clean CTF split/merge/redeem via Safe + relayer | Reference only |
| ImMike/polymarket-arbitrage | **none** | Py | 10.4k | 6 | 2025-12-09 | Gamma+CLOB+Kalshi, bundle/NegRisk/cross scanners, text-similarity matching at 0.6, taker execution | Reference only |
| OctagonAI/kalshi-trading-bot-cli | MIT | TS | 42k | 49 | 2026-09-09 | LLM research → Kelly sizing → 5-gate risk; directional, not arb | Out of scope |
| ryanfrigo/kalshi-ai-trading-bot | MIT | Py | 27k | 21 | 2026-06-17 | LLM directional | Out of scope |
| suislanchez/polymarket-kalshi-weather-bot | none | Py | 7.7k | 0 | 2026-03-01 | GFS-ensemble weather + BTC signals | Out of scope |
| alsk1992/CloddsBot | MIT | TS | 307k | 53 | 2026-09-12 | LLM agent platform across 1000+ venues; `postinstall` runs native-binding patch scripts and downloads an embedding model; `curl | bash` installer | Wrong shape, too large a trust surface |
| mbordash/DRADIS | **none** | Rust | 96k | 2 | 2026-09-18 | 96k lines, 2 tests, 27 stars, "quantum-computing" topic | Generated bulk; avoid |
| HarrierOnChain/Prediction-Markets-Trading-Bot-Toolkits | MIT | Rust | 3.4k | 0 | 2026-09-07 | 450 stars on 3.4k LOC with an SEO description | Star inflation; avoid |
| CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot | MIT | Py | 1.9k | 0 | 2026-05-04 | Polling BTC-hourly spread detector, no WS | Toy |
| realfishsam/prediction-market-arbitrage-bot | MIT | JS | 0.8k | 3 | 2026-01-16 | pmxt demo | Toy |
| Polymarket/poly-market-maker | MIT | Py | 3.8k | 17 | 2024-03-11 | Official keeper, pre-V2, unmaintained | Historical |
| Drakkar-Software/OctoBot-Prediction-Market | none | Py | 0.2k | 2 | 2026-03-30 | Launcher shim for OctoBot | Skip |
| elielieli909/polymarket-marketmaking | none | Py | (venv committed) | 12 | 2021-08-19 | Dead | Skip |
| Not cloned, flagged by pattern: radioman/*, steveseguinnin/*, txthinkin/*, Benjam1nCup/*, thesoulcrancerdev/*, AsArchitects & llllllilllll twins, TopTrenDev/* | | | | | | Keyword-stuffed / duplicate READMEs | Do not run |

## 4. Why poly-maker is the base

1. **It already is the Tier-S strategy.** Its canonical quote is a BUY-YES bid plus a
   BUY-NO bid priced so a filled pair merges to $1 of USDC at a locked edge. That is
   maker-side set completion, the one intra-venue arbitrage that survives 2026 fees.
2. **It handles the parts that burn people:** post-only everywhere, exchange heartbeat
   so a crash cancels resting orders, halts on stale market WS / blind user WS, per-market
   and event-group exposure caps, daily-loss kill, tick rounding in the right direction,
   fee/reward params refreshed from Gamma before every quote cycle, real on-chain merge
   for all three wallet types with a live-verified transaction.
3. **It is small enough to own.** 7.3k lines, pure-function strategy core
   (`(book, inventory, params, clock) → TargetQuotes`), 113 tests, strict typing. We can
   read all of it in a day; we cannot say that about the 100k–500k-line platforms.
4. **MIT.** Fork, rename, ship.

Gaps we fill (this is Weevil's own work):

| Gap | Plan |
|---|---|
| Political markets only | Add sports and NegRisk-event discovery (the paper: sports has the most opportunities) with per-category fee rates |
| Merge only, no NegRisk convert | Add convert/redeem via `py-sdk` batch position ops |
| Pinned to `py-clob-client-v2` | Migrate `execution/gateway.py` to `py-sdk` (official, updated daily, WS + relayer + positions in one package) |
| No replay backtester | Build a replay engine over its existing `/journal/` WS + order logs with a conservative queue-position fill model |
| No Kalshi | Kalshi REST via official SDK, own WS client; port the exact-cent LP scanner design (re-implemented, not copied) |
| No cross-venue | Graft the MIT matcher + executable-spread calc; whitelist pairs only with identical settlement source; hold-to-resolution sizing |
| Journal is local truth | Add daily reconciliation from Polymarket Data API / Kalshi portfolio endpoints; unreconciled rows never feed sizing gates |
| No graduation ladder | Paper → shadow → canary → scale with Wilson-lower-bound gates, per the playbook |

## 5. Decision record

- Base: fork `warproxxx/poly-maker` @ latest `main` (2026-07-09) → `weevil`.
- SDKs: `polymarket-client` (py-sdk) for Polymarket; `kalshi_python_sync`/`_async` for
  Kalshi REST; hand-written Kalshi WS.
- Ideas-only sources (license or size): homerun (AGPL), oracle3, dr-manhattan,
  ImMike/polymarket-arbitrage, kalshi-arbitrage-scanner (no license).
- Code grafts (MIT): nanare-sudo matcher/spread calc; playbook methodology.
- Rejected: everything in the bottom third of the scorecard.
