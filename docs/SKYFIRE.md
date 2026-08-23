# SKYFIRE_SOL — Runbook

Meme-coin momentum rotation system across two venues. Five sleeves
(MEME_ROTATION on Solana / CORE_HOLD / YIELD / PERPS on Drift /
HL_ROTATION on Hyperliquid perps) under a greedy CEO allocator that
operates strictly *inside* a SafetyGate it cannot modify, disable, or
route around. Live from day one; **size is the throttle** (50%
per-position probation until the operator pushes the full-size button).

> **Risk.** Meme coins are among the most volatile and adversarial
> markets that exist; most meme tokens go to zero, and no configuration
> of this system guarantees profits or caps losses at any comfortable
> number — the portfolio hard stop is **−60% from the high-water mark**
> by design. Fund the hot wallet only with money you can afford to lose
> entirely.

## Architecture in one paragraph

Sleeves publish `TradeIntent`s (pure data) to the in-process bus. The
CEO judges meme entries and proposes allocation targets — also data.
The **SafetyGate** runs the S0–S10 pipeline (kill switch, breakers,
daily pause, soft tier, NAV quarantine, blacklist, rug-verdict TTL,
correlation bucket, sleeve caps, probation resize, slippage-capped fresh
quote) and is the only minter of `ApprovedOrder` — a capability token
whose constructor raises anywhere else. The **ExecutionAgent** accepts
only `ApprovedOrder`, holds the single RPC submit path (sign → simulate
→ send → confirm with blockhash-expiry rebroadcast → definite terminal
state), and reports realized slippage. Exits are risk-reducing: they
skip the allocation checks and the CEO cannot veto them. All of this is
pinned by tests (`tests/test_skyfire_token.py`, `test_skyfire_gate_order.py`).

## Setup

```bash
python -m venv venv && venv/bin/pip install -e ".[dev]"   # add .[perps] later
venv/bin/python scripts/skyfire_keygen.py                 # Solana wallet (pubkey)
venv/bin/python scripts/skyfire_keygen.py --evm           # Hyperliquid wallet (0x…)
# Fund the Solana pubkey from Phantom; fund the 0x address with USDC on
# Hyperliquid via the Arbitrum bridge. DEDICATED hot wallets only —
# never your main wallets. Both decrypt with SKYFIRE_KEY_PASSPHRASE.
```

The HL_ROTATION sleeve is inert until `secrets/wallet_hl.age` exists and
the account holds equity — everything else runs without it.

Environment (systemd `EnvironmentFile=/etc/skyfire/env`, mode 0600):

```
HELIUS_API_KEY=...            # required (public RPC is selftest-only)
SKYFIRE_KEY_PASSPHRASE=...    # decrypts secrets/wallet.age
BIRDEYE_API_KEY=...           # optional: holder-count enrichment
JUPITER_API_KEY=...           # optional: api.jup.ag keyed tier
SKYFIRE_DASH_TOKEN=...        # >=16 chars — gates the web dashboard
```

Config: `config/skyfire.yaml`. Every number can only **tighten** the
hard ceilings baked into `src/skyfire_sol/config.py` — pydantic rejects
the file otherwise. Secrets never go in YAML.

## Verification without funding (do this first)

1. `venv/bin/python -m pytest tests/ -q` — full suite.
2. `venv/bin/skyfire --selftest` — config, key decrypt, Helius, Jupiter
   quote, DexScreener, RugCheck, SQLite, heartbeat.
3. **Mainnet read-only soak**: run `skyfire` for a few hours with the
   *unfunded* keypair. Everything is real — discovery, rug filtering,
   phantom logging, quotes, CEO loop, dashboard — and entries fail
   cleanly at preflight with insufficient funds, proving the pipeline to
   the last instruction before value moves. (Devnet is useless here: no
   Jupiter routing, no meme ecosystem, no DexScreener/RugCheck.)
4. Check the phantom log the next morning:
   `python -m skyfire_sol.monitoring.mcp` → `phantom_stats`, or
   `sqlite3 state/skyfire/skyfire.db 'SELECT stage, COUNT(*) FROM phantom_log GROUP BY 1'`.
5. Fund the wallets. Every entry runs at 50% size until **you** push the
   full-size button — there is no automatic lift. The dashboard's
   `PROBATION n clean` badge and the MCP `status` tool show the
   clean-fill readiness count; when you're satisfied, push the button
   with the MCP `go_full_size` tool or `touch state/skyfire/FULL_SIZE`
   (`back_to_probation` / deleting the file reverses it instantly).
   The CEO's verdict multiplier still modulates size inside probation —
   it can shrink entries, never grow them past the throttle.

## Running

```bash
skyfire --config config/skyfire.yaml              # headless
skyfire --config config/skyfire.yaml --dashboard  # with Rich dashboard
```

Production: `deploy/skyfire.service` (see its header comments — the
WorkingDirectory and KillSignal notes are load-bearing), logrotate
stanzas in `deploy/logrotate.conf`, MCP monitor via
`deploy/skyfire-mcp.service` or plain SSH stdio.

## Operating procedures

The breaker ladder is deliberately **generous** (operator choice): the
soft tier and daily pause sit wide so the bot runs hot, and only the
−60% hard stop is immovable. YAML can tighten any of it (ceilings:
daily ≤20, soft ≤45), never loosen past the ceilings.

