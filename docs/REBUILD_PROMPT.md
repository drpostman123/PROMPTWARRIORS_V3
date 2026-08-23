# PROJECT SKYFIRE v2 — Complete Build Prompt

> Paste this whole document into Claude Code as the opening prompt.
> If you are running inside a clone of the existing SKYFIRE repo, treat this
> as the SPEC TO IMPROVE TOWARD: audit the existing `src/skyfire_sol` against
> every section, close the gaps, and do not regress anything listed as
> battle-tested. If you are in an empty folder, build it from scratch to this
> spec. Either way: plan the architecture first, present it, then implement
> phase by phase with tests green after every phase.

---

## Mission

Build SKYFIRE: a live, autonomous meme-coin momentum rotation system that
trades real money on two venues — Solana spot memes (Jupiter routing) and
Hyperliquid perps — under a **greedy CEO allocation agent** that presses
winners hard, a **phantom-logging flywheel** that turns every untaken trade
into training data, and a **Goodhart-guarded self-improving loop** that tunes
the alpha and can never touch the safety rails. Linux-first (Ubuntu),
long-running under systemd, auditable end to end, monitorable and killable
from a phone.

The objective function is explicitly greedy: maximize expected return, with
risk appetite that INCREASES in confirmed momentum regimes. Greed lives in
allocation. The guardrails live outside the greedy loop. That separation is
the product.

## 0. Prime directives — structural, never negotiable in code

1. **The optimizer must not own its reward limits.** The CEO and the learner
   operate INSIDE a SafetyGate they cannot modify, disable, or route around.
   Enforce this four ways, each pinned by a test:
   - **Capability token**: orders reach execution only as frozen
     `ApprovedOrder` dataclasses whose `__post_init__` raises `PermissionError`
     unless constructed with a module-private `_GATE_TOKEN = object()` that
     only the SafetyGate's evaluate path supplies. Write the forgery test
     first.
   - **Import direction**: `ceo/` and `learning/` import only models, config,
     bus, blackboard, persistence. Never safety/, execution/, wallets, chain
     clients. Enforce with an AST-walking unit test, not a linter config.
     `learning/` also may not import `ceo/` — no optimizer rewrites another.
   - **State ownership**: breaker/probation/pause/HWM/policy files are written
     only by `safety/` (and `learning/` for policy.json only); the CEO reads a
     read-only mirror on the blackboard.
   - **Single submit chokepoint**: exactly one call site per venue that can
     submit a transaction/order (Solana RPC send, Hyperliquid exchange call).
     Pin with source-scan tests that fail if the submit string appears
     anywhere else.
2. **Fail toward safety.** Any unreadable safety-state file restores to the
   SAFE side: unreadable breaker file ⇒ LOCKED, unreadable probation ⇒ small,
   unreadable policy ⇒ defaults, unknown regime ⇒ most defensive caps,
   missing rug datum ⇒ gate FAILS. Never boot armed from corrupt state.
3. **Exits never ask permission.** Risk-reducing flow (sells, closes,
   flattens) bypasses the allocation checks, cannot be vetoed by the CEO,
   and passes a TRIPPED breaker and the kill switch when the reason is the
   flatten itself.
4. **Everything is journaled with reasoning.** Every gate decision with its
   full per-check trace, every CEO reallocation with plain-text reasoning,
   every learner conclusion (adopted/rejected/frozen) with rewards and sample
   counts. The journal is the product's memory and the learner's food.
5. **Size is the throttle, not time.** Live from day one. Probation sizes
   every entry at 50% and lifts ONLY when the human pushes the button
   (`FULL_SIZE` file / dashboard button / MCP tool). Clean-fill count is a
   readiness display, never a trigger.

## 1. Architecture

Single-process asyncio. Supervised task set: every loop wrapped in a
restart-on-exception supervisor (log, sleep 1s, re-enter; CancelledError
propagates). On fatal crash with open positions: persist the breaker trip
FIRST, then best-effort flatten both venues, and subscribe the drain queue
BEFORE publishing the flatten intents (a fresh bus subscription misses
earlier publishes — this was a real bug once).

