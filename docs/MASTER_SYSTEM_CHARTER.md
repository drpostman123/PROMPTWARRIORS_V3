# Master System Charter (v0.2, 2026-09-19)

> Canonical numbers (allocation, ladder, caps, gates) live in **one place**: `MASTER_KICKOFF_PROMPT.md` §"Canonical defaults". Where any other document disagrees, that section wins. Sleeve specifications: `specs/core.md`, `specs/convex.md`, `specs/macro.md`, `specs/events_routing.md`; sizing ladder: `research/sizing.md`; red-team review: `research/redteam.md`.

The operator's stated goal, in order of priority:

1. **Build a stock and crypto portfolio that compounds slowly and survives.** This is the
   purpose of the whole system; everything else exists to feed it.
2. **A small options sleeve** that hunts asymmetric, defined-risk "rocket" payoffs. Its
   profits are swept into the grind sleeves and, above a threshold, into the core.
3. **The prediction-market grind box** (Picador on Webull/Kalshi, fully researched in
   `PICADOR_WEBULL_PLAN.md`, `PICADOR_RESEARCH_ADDENDA.md`) and possibly a crypto grind.
4. **Macro sleeves** in commodities, bonds/yields and FX, switched on by trend and regime
   filters "when it matters".
5. All of it runs as the multi-broker octopus (`PICADOR_ORCHESTRATION.md`): one head,
   independent arms per broker, rented VPS primary, Linux desktop warm standby.

## Sleeve model

| Sleeve | Purpose | Character | Default capital share (to be tuned by the evidence brief) | Where it trades |
|---|---|---|---|---|
| **CORE** | Long-term stock + crypto compounding | Trend/momentum-tilted, volatility-targeted, low turnover | 60–70 % | Public / tastytrade / Webull for equities; Robinhood Crypto API or Webull for crypto |
| **CONVEX** | Asymmetric options payoffs | Small, defined-risk, expects to lose most months | ≤ 5–10 % | tastytrade / Public / Webull options; index options for 1256 treatment |
| **GRIND-PM** | Prediction-market mispricing (Picador) | Many small hold-to-settlement trades, exact fee math | 10–20 % as a measurement budget until proven | Webull events, Kalshi direct |
| **GRIND-CRYPTO** | Crypto basis/funding/mispricing (research only for now) | To be evidenced before any capital | 0 % until evidenced | Robinhood Crypto API / Webull |
| **MACRO** | Commodities, Treasuries/yields, FX trends | Mostly flat; enters on trend + regime confirmation | 0–15 %, regime-gated | ETFs and micro futures (tastytrade/Webull); Kalshi economics contracts for discrete events |

Profit sweep (default, to be tuned): CONVEX realized gains → GRIND-PM until GRIND-PM is at
its cap → CORE. GRIND-PM realized gains above its cap → CORE. CORE never funds CONVEX
beyond its cap; CONVEX refills only from its own budget line each quarter.

## Treasury rules the head enforces
- One equity curve, one risk budget, sleeves as sub-accounts in the journal even when
  they share a brokerage account.
- Drawdown ladder on total equity: −5 % halves CONVEX and MACRO; −10 % pauses CONVEX and
  MACRO, halves GRIND; −15 % everything flat except CORE at its floor allocation.
- Every sleeve has its own paper → canary → scale ladder; no sleeve inherits another's
  evidence.
- Sleeve-level and total position limits, plus the Kalshi aggregate limits and the
  self-match lock from the orchestration design.

## What is evidenced today vs. not
- Evidenced and specified: GRIND-PM (Picador), the octopus constraints, broker API facts.
- Being researched now: CORE trend rules, CONVEX structures and size cap, MACRO
  expression and costs, broker fit per sleeve, tax/account structure
  (`docs/research/portfolio.md` when the agent reports).
- Not yet researched: GRIND-CRYPTO specifics.

## Build order (one sleeve live before the next starts)
1. Octopus skeleton + Picador (GRIND-PM) in paper, then canary.
2. CORE: rules from the evidence brief, implemented as a low-frequency rebalancer arm on
   the cheapest equity/crypto broker; paper for one full rebalance cycle before live.
3. CONVEX: rule set from the brief, hard cap, kill rule; paper through at least one
   earnings season and one FOMC.
4. MACRO: regime filter + instrument map; paper until a trend regime actually triggers.
5. Standby deployment and failover drills before any sleeve scales past canary.