| Situation | What happens | Operator action |
|---|---|---|
| Kill needed NOW | — | `touch state/skyfire/KILL` (or MCP `kill` tool). Everything halts and flattens (both venues). Delete the file to re-enable. |
| Ready for full size | — | MCP `go_full_size` or `touch state/skyfire/FULL_SIZE`. This is the only way probation lifts. |
| Learner acting oddly | policy updates journaled in `policy_updates` | MCP `freeze_policy` (or `touch state/skyfire/POLICY_FREEZE`); it keeps journaling proposals without applying them |
| −15% day | 24h entry pause, positions held | none — clears itself; survives restarts |
| −40% from HWM | MEME+PERPS+HL halved, no new entries until −30% | none — hysteresis clears it |
| −60% from HWM | full liquidation to USDC (both venues), LOCKED | investigate; to re-arm: set `SKYFIRE_ACK_DRAWDOWN=YES` in the env, restart, then **remove the variable** |
| Corrupt/missing safety state | boots LOCKED (fail-safe) | inspect `state/skyfire/*.json`, fix or delete deliberately |
| PERPS go-live | — | fund the Drift subaccount, `pip install ".[perps]"`, set `sleeves.perps.enabled: true`, verify the driftpy API surface on the first order |

## Self-improving loop

The learner (`src/skyfire_sol/learning/`) closes the grounded reward
loop every 6 hours, under the same Goodhart discipline as everything
else — it tunes **alpha only**, inside hard bounds it cannot widen, and
no safety parameter is reachable from it:

- **Entry policy** (`min_vol_accel` + score weights): replays every
  resolved phantom candidate in hourly batches with top-N slot scarcity
  — "who would this parameter set have picked, and what did those
  tokens actually do at 6h?" Dead tokens count as −100%; rewards are
  winsorized at +300% so one outlier can't hijack the policy.
- **CEO press gain** (`winner_press_gain`): replays every historical
  risk-on allocation against realized next-24h sleeve returns — "how
  hard should the winner have been pressed?"

Adoption discipline: minimum evidence floors (200 resolved phantom
rows / 15 press events), then walk-forward validation — candidates are
searched on the older 70% of data and adopted only if they also beat
the incumbent on the newest 30% by a margin. Every conclusion (adopted,
rejected, or frozen) lands in the `policy_updates` table with rewards
and reasoning; the live policy is `state/skyfire/policy.json`
(versioned, clamped, fail-safe to defaults if unreadable).

Operator controls: MCP `policy_status` shows the current version and
recent conclusions; `freeze_policy` / `unfreeze_policy` (or
`touch state/skyfire/POLICY_FREEZE`) halts application while the
learner keeps journaling what it *would* have done.

## Monitoring

**Remote web dashboard** (`skyfire-dash`, `deploy/skyfire-dash.service`):
token-authed Starlette app on 127.0.0.1:8787 showing NAV, badges
(breaker/probation/soft-tier/kill), sleeves, positions, policy version +
recent learner conclusions, phantom stats, and the decision journal —
plus the operator buttons (KILL+flatten, go full size / back to
probation, freeze / unfreeze policy). Set `SKYFIRE_DASH_TOKEN`
(>=16 chars) in `/etc/skyfire/env`; the process refuses to start
without it. Reach it from anywhere via a tunnel:

```bash
ngrok http 8787            # quick public tunnel (token still required)
tailscale serve 8787       # preferred: private tailnet, nothing public
```

The dashboard and the MCP monitor are both out-of-process observers over
the same state dir — they can watch and halt, never trade.

- `state/skyfire/heartbeat.json` — 5s cadence; stale >30s means the
  runtime is dead (the MCP `status` tool flags this).
- `state/skyfire/snapshot.json` — full state, 2s atomic overwrite.
- `state/skyfire/decisions.jsonl` — every gate decision with the full
  per-check trace; every CEO reallocation with reasoning.
- `state/skyfire/skyfire.db` — phantom log (with 1h/6h/24h forward
  returns backfilled), trade journal, allocation journal, NAV history.
  This is the training corpus for the self-improving loop; keep it.

## Known deltas from the spec

- Jupiter's `quote-api.jup.ag/v6` no longer exists (probed 2026-08).
  The client speaks the identical schema at `lite-api.jup.ag/swap/v1`
  (default, free) or `api.jup.ag/swap/v1` (with `JUPITER_API_KEY`).
- YIELD v1 is jitoSOL-only. The Orca USDC/SOL concentrated LP has no
  maintained Python SDK; the `LpVenue` protocol in
  `sleeves/yield_.py` is the seam where it lands later.
- PERPS ships disabled until its Drift subaccount exists and the
  driftpy surface is verified live (`execution/drift_venue.py` notes).
- HL_ROTATION trades Hyperliquid perps long-only: the liquid non-major
  tail (≥$20M/day volume, majors excluded) ranked by 24h momentum,
  top-3 equal-weight, regime-scaled (risk-on full, choppy half,
  risk-off flat), funding-gated, −40% trail from the session peak.
  Its account equity joins portfolio NAV and it shares the MEME+PERPS
  correlation bucket. Capital moves on/off Hyperliquid manually (the
  bot never bridges funds cross-chain).
- Probation has no auto-lift — full size is a human decision
  (`go_full_size`), per operator preference. Breaker defaults are the
  generous ladder above.