- **Bus**: in-process topic pub/sub over bounded per-subscriber asyncio
  queues. Telemetry topics drop-oldest; intent/fill topics never drop
  (back-pressure). The approval edge (gate → executor) is a DIRECT typed
  call, never a bus message, or the token is decorative.
- **Blackboard**: single-writer immutable snapshot swapped atomically by the
  NAV task. Everyone else reads. Carries NAV, per-sleeve NAVs, allocations
  current/target, positions, prices, balances, regime read, and a read-only
  safety mirror.
- **Sleeve agents** publish `TradeIntent`s (pure data: sleeve, side, mint,
  size, urgency, reason — no route, no tx, no capability).
- **SafetyGate** runs the ordered check pipeline (first failure rejects,
  every decision journaled with the trace):
  `S0 kill → S1 breaker → S2 daily pause → S3 soft tier → S4 NAV sane+fresh
  → S5 sanity+blacklist → S6 rug verdict fresh(≤10min)+PASS → S7 correlation
  bucket → S8 sleeve caps → S9 probation resize (down, never up) → S10 fresh
  quote within the slippage cap` — then mints the token and calls the
  executor directly. CEO verdicts apply to meme entries only and clamp to
  `min(1.0, mult)` — a hostile multiplier can shrink, never grow.
- **ExecutionAgent**: per-venue paths, each the sole submitter.
  - Solana: re-quote if stale (>10s) re-asserting the cap → Jupiter swap
    build with dynamic priority fee → sign locally (solders) → SIMULATE
    before send (a deterministic failure never hits the chain) → send +
    confirm with lastValidBlockHeight expiry and idempotent rebroadcast →
    ALWAYS resolve to a definite terminal state (never leave an order
    unknown) → emit Fill with realized slippage vs quote.
  - Hyperliquid: IOC market orders with the SDK slippage bound; parse the
    order result statuses for per-fill errors.
- **CEO (greedy allocator)**: 5-min loop + event wakeups. Greedy score per
  sleeve = 0.6×7d Sharpe + 0.4×24h return. Regime = SOL EMA trend (6h vs 48h
  hourly) + meme breadth (share of scanned candidates with positive 1h
  momentum) + funding-rate veto (rich funding demotes risk-on to choppy).
  risk_on ⇒ multiply the winner's weight by the (learned) press gain and
  renormalize; choppy ⇒ boot weights; risk_off/unknown ⇒ collapse to
  CORE/USDC and YIELD. Output is `AllocationTargets` (data); the gate clamps
  before it takes effect. No special CEO trade path exists — sleeves resize
  themselves through ordinary intents.

## 2. Venues, sleeves, boot allocation

| Sleeve | Boot | What it does |
|---|---|---|
| MEME_ROTATION (Solana spot) | 25% | momentum entries on rug-clean memes; 2× take-profit sells half, −40% trail from peak on the runner, 48h time stop for flat/negative; ≤5 concurrent; per-position ≤20% of sleeve |
| HL_ROTATION (Hyperliquid perps) | 20% | long-only top-3 momentum rotation over the liquid non-major tail (≥$20M/day, majors excluded, funding-gated ≤0.05%/hr, positive 24h return); regime-scaled gross (risk_on 1.0 / choppy 0.5 / else 0); −40% trail; rotate out what falls from the ranking |
| CORE_HOLD | 25% | SOL/wBTC/wETH/USDC ballast, 5pp drift-band rebalance; holds the USDC the breakers retreat into |
| YIELD | 20% | jitoSOL liquid staking (LP venue behind a protocol seam for later) |
| PERPS (Drift) | 10% | SOL-PERP directional on the regime call, ≤3×, dedicated subaccount = isolation; ships disabled until its account is funded |

MEME + PERPS + HL_ROTATION are ONE correlation bucket: ≤55% of NAV risk-on,
≤30% risk-off/unknown — memes are one trade in a crash. Boot bucket = 55
exactly. The CEO may propose anything (0–100% per sleeve); the clamp is law.

