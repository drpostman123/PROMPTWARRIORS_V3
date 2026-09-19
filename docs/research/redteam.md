# Picador / Octopus — Red-team review (2026-09-19)

Reviewed: `MASTER_SYSTEM_CHARTER.md`, `PICADOR_ORCHESTRATION.md`, `PICADOR_WEBULL_PLAN.md` (v1.1),
`PICADOR_RESEARCH_ADDENDA.md`, `PICADOR_KICKOFF_PROMPT.md`, `research/quant.md`, `research/ops.md`,
`research/webull_api.md`. Four hats: skeptical quant, compliance officer, SRE, tax advisor.

Independently re-verified this session (primary sources, not the agents' summaries):
- Kalshi Rulebook v1.29 PDF, text extracted locally: Rule 5.11(c) 15-minute review window, (c)(ii) ±20¢ "No
  Cancellation Range", (c)(v) "$3,000 … the Trader whose order resulted in the cancellation or adjustment",
  (c)(vi) "final and not subject to appeal", (d) ATS-malfunction relief only for "Self-Clearing Members … signatories
  to an active market maker agreement … or who have been granted access to an advanced tier of Kalshi's API";
  Rule 3.3(b) verbatim as quoted in the docs; Rule 3.3(e); Rule 5.12; Rule 5.19(f) "as if … a single Person";
  the 5.17 prohibited-practice clause "Does not result in a change in beneficial ownership" carries **no intent
  qualifier** (only 3.3(b) "intentionally" and 5.17(bb) "intended" do).
- `docs.kalshi.com/websockets/cfbenchmarks-value`: channel exists, `avg_60s_data` "always present", auth = "API key
  authentication required", no tier/entitlement language on the page.
- Webull FAQ 11053 (live): Index 8:00–16:00 ET; "Crypto, Companies, Financials, Weather, Economic, Culture, and
  Sports-related Event Contracts … are available 24/7"; TIF Day/IOC/FOK/GTC/GTD, quick (market) orders, USD quick
  orders FOK only; Day "Cancels at 12:10 AM the following day"; $0.01 + $0.01 per contract on opening and closing.
- Kalshi fees help page (updated 2026-04-19) defers all formulas to the fee-schedule PDF; maker fees confirmed to exist
  for resting orders on some markets. The "¼ of taker" maker figure in the docs remains third-party.
- **`docs/research/brokers.md` and `docs/research/portfolio.md` do not exist** in the repo or on the branch, although
  `PICADOR_ORCHESTRATION.md` §2 says its venue table is "finalized from the broker research agent; see
  `docs/research/brokers.md`" and the Charter cites `portfolio.md` for sleeve rules.

---

## 1. Ten ways this loses money that the plan under-weights

Ranked by expected damage × probability. Each: mechanism → evidence → what to change.

### 1.1 S1 is a taker strategy against the people who own the feed; the measured markout table is already negative
- **Mechanism.** S1 lifts SIG/Jump/Wintermute offers on the Kalshi book through a Webull REST → Kalshi path (CloudFront
  edge, HMAC, preview, place; hundreds of ms). Picador only gets filled when the maker has not yet re-priced, which is
  disproportionately when the maker is happy to sell. "Picador wins on breadth, not speed" is a hope; breadth does not
  neutralise per-fill adverse selection, it multiplies it.
- **Evidence.** `quant.md` §5.4: of 20 (price-band × time-to-expiry) cells for BTC takers, 13 are negative after the
  2¢ fee; the only reliably positive cells are cheap-side tails in the last 5 min and mid-price takers 15–60 min out,
  one day of data. `quant.md` §7 and Bartlett & O'Hara: index/crypto hourlies are the *efficient* ("broad-based")
  category. `ops.md` §7: a 93%-win-rate bot still lost as taker on efficient series.
- **Change.** Make the Phase-0 go/no-go an out-of-sample forecast test, not an "edge distribution": does `p_fair`
  beat the Kalshi mid on Brier/log-loss at settlement, by hour and price bucket, on ≥ 300 independent events? If
  `p_fair` does not beat the mid, there is no S1 regardless of how large "p_fair − market" looks.

### 1.2 The risk-neutral density systematically over-prices tails; S1 will "find" edge exactly where it loses
- **Mechanism.** 0DTE implied vol sits above realised (quant §3: realised 8% ann. Jul–Sep 2026; 0DTE implieds usually
  above). A risk-neutral (RN) density with σ_RN > σ_P assigns *more* mass to far strikes than the physical measure.
  S1's rule "buy YES when p_fair_low − ask ≥ margin" therefore flags OTM YES (and near-ATM NO) as cheap on every
  quiet day. The market (made by firms with the same chain) already prices closer to physical. `quant.md` §3 calls
  the B-L tail probability "conservative" — that is true only for a tail *seller*; for a tail *buyer* it is the
  opposite. The docs have the sign of this bias backwards.
- **Evidence.** quant §3 "RN vol > realised on average"; A4's measured variance shares; the 2–10¢ tail cells in §5.4
  are positive only in the last 120–300 s (mechanical certainty), and negative (−5.5 to −6.2¢) everywhere earlier.
- **Change.** Calibrate an RN→physical map from the recorder (regress settlement outcomes on p_fair; use the slope as
  the shrinkage, not a flat 50%); forbid tail buys (< 15¢) until that calibration is positive in-sample and out.

### 1.3 The −3%/day loss lock does not cap losses in a hold-to-settlement book
- **Mechanism.** Every loss is committed at entry; the lock only stops *new* opens after the first settlement prints a
  loss. With kickoff defaults (15% per event, 5 open events, no same-direction cap) the committed loss on a trend day
  is up to 75% of equity while the lock still reads "no loss yet". Consecutive hourly NO-above-K positions on a
  selloff day are one bet (quant §6(ii): "perfectly correlated model error").
- **Evidence.** Kickoff "Operator defaults"; quant §6 Monte-Carlo (3 correlated legs at quarter-Kelly → median max DD
  61%, p95 81%; est. 5¢/true 0 → 96%).
- **Change.** Cap *total committed stake* (sum of cost basis of all open positions) at ≤ 10–15% of equity, cap
  same-sign vol/direction exposure across hours at ≤ 2 events, and make the daily lock trigger on
  mark-to-market (Kalshi mid) of open positions, not on realised settlements.

### 1.4 Kelly at high prices plus a 50% flat shrink still over-sizes on estimation error
- **Mechanism.** At 90¢ a 3¢ edge is f* = 37.5% (quant §6); 0.15 × f*(1.5¢) ≈ 2.8% per market, but a 90¢ contract that
  loses costs 100% of stake and the edge estimate at 90¢ is dominated by feed basis and boundary noise (21% of
  settlements within 0.5 pt of a strike). Meister: growth loss is first-order in probability misjudgement.
- **Evidence.** quant §6; quant §1.1 boundary proximity; A2 feed basis 0.05–0.5 pt.
- **Change.** Size on the *shrunk* edge with price-bucket-specific shrink (≥ 80% shrink above 80¢), and hard-cap stake
  per market at a fixed dollar amount during canary regardless of Kelly.

### 1.5 Fixed costs exceed the measurement budget's capacity to pay for them
- **Mechanism.** $3,000 budget, 1–5 contracts per trade, 4¢ minimum net edge → expected profit ≈ 20¢/trade; 400 canary
  trades ≈ $80 expected gross. Against that: VPS $30–80/mo, an OPRA **Non-Display** OpenAPI subscription (price
  unpublished; exchange non-display licences are priced for firms), possibly a Cboe index-value feed for the live SPX
  reference (kickoff assumes "index quote 1 Hz" exists but names no source), plus a CPA opinion. The plan books the
  data subscription as "price only visible in the portal" and moves on.
- **Evidence.** Plan §6 budget; webull_api §7 ("Non-display OPRA is typically priced above display; budget
  accordingly"); B8 (OpenAPI stock L1 is also a paid Nasdaq Basic Non-Display licence).
- **Change.** Get the OPRA-non-display and index-feed prices in writing in week 1; if fixed costs > $100/mo, either the
  venue is wrong (IBKR/direct Kalshi with a different data path) or the project is a paid research exercise and should
  be budgeted as such.

### 1.6 Resting-order pick-off during "emulated IOC" and after crashes
- **Mechanism.** If IOC is not available (B2 unverified), "place + immediate cancel" leaves a live limit on Kalshi for the
  full round-trip latency plus cancel-queue time; it fills only when the book moves through it, i.e. when the price
  is now worse. Any crash, 429 on cancel, or halt window (`OPENAPI_NO_TRADING_TIME`) leaves DAY orders alive until
  00:10 ET; in 24/7 crypto series that is 8+ hours of unattended resting bids against informed makers.
- **Evidence.** webull_api §4 (Day cancels 00:10; no cancel-on-disconnect documented, ops §6); FAQ 11053 verified today;
  kickoff still says "IOC by place + immediate cancel" as fallback.
- **Change.** No live trading until IOC/FOK is verified in sandbox *and* production; if unavailable, S1 must re-price or
  cancel on every book update and a watchdog outside the main process must cancel-all on missed heartbeat.

### 1.7 Money represented as integer cents cannot represent the products
- **Mechanism.** Some event markets tick at $0.0001 (plan §1.3, B7 `price_ranges.step`) and quantities carry 2 decimals
  with fractional fills possible on `fractionable` markets even for whole-number orders. Integer-cent money silently
  truncates cost, fee and PnL → reconciliation never reaches "drift zero" (a gate) or, worse, passes with a
  systematically wrong ledger.
- **Evidence.** Kickoff deliverable 1 "Money is integer cents everywhere"; webull_api §3–4.
- **Change.** Represent money in integer hundredths of a cent (or `Decimal` with explicit quantisation) and quantity in
  integer hundredths of a contract; property-test fee/PnL round-trips against the Order Detail `fees[]` field.

### 1.8 Cross-sleeve buying-power coupling on Webull
- **Mechanism.** Event BP = margin excess of the linked account minus provisional cash; a PDT flag limits it to the
  excess above $25k. The Charter puts CORE equities and CONVEX options on Webull too. A CORE drawdown, an options
  assignment, or three day trades in the linked account zeroes event BP mid-session; a negative FCM balance lets
  Webull liquidate event positions (Rule 3.3(f)).
- **Evidence.** ops §8; Charter sleeve table; plan §1.4.
- **Change.** Dedicated Webull *cash* account for Picador with nothing else in it, or Picador on a broker no other
  sleeve uses.

### 1.9 Settlement misattribution corrupts the very numbers the gates read
- **Mechanism.** gRPC position (settlement) events carry `event_name` + `yes_condition`, no symbol and no
  `client_order_id`; order events carry no fee and no outcome. Two strikes of the same event with similar
  `yes_condition` strings, or a partial fill sequence, get mapped to the wrong strategy/leg; per-bucket realised edge
  and Wilson bounds are then computed on mislabelled trades.
- **Evidence.** B6; webull_api §2; kickoff deliverable 9 relies on these events.
- **Change.** Treat REST positions + Order Detail (with `fees[]`, `event_outcome`) as the settlement source of truth;
  gRPC events are a trigger only. Gate on reconciled rows only (already stated) *and* alert when > 1% of rows are
  `suspect`.

### 1.10 Tax makes a break-even bot a net loser (see §4.4 for numbers)
- **Mechanism.** Under a wagering reading, gross wins are income and only 90% of gross losses are deductible, and
  only as an itemised deduction for a non-professional. High turnover at 55% win rate produces phantom income of
  roughly 20% of gross wins; a standard-deduction filer is taxed on 100% of gross wins.
- **Evidence.** ops §3; addenda C5.
- **Change.** CPA opinion is a Phase-0 exit criterion, not a "before scaling" item; the journal must report gross wins,
  gross losses and net separately from day one.

Honourable mentions: the 8:00–9:30 pre-open window has no 0DTE chain (A9/quant §0.5) and must be gated off; weekend
index books 30–45¢ wide must never count as opportunities; the per-hour variance profile is from 49 days in an 8%-vol
regime with no macro-calendar term (FOMC 14:00, CPI 8:30, opex) — add an economic-calendar gate to the governor.

---

## 2. Internal contradictions between documents

| # | Where | Conflict |
|---|---|---|
| 1 | ORCH §2 vs repo | Venue table "finalized … see `docs/research/brokers.md`" — file does not exist; tastytrade/Public/IBKR/Alpaca fee and event-API columns are "pending". Charter then lists "octopus constraints, broker API facts" under **evidenced**. |
| 2 | Charter §"What is evidenced" vs `portfolio.md` | Charter defers CORE/CONVEX/MACRO rules to `docs/research/portfolio.md`, which does not exist; the capital shares (60–70% CORE etc.) are therefore un-evidenced defaults presented in a table. |
| 3 | Charter build order vs ORCH §5 vs Kickoff | Charter step 1 = "Octopus skeleton + Picador"; ORCH §5 = octopus *after* single-arm Picador is in paper; Kickoff builds a single-process Picador with no head, no NATS, no `Intent/Fill/Position` schema, no `head_epoch`/`owner_id`. Three different first steps. |
| 4 | ORCH §3.4 | "Each [secret] lives in exactly one place (the primary)" vs "the standby must also make an authenticated read call daily" (needs the same App Key + token) and "failover copies them via the encrypted credential store". systemd `LoadCredentialEncrypted` with host/TPM keys is host-bound; the copy is undecryptable on the desktop. |
| 5 | ORCH §3.1/§3.4 vs Kickoff | Lease "row with lease" in "SQLite/Postgres"; state replication "Postgres streaming replica or Litestream". A lease in SQLite replicated by Litestream (one-way, async) cannot arbitrate two hosts. Kickoff uses SQLite only. |
| 6 | Plan §1.2/§5 rule 2 and Kickoff vs webull_api §1 | "Market-data REST 60/60 s **total** / 1 request/s shared" vs "counters are per endpoint" (snapshot+depth+bars+tick = 4 req/s). Kickoff repeats the wrong version. |
| 7 | Plan §1.3 and Kickoff vs webull_api §4 | `client_order_id` "alphanumeric only" — no such rule found; samples use uuid hex. Harmless but stated as fact. |
| 8 | Plan §5 rule 12 vs webull_api §9 / ops §6 | "read-only key where Webull offers scopes" — no scopes exist for individual keys. |
| 9 | Plan §5 architecture vs A7 | "Kalshi public REST/WS (no auth)" — Kalshi WebSocket requires an API key (verified today for `cfbenchmarks_value`; Kalshi WS auth is connection-level). The recorder therefore needs a Kalshi account on day 1, not in Phase 5. |
| 10 | Plan §0 fact (3), §1.4, ORCH §1 "24/7 crypto" as a Kalshi-direct differentiator vs B1 and FAQ 11053 (verified today) | Webull crypto/financial events are 24/7 per the live FAQ; the developer docs say weekdays 8–18. If FAQ is right, S2's "unreachable overnight" premise and one of ORCH's four justifications for a Kalshi arm fall away. |
| 11 | Plan §4 (S4 listed as a strategy; §5 rule 8 "except S4") vs A5/Kickoff ("No S4") | The v1.1 plan body still carries S4 and LIMIT/DAY-only; only the header says the addenda win. |
| 12 | Plan §8 Q2 vs Kickoff defaults | Per-event cap 20% (plan) vs 15% (kickoff); margin 3¢ (plan §4) vs 4¢ min net edge (kickoff); quarter-Kelly (plan) vs 0.15 × Kelly on 50%-shrunk edge (kickoff) vs "≤ ¼ of shrunk Kelly" (quant §6). |
| 13 | Plan §6 vs Kickoff gates | Paper ≥ 200 settlements vs ≥ 300; canary profit factor ≥ 1.5 vs ≥ 1.3; scale gates "N ≥ 30/60" (plan Phase 3, not updated) vs "400 settlements or Wilson LB > 0, whichever first" (kickoff). |
| 14 | Kickoff gate | "Wilson lower bound > 0 at 95%, **whichever comes first**" is sequential peeking; it inflates the false-promotion rate well above 5%. It also counts "settlements", while quant §0.8 says same-hour strikes are one bet. |
| 15 | Kickoff vs ops §6 | Clock-drift halt 250 ms (kickoff) vs 100 ms (ops). |
| 16 | Plan §3 vs Kickoff | Nasdaq series `NASDAQ100I` (plan) vs `KXNASDAQ100U` "verify" (kickoff/quant). |
| 17 | Plan §1.5 vs webull_api §1 | Event snapshot: plan implies 20 symbols/call (options figure); event snapshot accepts 100. |
| 18 | Plan §1.4 vs Charter drawdown ladder | Plan's daily lock is on "start-of-day equity" of the event account; Charter's ladder is on "total equity, one equity curve". Which equity gates Picador is undefined. |
| 19 | ORCH §1 item 4 / addenda C1 vs ORCH §2 role column | Webull is "under state enforcement" (redundancy argument) yet remains "primary event arm"; no trigger is defined for switching primary. |
| 20 | PREDICTION_MARKET_ARB_STRATEGY §1 vs ops §5 | Polymarket US theta 0.06 vs 0.0695 (effective 2026-09-17). Plan §0 says the older doc's fee research "still applies". |
| 21 | quant §3 internal | "B–L gives a conservative tail probability" contradicts the same section's σ_RN > σ_P statement for a tail buyer (see §1.2). |
| 22 | webull_api §1 vs SDK | Sandbox MQTT host: docs `data-api.sandbox.webull.com`, skills repo `api.sandbox.webull.com`, SDK `endpoints.json` has no sandbox entry at all. |
| 23 | Kickoff STEP 0 | Clones `drpostman123/PROMPTWARRIORS_V3` branch `claude/prediction-market-arbitrage-dxi93z` over plain HTTPS; assumes the repo is public/reachable from the desktop without credentials. Unverified. |

---

## 3. Single points of failure in the octopus, with mitigations

| SPOF | Failure story | Mitigation |
|---|---|---|
| **Leader lease** (row in Postgres/SQLite on the VPS) | The lease store lives on the primary. If the VPS is up but partitioned from the desktop, the desktop cannot read the lease, times out on heartbeats, runs its "cancel-all then promote" rule, and starts cancelling a healthy primary's orders through the broker APIs while the primary re-places them: cancel/place war, 600/60 s exhaustion, IP block (webull_api rate-limits page). "Two live heads impossible" is false because *cancel-all is itself a live action* taken without the lease. | The lease must live on a third party neither host owns (a $5 KV/VPS, DynamoDB conditional write, or Consul on a third node), with fencing tokens (`head_epoch`) that arms verify against that store, not against the head. **Manual promotion only** for v1; automatic promotion requires a witness (external health check) plus a minimum outage (≥ 15 min) plus flat positions. |
| **Secrets** (App Secret, Webull token dir, Kalshi RSA key) | `systemd-creds` host-bound encryption cannot be copied to another host; the token dir is *mutable* (SDK rewrites it), so a stale copy on the desktop is INVALID; "one place" and "standby keeps it alive daily" conflict. Key reset invalidates the old key instantly with no overlap. | Encrypt with `age`/`sops` to two recipient keys (one per host), distribute via git-crypt or a private object bucket; one host holds the *live* token, the other holds only the App Key/Secret and creates its own token on promotion (needs the phone). Rehearse key rotation as a cut-over with the bot halted. |
| **2FA token / the phone** | Token INVALID after 15 idle days, on key reset, on Webull-side invalidation; renewal needs in-app approval within 5 min. Operator travelling or phone dead → no trading *and no API cancel*. The "out-of-band kill switch with a separately stored key" needs a second App Key, which may not exist for individuals (unverified). | Keepalive + age alert (already planned). Runbook step 0: cancel orders **from the Webull app** (human path independent of the API). Test whether a second App Key can be issued; if not, the out-of-band killer is the phone. Keep positions hold-to-settlement so a dead bot is not a loss, and never leave resting orders across a period the phone is unreachable. |
| **Broker API changes** | Webull moved hosts 2026-07-08, changed pagination and `category_id` type 2026-09-05, docs and SDK disagree on HMAC algorithm and TIF; 14 open SDK issues, none answered; issue #13 (no order events) and #17 (sandbox routes to prod) are live. Kalshi rules changed Source Agency in Nov 2024 and can swap it again (Rule 7.2). | Pin the SDK; daily contract test against saved response shapes (`docs/api-shapes/`) that fails closed (halt, alert) on new/missing fields; run a 1-contract canary order every trading day and reconcile it end-to-end; subscribe to the changelog page and `llms.txt` diff; keep the Kalshi-direct adapter compilable even if unused. |
| **Rate-limit budget shared across hosts** | Limits are per App Key, not per host (webull_api §1, rate-limits page). A "warm standby with arms connected read-only" consumes the same 5 MQTT connections, 60/60 s subscribe budget and 2/2 s account reads as the primary — the standby degrades the primary. | Standby holds no Webull connections; it reads Kalshi public data only and restores state from the replica. On promotion it opens connections. |
| **NATS JetStream** | Single node on the VPS; if it dies, arms self-cancel (good) but the head cannot fan out `risk.kill`; JetStream on one disk. | For one operator on one VPS, drop NATS in v1 (single process, in-memory queues); when arms exist, make the kill path independent of the bus (arms poll a kill flag in the lease store every 2 s; head can also call each arm's local HTTP `/kill`). |
| **Postgres streaming replica over WireGuard to a residential desktop** | Desktop offline (ISP, power, sleep) → replication slot retains WAL → VPS disk fills → Postgres stops → head halts mid-session with open orders. Classic. | `max_slot_wal_keep_size`, disk alert at 70%, or prefer SQLite + Litestream to S3 (no coupling; desktop restores from S3). Accept RPO of seconds: on promotion, **reconcile from broker open orders/order history before any new intent** and derive `client_order_id` from `head_epoch` + random so a behind-replica counter never reuses an id. |
| **Desktop failover host** | Same box is dev machine, paper environment and standby: a `git checkout`, a test run or an `apt upgrade` restart can flip state; residential NAT/dynamic IP; not in us-east-1. Auto-promotion from it is the most dangerous line in the design. | Standby = cold restore drill target only, until there is evidence the strategy deserves HA. If HA is wanted, a second $5 VPS in another AZ is safer than the desktop. |
| **Kalshi as the single exchange behind five arms** | Kalshi halt (2026-04-15), Rule 5.12 FCM suspension cancelling all Webull orders, market "under review" up to 24 h, court-ordered voiding. The octopus diversifies brokers, not the exchange. | Reconciliation must tolerate cancelled/voided fills; capital sweep weekly; ForecastEx via IBKR is the only genuinely different book. |
| **Webull as an enforcement target** | CT C&D and KY suit are sports-only today; Webull ToS §3.3 lets it suspend products at will; a product pull or state geofence strands positions (hold to settlement is fine; new opens stop). | Sports never; state check quarterly; "Webull pulls index/crypto events" is the trigger for promoting the Kalshi-direct arm, with the 3.3(b) email already acknowledged. |

---

## 4. Compliance and tax traps

### 4.1 Self-match across accounts (Kalshi 3.3(b), 5.17)
- **Mechanism.** Buying NO on arm B while arm A holds YES is a cross; so is arm A's SELL-to-close meeting arm B's bid;
  so is a forgotten DAY order (alive to 00:10) meeting the other arm next morning. 3.3(b) says "intentionally", but the
  5.17 clause "does not result in a change in beneficial ownership" has **no intent qualifier**; an accidental cross
  is still a reportable wash. The ORCH lock is keyed on `(market, side)` and on *opening*; closes and resting tails
  are the gap.
- **Control.** Lock on `market` for the full life of any own position or order in any arm (one arm per ticker,
  period); pre-trade check against every arm's open-order cache; post-trade detector matching fills across arms by
  ticker/time/price and alerting; disclosure email before the second account's first trade, acknowledgement in the
  repo; include the human's manual app trades (Robinhood, Webull app) as an "arm" in the lock.

### 4.2 Position-limit and accountability aggregation (3.3(e), 5.18, 5.19(f))
- **Mechanism.** INX $7M and BTC $1M/strike are not binding for Picador, but Position Accountability Levels are lower,
  reportable levels are lower still, and the Charter's MACRO sleeve uses Kalshi economics contracts (Fed/CPI) whose
  limits are far smaller. Aggregation is across Webull + Kalshi direct + IBKR + Robinhood + anyone trading "pursuant
  to an express or implied agreement" (a co-founder). First breach = warning letter, second within 12 months =
  disciplinary proceeding.
- **Control.** Per-series limit table in config (from contract terms), summed across arms and sleeves, alert at 50%,
  reject at 80%; no second human ever trades the same series.

### 4.3 Rule 5.11 error trades — $3,000, no appeal, no ATS exception for FCM customers
- **Mechanism.** (i) Picador's own off-market order (bug, stale book, wrong outcome flag) fills outside fair ± 20¢; the
  counterparty requests review within 15 min; Picador pays $3,000 plus the loss. (ii) Picador lifts a *counterparty's*
  erroneous quote (quant §5.2 shows placeholder 0.96×12 asks in every unquoted range at open; weekend books 0.00/0.46);
  Kalshi cancels; who "resulted in" the cancellation is Kalshi's discretion and final. (iii) The kickoff's band is
  "±10¢ from Kalshi mid" — undefined when one side is empty, and mid of 0.00/0.46 is 23¢, so the band permits buying
  at 33¢ in a market worth 5¢.
- **Control.** Band against *model fair value* and last trade, not mid; refuse when spread > 10¢ or either side empty;
  refuse the first 60 s after an event opens; per-order notional ≤ $500 in canary; flag any fill > 15¢ from fair as
  a suspected error trade and do not hedge or size on it; hold a $3,000 reserve outside trading capital; log the
  Kalshi review window so a request can be made within 15 min if Picador is the victim.

### 4.4 Wagering-loss cap and phantom income (§165(d) as amended by OBBBA §70114, tax years after 2025)
- **Mechanism.** If event contracts are wagering: every winning contract's payout − cost is a win, every losing
  contract's cost is a loss; only 90% of losses deduct, only against wins, and for a non-professional only on
  Schedule A. Example at 55% win rate, 50¢ contracts, 1,000 contracts: wins 550 × $0.48 = $264, losses 450 × $0.52 =
  $234, real profit $30. Itemiser: taxable $264 − 0.9 × $234 = $53 → ~$13 tax at 24% (43% of profit). Standard-deduction
  filer: taxable $264 → ~$63 tax, **more than double the profit**. A 10,000-contract/month bot at this edge is a
  guaranteed after-tax loser. §1256 60/40 is the alternative reading but is "aggressive" and conflicts with
  Kalshi's own swap characterisation (§1256(b)(2)(B) excludes swaps).
- **Control.** CPA opinion is a Phase-0 gate; journal reports gross wins / gross losses / net and both tax views daily;
  the graduation gate must be on *after-tax* edge under the worse reading; consider whether the operator itemises;
  keep the option of an entity/trader-status structure open before scaling; Webull's 1099 form type in writing
  (futures@webull.com) before the first live trade.

### 4.5 Wash sales across brokers (§1091)
- **Mechanism.** For hourlies, low materiality (each ticker dies within the hour; a loss-sale + rebuy inside the hour
  defers a loss that is realised the same year) unless capital treatment applies and a loss straddles year-end. The
  real exposure is the Charter's CORE/MACRO/CONVEX sleeves: sell SPY at a loss on Webull, buy SPY (or an option on
  it) on tastytrade/Public within 30 days; brokers only track within-account, the taxpayer must adjust, and a purchase
  inside an IRA disallows the loss permanently.
- **Control.** One consolidated journal keyed by CUSIP/ticker across all arms; a 31-day same-symbol cross-broker
  freeze after any loss sale; CORE rebalancer uses non-identical substitutes; year-end report flags open event
  positions and December loss sales.

### 4.6 State enforcement and exchange-level invalidation
- **Mechanism.** Today sports-only (CT C&D 2026-09-10 names Webull; KY suit; 9th Cir. 2026-08-28; NJ cert petition).
  Tail paths that reach index/crypto: Webull withdraws the product or geofences the operator's state; Kalshi suspends
  Webull (Rule 5.12: all customer orders cancelled, subsequent trades invalid); a state court orders resident trades
  voided (Michigan, July 2026) — settled wins can be clawed back after the fact.
- **Control.** Record the operator's state and re-check its status monthly; sweep profits to the bank weekly so
  event-account balance stays near the working minimum; reconciliation tolerates cancelled and voided fills; keep the
  Kalshi-direct route disclosed and warm.

### 4.7 Account-operation and secrets hygiene (Webull ToS §2, Kalshi 3.5(f))
- **Mechanism.** Only the account owner may operate the account; keys handed to a cloud agent runtime or a
  collaborator are a "third party". Market data licences are Non-Display: a public dashboard of Webull quotes breaches
  them.
- **Control.** Keys only on operator-controlled hosts; Claude Code sessions that touch live keys run on the desktop, not
  in remote sandboxes; publish only Kalshi public data.

---

## 5. What to cut, and the order to build in if survival is the priority

### Cut or defer (out of v1 entirely)
1. **The octopus** (head, NATS, arms, lease, shadow arms, router). It solves a fee-routing problem worth ≤ 0.25¢ per
   contract at mid (ops §5) while adding every SPOF in §3. Build one process, one broker, one account.
2. **Desktop warm standby and any automatic failover.** A hold-to-settlement bot with no resting orders loses nothing
   when it is down. Replace with a documented cold-restore drill.
3. **Robinhood crypto arm, tastytrade/Public/IBKR/Alpaca arms, GRIND-CRYPTO, MACRO, CONVEX** until Picador has
   ≥ 1,000 reconciled settlements. The Charter's "one sleeve live before the next" already says this; the ORCH
   build order does not respect it.
4. **S2 Deribit density, S3 executor, S4 in any form, sports.** S3 scanner stays only if it costs < 1 day.
5. **Postgres, Prometheus + Telegram + healthchecks trio.** SQLite; one alert channel plus a dead-man ping.
6. **Kelly.** During canary, fixed stake per market ($25–50) and fixed max committed stake; Kelly only after the
   RN→physical calibration exists.
7. **The "index quote 1 Hz" and SPX/XSP options from Webull** if the OPRA non-display price is not trivial; the whole
   S1 data path should be priced before it is coded.

### Build order for survival
0. **Decide the venue before writing an executor.** Price IBKR's Kalshi routing (commission, hourlies exposed via TWS
   API, no OPRA-non-display requirement) and Kalshi direct (taker fee ~equal at mid, IOC/FOK native, `cfbenchmarks`
   and orderbook WS included, error-trade rule identical). Webull's only advantages are a flat fee and a paper
   environment; both alternatives may remove three unverified items at once. Kickoff deliverable 11 must move before
   deliverable 2, not sit at 11.
1. **Recorder first, with zero broker keys**: Kalshi public REST (books, trades, candles), Kalshi authenticated WS
   (`cfbenchmarks_value`, orderbook) from a KYC'd data account, whichever options and index feed is chosen. Runs on
   the desktop. Nightly report.
2. **Forecast test on recorded data**: `p_fair` vs Kalshi mid on Brier/log-loss at settlement by hour and price bucket,
   ≥ 300 independent events, with the RN→physical calibration slope. This is the go/no-go for the whole project;
   nothing in the executor is worth building before it passes.
3. **CPA opinion and after-tax edge** under the wagering reading, with the Phase-2 numbers. Second go/no-go.
4. **Phase-0 API tests in sandbox** (§6 below), recorded in `docs/api-shapes/`.
5. **Governor + paper executor** with the caps in §1.3 (committed-stake cap, same-direction cap, MTM lock), integer
   hundredths-of-cent money, reconciliation that tolerates voided fills.
6. **Live canary** on a dedicated cash account, fixed $25–50 stake, S1 only, no resting orders, 1-contract daily
   heartbeat order, $3,000 reserve outside the account.
7. **Gates** in independent events, fixed-horizon (no peeking), reconciled rows only.
8. Only then: second account (Kalshi direct or IBKR) with the 3.3(b) email acknowledged, the per-ticker lock, and a
   third-party lease store. The octopus, if ever, grows from that.

---

## 6. The 15 most important unverified assumptions, as tests for the first coding session

Each is pass/fail and should be recorded with the raw response in `docs/api-shapes/`.

1. **Sandbox has an events account and simulates event fills.** Test: sandbox `account list` contains
   `account_class=EVENTS_CASH`; place a 1-contract LIMIT buy at the current Kalshi ask on a live KXBTC range; observe
   an order event with `FINAL_FILLED` within 60 s and a position. (Issue #6 says the API supported only cash/margin.)
2. **IOC / FOK / GTC are accepted for `instrument_type=EVENT`.** Test: place one order per TIF in sandbox; record
   status and any 417 `error_code`; repeat once in production with 1 contract at 1¢ far OTM and cancel.
3. **Crypto/financial events trade through the API outside Mon–Fri 08–18 ET.** Test: on a weekend, place a 1¢ sandbox
   order on a KXBTC hourly; expect no `OPENAPI_NO_TRADING_TIME`; verify MQTT `event-quote` updates arrive on Saturday.
4. **`option-snapshot` returns SPXW/XSP quotes with bid/ask**, and the OPRA non-display subscription's monthly price.
   Test: contract list `root_symbol=SPXW, expired_cycle=DAILY, start_date=today`, then snapshot 20 of them; assert
   `bid`, `ask`, `imp_vol` non-null. Record the portal price.
5. **A live SPX (not SPY) index value is obtainable at ≥ 1 Hz from some feed the plan can pay for.** Test: name the
   endpoint/category, fetch 60 s of values, compare to Kalshi's next `expiration_value` at HH:00:00.
6. **A retail Kalshi API key receives `cfbenchmarks_value` with `avg_60s_data`** (docs say API key only). Test:
   subscribe, log 10 minutes, verify the 60-s average recomputed from the 1 Hz stream matches `avg_60s_data` and that
   the settled `expiration_value` equals the average at HH:00.
7. **gRPC order events actually arrive for event orders** (issue #13) and **position settlement events map to a
   ticker.** Test: 1-contract sandbox and production orders; count `SubscribeSuccess` vs order events; at settlement
   capture the position event and show a deterministic join to the Kalshi ticker.
8. **Per-order cap and preview behaviour.** Test: preview 60,000 contracts at 1¢ and 1,000 contracts at 99¢ in sandbox;
   record which is rejected and the `error_code`.
9. **Rate-limit counters are per endpoint and per App Key across hosts.** Test: 61 snapshot + 61 depth calls in one
   minute from one host (expect 429 only per endpoint); then 40 + 40 from two hosts (expect shared budget). Decide
   the standby design from the result.
10. **MQTT throughput and symbol cap.** Test: subscribe 100, 200, 400 symbols across 1–2 connections; measure messages/s
    per connection (docs say ≤ 3), whether updates are conflated or dropped, and whether reconnect restores anything.
11. **Token and key concurrency.** Test: use the same App Key + token from the desktop and a VPS at once for 24 h;
    confirm neither is invalidated and a read call from host B keeps the token alive. Test whether a second App Key
    can be issued to one individual account.
12. **Fee and settlement accounting matches the ledger.** Test: one live 1-contract round trip and one hold-to-settle;
    compare Order Detail `fees[]`/`commission{}` and the statement to `0.02 × contracts`; include one `fractionable`
    and one $0.0001-tick market.
13. **Webull book == Kalshi book, and the lag.** Test: record Webull MQTT `event-quote` and Kalshi WS `orderbook_delta`
    for the same 20 tickers for one hour; report price mismatches and the lag distribution; list Kalshi markets that
    Webull shows as `NT` or omits.
14. **The thesis: `p_fair` beats the market.** Test (offline, recorder data): for ≥ 300 independent index events, Brier
    score of `p_fair` (SPX B-L, per-hour variance, live index reference) vs Kalshi mid 30 min before expiry; report by
    hour and price bucket with a calibration slope. Pass = `p_fair` strictly better and slope > 0 out-of-sample.
15. **Cancel-on-disconnect does not exist, and cancel-all on restart works.** Test: place a sandbox order, `kill -9` the
    process, confirm via REST the order is still open 5 min later, restart, confirm it is cancelled within 10 s.

Non-code gates that belong in the same week: Webull's tax form for the event account in writing; the OpenAPI
agreement text saved at application; the operator's state on the current enforcement lists; the
`drpostman123/PROMPTWARRIORS_V3` branch reachable from the desktop with the clone command in the kickoff.