Wallets: fresh dedicated keypairs only, generated by a keygen script,
age-encrypted at rest (pyrage, passphrase from env), decrypted into memory at
boot, plaintext never on disk, `/secrets/` gitignored. Funded manually
(Phantom → Solana pubkey; USDC via Arbitrum bridge → Hyperliquid address).
The bot never bridges funds cross-chain.

## 3. Rug/honeypot hard gate (Solana entries; ALL must pass, missing datum = FAIL)

1. mint authority revoked — verified ON-CHAIN (getAccountInfo jsonParsed),
   never trust an aggregator alone
2. freeze authority revoked — same
3. LP burned or locked ≥30d (RugCheck report; ≥90% lpLockedPct counts as
   burned/locked)
4. top-10 holders <30% of supply excluding the presumed main LP account
   (getTokenLargestAccounts + supply)
5. token age >1h — the launch-snipe zone is the MEV bots' kill box; do NOT
   add bonding-curve sniping, it contradicts this gate
6. liquidity >$100K pooled
7. **Jupiter sell-test**: quote the token back to SOL AND USDC; no route
   either way = honeypot = permanent persisted blacklist
8. deployer serial-rugger history (RugCheck rugged flag + creator risks)

These gates are not caution — they are what stops the bot from buying tokens
that cannot be sold.

## 4. Risk framework (the generous ladder — operator's chosen numbers)

Hard ceilings live as module constants; pydantic `Field(le=CEILING)` lets
YAML tighten but never loosen. Defaults / ceilings:

- Daily −15% (ceiling −20) ⇒ 24h entry pause, positions held; baseline rolls
  at UTC midnight IN-PROCESS and a mid-day restart can never re-anchor it
- Soft tier −40% from HWM (ceiling −45), clears at −30% hysteresis ⇒
  MEME+PERPS+HL halved, no new entries
- **Hard stop −60% from high-water mark ⇒ flatten everything on both venues
  to USDC, LOCKED, manual restart only** (re-arm requires
  `SKYFIRE_ACK_DRAWDOWN=YES` in env once, then remove it). This line never
  moves.
- Slippage caps: memes 3%, majors 0.5%, HL orders 1% (ceiling 3) — enforced
  in the quote client (refuses to even return a non-compliant quote) AND
  re-asserted at execution
- NAV plausibility quarantine: a >20% jump needs 3 consecutive consistent
  readings before it moves anything; quarantined NAV never sizes or trips
- Kill switch: `state/skyfire/KILL` file ⇒ reject all + flatten both venues;
  survives restart until a human deletes it
- Safety state lives in tiny per-concern JSON files with atomic tmp+rename
  writes — NEVER in the SQLite DB (a corrupt DB must not un-trip a breaker)

## 5. Phantom flywheel + self-improving loop

**Phantom log** (SQLite, WAL, single writer task consuming a bus topic):
every candidate that passes discovery but is not entered — rug_reject,
entry_reject, ceo_veto, gate_reject, near_miss (no slot / no capital) — gets
a row with the FULL feature vector, price at observation, and stage. A
backfill job resolves forward returns at 1h/6h/24h (price source with
fallback; unpriceable ⇒ dead=1, treat as −100%). Runs from block one.

**Learner** (every 6h), tunes ONLY: entry `min_vol_accel`, three entry score
weights, CEO `winner_press_gain`. Each clamped to hard bounds
(`LEARN_BOUNDS` module constants) the search cannot widen.

- Entry reward: replay resolved phantom rows in HOURLY BATCHES with top-N
  slot scarcity — mirror the live selection mechanics exactly, so both the
  gate (who passes) and the ranking (who wins scarce slots) move the reward.
  Winsorize at +300%, dead = −100%.
- Press reward: replay each historical risk-on allocation against realized
  next-24h sleeve returns from NAV history.
- Adoption: evidence floors (≥200 resolved phantom rows / ≥15 press events),
  walk-forward 70/30 by time, adopt only if the candidate beats the incumbent
  on the NEWEST 30% by a margin (+1.0pct entry / +0.10pct press). Journal
  every conclusion to a `policy_updates` table. Versioned
  `state/skyfire/policy.json`. `POLICY_FREEZE` file ⇒ evaluate + journal but
  apply nothing. Write the "flat world adopts nothing" regression test.

## 6. Use Hyperliquid's full toolkit (v2 improvements)

- **Agent (API) wallets**: approve a separate agent key for trading so the
  key in bot memory CANNOT withdraw funds — strictly better custody than
  trading with the master key. Keygen flow: master key signs the approval
  once (operator step), bot runs with the agent key.
- **WebSocket feeds** (`wss://api.hyperliquid.xyz/ws`): subscribe allMids +
  user fills instead of polling for marks/fills; keep REST metaAndAssetCtxs
  for the 5-min universe scan.
- **TWAP orders** for entries above a size threshold (HL native TWAP) so
  rotation size doesn't pay the whole spread at once; IOC market below it.
- **Spot HIP-1 memes** as an optional second HL universe behind the same
  gate (config-flagged, off by default until perps rotation is proven).
- Builder codes / vaults: out of scope — note why (custody and fee
  complexity outweigh benefit for a single-operator bot).
- Isolation: dedicated subaccount for anything levered.

## 7. Hard-won facts (probed live 2026-08 — do not rediscover these)

- Jupiter's `quote-api.jup.ag/v6` NO LONGER EXISTS. The identical schema
  lives at `https://lite-api.jup.ag/swap/v1/*` (free) and
  `https://api.jup.ag/swap/v1/*` (keyed). `priceImpactPct` is a FRACTION
  string ("0.0123" = 1.23%) — normalize. Base URL + key config-driven.
- Hyperliquid `POST /info {"type":"metaAndAssetCtxs"}` ⇒ ~232 perp markets;
  ctx fields: markPx, prevDayPx, dayNtlVlm, funding (hourly rate as
  fraction), openInterest; asset fields: szDecimals (round order sizes to
  it), maxLeverage. Python SDK `hyperliquid-python-sdk` works; it is SYNC —
  wrap calls in `asyncio.to_thread`. Orders <$10 notional are rejected.
- RugCheck `GET /v1/tokens/{mint}/report`: markets[].lp.lpLockedPct,
  creator, rugged, topHolders. Free tier works, rate-limit it.
- Free-tier rate limits are real at a 60s scan cadence: per-host async token
  buckets in EVERY client from day one (Helius ~8rps, DexScreener ~4rps,
  RugCheck ~2rps, Jupiter lite ~1rps) or the scanner 429-starves.
- Public Solana RPC 429s constantly — Helius key is effectively required;
  the fail-closed rug gate correctly rejects candidates when holder data
  can't be fetched (verify this behavior, keep it).
- Devnet is USELESS for this system (no Jupiter routing, no memes, no
  DexScreener/RugCheck). The correct rehearsal is a mainnet soak with an
  UNFUNDED keypair: everything runs live and entries die at preflight with
  insufficient-funds — proving the pipeline to the last instruction before
  value moves.

## 8. Improvements over v1 (build these in, they were v1 shortcuts)

- Real candle history for entries: keep a rolling in-memory OHLC per
  candidate (from scan-cycle marks) so EMA and ATR-based blowoff vetoes use
  actual series instead of the 1h-change proxy.
- Per-position realized PnL attribution written to the trades table on every
  close (entry cost basis from fills, not signal marks).
- Jito bundle path actually implemented for urgent Solana exits (not just
  flagged).
- HL fills reconciled from the user-fills WebSocket stream, feeding the same
  realized-slippage probation counter as Solana fills.
- Dashboard adds a tiny NAV sparkline from nav_history and a phantom
  hit-rate panel (avg fwd returns by stage — is the filter leaving money on
  the table or dodging bullets? That panel is the whole thesis in one row).

## 9. Ops surface

- **Web dashboard** (Starlette/uvicorn, separate process, reuses the same
  read-only monitor layer as MCP): NAV + badges (breaker/probation/soft-tier
  /kill/policy-frozen), sleeves, positions with PnL, policy version + recent
  learner conclusions, phantom stats, journal tail. Controls: KILL+flatten,
  go-full-size / back-to-probation, freeze/unfreeze policy — each behind a
  confirm. Mandatory ≥16-char bearer token from env (refuse to start
  without), bind 127.0.0.1, reach via `ngrok http 8787` or better
  `tailscale serve 8787`.
- **MCP stdio monitor** (dependency-free JSON-RPC): status, positions,
  journal_tail, phantom_stats, trades_tail, kill, go_full_size,
  back_to_probation, policy_status, freeze/unfreeze — same file-write
  control surface, runs out-of-process.
- **systemd**: hardened units (ProtectSystem=strict, empty capability set,
  ReadWritePaths only state+logs). `KillSignal=SIGINT` so the crash-flatten
  path runs. `WorkingDirectory` is a SAFETY CONTROL (state paths are
  CWD-relative; the wrong cwd silently re-arms a tripped breaker). NO
  nightly restart timer — crypto is 24/7, the daily baseline rolls
  in-process. Logrotate: copytruncate for the ops log (FileHandler opened
  once), plain rename for calibration JSONLs (reopened per record), never
  rotate the DB/state files.

## 10. Testing requirements (the suite IS the safety case; target 200+)

- Token forgery raises for every Approved* class; executor rejects
  non-approved objects; AST import-hygiene for ceo/ and learning/;
  single-submit-site source scans per venue.
- Gate: S0–S10 first-failure ORDER pinned; exits bypass pause/soft/bucket/
  caps; flatten passes TRIPPED; CEO veto works on entries and NOT on exits;
  hostile mult >1 cannot grow size; hostile 100%-MEME allocation clamps to
  the bucket.
- Breakers: trip/restore/unreadable⇒LOCKED, ack re-arm, hysteresis both
  directions, daily no-re-anchor across restart, NAV quarantine (3 reads).
- Probation: clean fills NEVER auto-lift; button lifts; deleting the button
  re-engages; unreadable counter fails small.
- Rug filter: table-driven per gate, first-failure ordering, missing datum
  fails, boundary values, blacklist persistence.
- Exit engine as a pure-function matrix (breaker > take-profit > trail >
  time stop; time stop never fires on a winner).
- Learner: adopts a genuinely separating threshold on synthetic data, waits
  below the evidence floor, journals-without-applying when frozen, KEEPS THE
  INCUMBENT in a no-edge world, bounds clamp.
- Clients on httpx.MockTransport (impact normalization, cap rejection
  pre-return, sell-test honeypot, HL result parsing).
- Executor on fakes + real throwaway keypair: simulate-before-send ORDER
  asserted, preflight abort, confirm-timeout resolves via await_terminal,
  stale-quote re-quote re-asserts the cap.
- Config: every hard ceiling rejects loosening via ValidationError; cross-
  field coherence (pause < soft < hard, hysteresis below tier, allocations
  sum to 100).

## 11. Build order & go-live runbook

1. Skeleton: config (ceilings) → models → bus/blackboard → wallets → chain/
   HTTP clients with rate limiters. `--selftest` proves connectivity (RPC,
   Jupiter quote, DexScreener, RugCheck, HL info, DB, heartbeat).
2. Solana meme path: rug gate → scanner → entry/exit engines → persistence
   + phantom → breakers/probation/gate → executor → sleeve. Tests green.
3. CEO + CORE + YIELD + NAV engine. 4. HL rotation (venue, gate path,
   sleeve, WS fills). 5. Drift perps (optional extra, disabled default).
   6. Learner. 7. Dashboard + MCP + systemd + runbook.
8. **Verify without funding**: full suite → selftest → multi-hour mainnet
   soak with unfunded keypairs (real discovery/filtering/phantom accrual;
   force one micro intent to die at preflight). Overnight backfill ⇒ first
   phantom analysis before a cent is deployed.
9. Fund small. Probation 50% until the operator pushes the button. The
   learner needs ~1–2 days of phantom accrual before its first opinion —
   until then it logs "waiting" and that is correct behavior.

Non-negotiables that survive every refactor: the rug gate, the sell-test,
the slippage caps, the token pattern, fail-safe restores, exits-never-ask,
the −60% line, journaled reasoning everywhere, and the learner's bounds +
walk-forward discipline.
