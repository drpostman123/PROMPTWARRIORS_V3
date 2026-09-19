# GRIND-PM — Event-Contract Routing Layer Specification (v0.1, 2026-09-19)

Scope: the routing layer that sits between the head's strategy/fair-value service and the
three API-capable event-contract routes: **KD** = Kalshi direct (REST/WS v2), **WB** = Webull
OpenAPI, **IB** = IBKR TWS/Web API (Kalshi + ForecastEx + CME). Everything else in the octopus
(fair value, NATS bus, leader lease, journal) is assumed from `PICADOR_ORCHESTRATION.md`.
Every number below is tagged **[V]** verified today from primary docs, **[D]** taken from the
repo research docs (`brokers.md`, `quant.md`, addenda), or **[U]** unverified → §11 test.

Design rule that drives everything: **KD, WB and IB(Kalshi) are the same order book.** Only
IB(ForecastEx) and IB(CME) are different books. The router therefore has two jobs that must
never be confused: (a) *fee/capital/availability routing* into one Kalshi book without ever
matching ourselves, and (b) *cross-exchange* trades between Kalshi and ForecastEx.

---

## 1. Route fact table

| Item | KD — Kalshi direct | WB — Webull OpenAPI | IB — IBKR TWS/Web API |
|---|---|---|---|
| Books reached | Kalshi | Kalshi | Kalshi **[U: contract addressing]**, ForecastEx, CME |
| Order types | limit only [V] | LIMIT only [D] | limit only ("Event Contracts only support Limit Orders") [V] |
| TIF | `good_till_canceled` (+ `expiration_time` unix-s → GTD), `immediate_or_cancel`, `fill_or_kill` [V] | `DAY` documented; JSON schema lists GTC/IOC/GTD/FOK **[U]**; DAY cancels 00:10 ET [D] | Day, GTC, IOC [V] |
| Sides | v2: `side=bid` (buy YES) / `ask` (sell YES); short = ask without position → NO position **[U: confirm in demo]**; `reduce_only` available [V] | BUY-to-open / SELL-to-close only; short = BUY NO [D] | buy only for ForecastEx; no short selling; exit = buy opposing contract, IB nets [V]. Kalshi legs via IB: Yes/No as Call/Put presumably **[U]** |
| Price / qty format | fixed-point dollar strings, `count` 2-decimal string [V] | $0.01–0.99, qty 0–2 dp [D] | strike/right on options model; whole contracts only, no cash quantity [V] |
| Maker/taker fee by price p (per contract) | taker `0.07·p(1−p)` → 0.33¢ @5¢, 1.12¢ @20¢, **1.75¢ @50¢**; maker `0.0175·p(1−p)` → 0.08¢ / 0.28¢ / **0.44¢**; per-fill, rounded up to $0.000001, per-order accumulator; direct members settle at $0.0001 precision [V] | flat **2.0¢/side** every price; settlement free [D] | Kalshi: **$0.01 IBKR + $0.01 exchange = 2.0¢**; ForecastEx: **$0.00 + $0.01 = 1.0¢**; CME: $0.01 + $0.01 [V] |
| Self-trade prevention | `self_trade_prevention_type` **required**: `taker_at_cross` (cancel incoming; already-matched partials stand) or `maker` (cancel own resting order, keep matching) [V] — sees only this member's orders | none | none (and IB's cross-exchange "best net price" router can send a Kalshi-book order for us [V] → §6.4 pin exchange) |
| Post-only | `post_only` bool (default false) [V] | no | no |
| Risk primitives | `order_group_id` with `contracts_limit` 1–1,000,000 over rolling 15 s; triggered → cancel-all + block until reset; manual trigger; `cancel_order_on_pause` [V] | preview (fee + est. cost) [D] | none beyond account-level |
| Amend | amend keeps queue priority only when decreasing size; `decrease-order` endpoint [V] | replace by client_order_id [D] | modify order |
| Batch | v2 batch create/cancel, size scales with tier write budget; 10 tokens per order; v1 batch cancel 20 (advanced) [V] | batch place EQUITY-only [D] | n/a |
| Rate budget | tokens/s read/write: Basic 200/100, Advanced 300/300 (self-serve upgrade), Expert 600+; most calls 10 tokens; Basic write bucket holds 1 s, higher hold 2 s [V] | place 600/60 s, cancel 600/60 s, preview 150/10 s, balance/positions/open-orders **2/2 s**, REST market data 60/60 s [D] | TWS pacing 50 req/s (100 booster); Web API 10 req/s [D] |
| Hours | Kalshi hours per series; crypto hourlies 24/7 incl. weekends [D] | index Mon–Fri 08–16 ET; crypto 08–18 ET vs FAQ "24/7" **[U]**; sports 24/7 [D] | per exchange; ForecastEx/CME hours **[U]** |
| Per-order caps | position limits: INX $7M/member, BTC $1M/strike [D]; order cap not published **[U]** | 50,000 contracts/order (or 500,000 / $50k) **[U]** → hard guard $50k notional [D] | **[U]** |
| Sandbox | demo: `https://external-api.demo.kalshi.co/trade-api/v2`, `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`, separate keys, fake funds, "prices may not reflect real markets" [V] | sandbox = paper; separate sandbox App Key auto-approved; 30/60 s limits [D] | paper account exists; top-of-book fills; **event contracts in paper [U]** [V] |
| Data | public REST orderbook (no auth); WS orderbook deltas, user fills (`is_taker`, `fee_cost`, `count_fp`, `yes_price_dollars`, `ts_ms`) [V]; authenticated WS carries BRTI 1 Hz/5 Hz [D] | MQTT event-quote/snapshot/tick, ≤5 conns/App Key, ≤3 msg/s/conn, subs not restored on reconnect [D] | BID = "Highest Bid", ASK = "Buy Yes Now at"; no last/trade history for ForecastEx [V] |
| Funding latency | ACH out free, 3–5 business days (1–2 to initiate); debit card 1–3; wire only ≥ $500k [D-search] | ACH in 3–5 bd to settle (provisional ≤ $1k); ACH out 2–5 bd; $50k/day withdrawal cap [D-search] | ACH in: 4 bd hold; out: to originating bank after 5 bd, elsewhere after 44 bd [D-search] |
| Compliance identity | Self-Clearing Member (Rule 3.3(c): must name FCMs) | FCM customer of Webull Futures LLC | FCM customer of IBKR LLC |

Fee consequence that the router must respect: **KD is the cheapest route at every price, for
both taker (≤1.75¢ vs 2¢) and maker (≤0.44¢).** WB and IB(Kalshi) are never chosen on fee;
they are chosen on capital location, rate budget, route health, or FCM redundancy (§3).

---

## 2. Cross-venue resting-order registry and ownership lock

### 2.1 Why one registry
Kalshi's STP only sees orders under the KD member id. An IOC sent through WB can lift a KD
resting order; an IB order can match a WB order. Rule 3.3(b) / 5.17(c) violations are
strict in form. So the head owns a single, transactional registry (Postgres; SQLite+Litestream
acceptable single-node) and **no arm may send an order without a token minted from it.**

### 2.2 Tables (minimum)
```sql
-- one row per Kalshi/ForecastEx market
CREATE TABLE market_lock (
  book        TEXT NOT NULL,            -- 'KALSHI' | 'FORECASTX' | 'CME'
  ticker      TEXT NOT NULL,            -- Kalshi ticker or IB conId string
  state       TEXT NOT NULL,            -- FREE | RESERVED | HELD | DRAINING | FROZEN
  dir         TEXT,                     -- 'YES' | 'NO'  (economic direction of ALL live orders)
  routes      TEXT[] NOT NULL DEFAULT '{}', -- routes currently holding orders/reservations
  epoch       BIGINT NOT NULL,          -- head epoch that granted it
  lease_until TIMESTAMPTZ,              -- for RESERVED only
  updated_at  TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (book, ticker)
);
CREATE TABLE resting_order (
  intent_id        UUID NOT NULL,
  route            TEXT NOT NULL,       -- KD | WB | IB
  client_order_id  TEXT NOT NULL,       -- ≤32 alnum for WB; uuid for KD; orderId for IB
  venue_order_id   TEXT,
  book             TEXT NOT NULL, ticker TEXT NOT NULL,
  dir              TEXT NOT NULL,       -- YES | NO (economic)
  action           TEXT NOT NULL,       -- OPEN | CLOSE
  price_c          INT NOT NULL,        -- price in cents of the *dir* contract
  qty              NUMERIC NOT NULL, filled NUMERIC NOT NULL DEFAULT 0,
  tif              TEXT NOT NULL, post_only BOOL NOT NULL DEFAULT false,
  state            TEXT NOT NULL,       -- PENDING_ACK | LIVE | CANCEL_SENT | UNKNOWN | DONE
  placed_at        TIMESTAMPTZ NOT NULL, last_venue_ts TIMESTAMPTZ,
  deadline_at      TIMESTAMPTZ NOT NULL,-- hard TTL: arm cancels at/after this
  PRIMARY KEY (route, client_order_id)
);
CREATE TABLE position_by_route (
  route TEXT, book TEXT, ticker TEXT, yes_qty NUMERIC, no_qty NUMERIC, cost NUMERIC,
  venue_ts TIMESTAMPTZ, PRIMARY KEY (route, book, ticker)
);
```
Economic direction normalisation (the whole guard depends on it): a `buy YES @ p` and a
`sell YES @ p` (KD `ask`) / `buy NO @ 1−p` are opposite; **any order with dir=YES can cross any
order with dir=NO on the same book at some price**, so the invariant is stated on `dir`, not
on venue-specific side fields.

### 2.3 Invariants (enforced in one SQL transaction per token)
- **I1 (one direction per book+ticker):** `market_lock.dir` is the only direction any route may
  have in `PENDING_ACK|LIVE|CANCEL_SENT|UNKNOWN` orders. Same-direction orders on several
  routes are allowed (bids cannot match bids).
- **I2 (flip requires drain):** to place a `dir ≠ lock.dir` order the lock must first pass
  through `DRAINING` and reach `FREE`, which requires *venue-confirmed terminal state*
  (KD: cancel response / order status `canceled` with `remaining_count=0`; WB: order event
  `CANCELLED`/`FINAL_FILLED`; IB: `orderStatus Cancelled/Filled`) for **every** row of that
  book+ticker across all routes. Timers never release a lock; only acks or a manual override
  logged under §6.5.
- **I3 (takers lock too):** an IOC/FOK is a resting order for the milliseconds it lives; it
  gets a `RESERVED` token with a 5 s lease and is inserted as `PENDING_ACK`. A `taker_at_cross`
  reject or an IOC that returns nothing releases it.
- **I4 (unknown is held):** a place/cancel that times out becomes `UNKNOWN`, the lock stays
  `HELD`, and only a reconcile read (KD `GET /orders/{id}`; WB `get_order_detail`; IB
  `reqOpenOrders`/`reqExecutions`) can move it. Reconcile runs every 5 s while any UNKNOWN exists.
- **I5 (closing never crosses opening):** a `CLOSE` of a YES position is a dir=NO order
  economically. Closes therefore go through the same lock: you cannot rest a YES bid on KD and
  sell YES on WB. Closing on the same route as the position is the only place a close can
  happen anyway (positions are per FCM); the head must drain other routes' same-ticker orders
  first.
- **I6 (epoch fencing):** every token carries `head_epoch`; arms reject tokens with an epoch
  older than the newest seen (standby-promotion guard from the orchestration doc).

### 2.4 State machine
```
FREE ──reserve(intent)──▶ RESERVED(lease 5 s) ──order acked LIVE or filled──▶ HELD
  ▲                             │ lease expiry w/o send, or venue reject               │
  │◀────────────────────────────┘                                                      │
  │                                                                                    ▼
  │◀── all rows terminal (acked) ── DRAINING ◀── flip requested / route down / kill ──┘
FROZEN: entered on Rule 5.12 event, manual hold, or reconciliation mismatch; exits only by operator.
```
`HELD` with same dir accepts new reservations from any healthy route (join). `DRAINING`
refuses all new reservations and fans out cancels to every route in `routes[]`.

### 2.5 TTLs and leases
- Reservation lease: 5 s (taker) / 15 s (maker place); renewals not allowed — resend intent.
- Resting-order `deadline_at`: maker orders max 20 min or `min(20 min, T_close − 90 s)`; KD
  orders also carry `expiration_time = deadline_at` so the exchange enforces it if we die [V].
  WB DAY orders get a bot-side deadline (no GTD relied upon until §11 test passes). IB Day/GTC
  get a bot-side deadline.
- Arm heartbeat 5 s; arm loses head for 15 s → arm cancels *all* its resting orders
  (self-cancel-on-disconnect); head marks those rows `CANCEL_SENT` and waits for acks.
- Leader lease (head): 10 s TTL; standby may only promote after verifying zero resting
  orders per route via venue reads.

### 2.6 How Kalshi's own STP flag is used
- Every KD order sends `self_trade_prevention_type=taker_at_cross` **by default**: if our own
  registry is ever wrong about KD-vs-KD (e.g. two head epochs briefly alive), the exchange
  cancels the incoming order rather than matching. Partial fills already matched stand [V],
  so this is a backstop, not the guard.
- `maker` is used only by the flatten path (`reduce_only=true`, IOC): it removes our own
  resting quote and keeps matching against others — exactly what an emergency exit wants.
- `post_only=true` on every maker order (§4) rejects any crossing placement outright.
- `order_group_id`: one group per strategy per route with `contracts_limit` = the strategy's
  15-s fill cap (start 200 contracts); a triggered group is surfaced as a route-level halt.
- Subaccounts (`subaccount` int) are **not** used for routing separation: they share the
  member id, and the registry is the source of truth regardless.

### 2.7 Tracking WB and IB orders without STP
- WB: `client_order_id` = 32-char alnum `<proc4><epoch6><seq22>`; every place goes
  PENDING_ACK → LIVE on the gRPC `order` event (or REST `get_order_detail` fallback within 2 s,
  because of SDK issue #13: subscribe success with zero events [D]). Fills come only as order
  events without fees [D]; fee is computed as `0.02·filled` and reconciled against statements.
- IB: `orderId` from `reqIds`; `openOrder`/`orderStatus`/`execDetails`/`commissionReport`
  callbacks; `reqOpenOrders` on connect to adopt strays; `reqAllOpenOrders` on standby
  promotion. Exchange pinned (§6.4) so we know which book each row is on.
- Both routes get a **shadow STP** in code: before sending an order the arm re-reads the
  registry row and refuses if `dir` mismatches (belt) in addition to the head's token (braces).

---

## 3. Routing function

### 3.1 Inputs
`Intent{book, ticker, dir, action(OPEN|CLOSE), price_bucket ∈ {≤10¢, 10–30, 30–70, 70–90, ≥90},
limit_price_c, qty, urgency ∈ {MAKE, TAKE_PATIENT, TAKE_NOW, FLATTEN}, strategy_id, deadline,
fair_value_c, fv_band_c}`.

### 3.2 Per-route cost model (cents per contract, all-in)
```
cost(r) = fee(r, p, maker?) + slip(r) + miss(r) + capital(r) + rate(r)
fee(KD, p, taker)  = ceil6(7·p·(1−p))           ; fee(KD, p, maker) = 1.75·p·(1−p)
fee(WB, p, ·)      = 2.0                         ; fee(IB_kalshi, p, ·) = 2.0
fee(IB_fx, p, ·)   = 1.0                         (ForecastEx exchange fee only)
slip(r)  = measured (§9) median adverse move between decision and ack on route r, by bucket
           (initial priors: KD 0.0, WB 0.5 for TAKE_NOW at touch, IB 0.5) — WB/IB add a hop.
miss(r)  = P(miss | r) × opportunity_value  (only for TAKE_*; from shadow report; prior 0)
capital(r) = 0 if free BP on r ≥ notional, else +∞ (routes never borrow)
rate(r)  = +∞ if r's write budget in the next 1 s < cost of order + one cancel (reserve 30 %
           of every route's write budget for cancels/risk at all times)
```
Hours penalty is a hard constraint, not a cost.

### 3.3 Constraints (all hard, evaluated in this order; first failure explains the refusal)
1. `kill_switch == off`, `head_epoch` current.
2. Compliance gate (§6.1) passed for every route that is not the *first* route.
3. Ownership lock (§2.3) grants `dir` on `(book,ticker)` for candidate route.
4. Aggregate exposure (§6.2): post-trade contracts in ticker, event and series ≤ caps.
5. Price band and notional (§6.3).
6. Route health: heartbeat < 15 s old, no UNKNOWN rows older than 60 s, venue status
   `open` for the ticker, MQTT/WS data age < 3 s.
7. Hours: category open on route (WB index 08–16 ET; crypto window per §11 result); for
   MAKE intents refuse within 90 s of close; for TAKE refuse within 30 s of close.
8. Per-order caps: qty ≤ min(route cap, $50k notional guard, strategy cap).
9. Buying power on route ≥ notional (+ maker collateral already committed).
10. Rate budget as in 3.2.

### 3.4 Tie-breaks
1. Lowest `cost(r)`.
2. If within 0.1¢: prefer the route already in `market_lock.routes` (keeps one route per
   ticker, simplifies reconciliation).
3. Then the route with more free BP as a fraction of its allocation (§7 rebalancing pressure).
4. Then the lowest measured p50 ack latency last 10 min.
5. Then a fixed order KD > IB > WB (WB last because DAY-only and 2/2 s account reads).

### 3.5 Pseudocode
```python
def route(intent: Intent, snap: Snapshot) -> Decision:
    if intent.book == "FORECASTX": cands = ["IB"]                       # only door
    elif intent.urgency == "MAKE": cands = ["KD"]                       # §4: makers only on KD
    else: cands = ["KD", "IB", "WB"]

    reasons = {}
    scored = []
    for r in cands:
        ok, why = hard_constraints(intent, r, snap)                    # §3.3, ordered
        if not ok: reasons[r] = why; continue
        scored.append((cost(intent, r, snap), r))
    if not scored:
        return Decision.refuse(reasons)                                 # journal every refusal
    scored.sort(key=lambda t: (round(t[0], 1), tie_break_rank(t[1], intent, snap)))
    best_cost, best = scored[0]

    # size against per-strike depth on the *Kalshi* book (WB/IB are the same book)
    qty = min(intent.qty, depth_at_or_better(snap, intent), route_cap(best), bp_qty(best, intent))
    if qty <= 0: return Decision.refuse({"size": "no depth or BP"})

    with registry.txn() as tx:                                          # §2, single transaction
        tok = tx.reserve(intent.book, intent.ticker, intent.dir, best, lease=lease_for(intent))
        if tok is None: return Decision.refuse({"lock": "direction held by other route(s)"})
        tx.check_aggregate(intent, qty)                                 # §6.2 raises → refuse
        tx.insert_pending(intent, best, qty, deadline=deadline_for(intent))
        audit(tx, "ROUTE", intent, best, qty, best_cost, reasons)       # §6.5
    return Decision.send(best, tok, qty, params_for(best, intent))     # STP/post_only/TIF per route

def params_for(route, intent):
    if route == "KD":
        return dict(side = "bid" if intent.dir == "YES" else "ask",   # [U] confirm NO mapping in demo
                    price = dollars(intent.limit_price_c, intent.dir),
                    time_in_force = {"MAKE":"good_till_canceled","TAKE_PATIENT":"good_till_canceled",
                                     "TAKE_NOW":"immediate_or_cancel","FLATTEN":"immediate_or_cancel"}[intent.urgency],
                    expiration_time = unix(intent.deadline) if intent.urgency in ("MAKE","TAKE_PATIENT") else None,
                    post_only = intent.urgency == "MAKE",
                    reduce_only = intent.action == "CLOSE",
                    self_trade_prevention_type = "maker" if intent.urgency == "FLATTEN" else "taker_at_cross",
                    cancel_order_on_pause = True,
                    order_group_id = group_for(intent.strategy_id, "KD"),
                    client_order_id = str(uuid4()))
    if route == "WB":
        return dict(side="BUY" if intent.action=="OPEN" else "SELL", event_outcome=intent.dir.lower(),
                    time_in_force="DAY", order_type="LIMIT", limit_price=..., quantity=...,
                    client_order_id=wb_coid())                          # IOC emulated: place+cancel on ack
    if route == "IB":
        c = ib_contract(intent)                                         # exchange pinned, never SMART (§6.4)
        return dict(contract=c, action="BUY", orderType="LMT", tif={"TAKE_NOW":"IOC","FLATTEN":"IOC"}.get(intent.urgency,"DAY"),
                    totalQuantity=int(qty), lmtPrice=..., transmit=True)
```
Split fills: the router never splits one intent across routes in the same tick. If `qty` is
below the intent, the remainder is re-issued as a new intent next tick, which may pick a
different route — always same `dir`, so I1 holds.

---

## 4. Maker strategy: Kalshi direct only; takers on Webull/IBKR

Why makers only on KD: fee 0.0175·p(1−p) ≤ 0.44¢ vs 2¢ flat elsewhere (4–25×) [V][D];
`post_only` and `expiration_time` exist only there; and confining all resting liquidity to
one member id means Kalshi's own STP is a genuine second line of defence for the bulk of our
resting risk. Takers may still go to KD when BP is there (it is cheaper), but the *capital
plan* (§7) parks taker capital on WB/IB so the maker book on KD is never starved and an FCM
suspension (§8) cannot take the maker engine down.

### 4.1 Quote rules
- One-sided quotes only, on the side the fair-value service prefers (I1 forbids resting
  both sides while a taker might need the other; two-sided market making is a later, KD-only,
  registry-aware mode).
- Price: `bid = min(fair_low − margin_maker, best_bid + 1¢ if that stays ≤ fair_low − margin_maker)`;
  `margin_maker` starts at 1.5¢ (taker margin is 3¢) because the fee is ~0.4¢ and fills are
  adverse-selected (§7 of quant.md: makers with the live BRTI/index feed are the informed side;
  we must not be the uninformed maker in the last 5 min).
- Windows: crypto hourlies 24/7 (KD only route with weekend hours); index hourlies only
  09:30–15:55 ET; never within 90 s of any close; never when data age > 2 s.
- `expiration_time` = now + 20 min max; `post_only=true`; `cancel_order_on_pause=true`.
- Size: quarter of the shrunk-Kelly size (quant.md §6), capped by `order_group` 15-s limit.

### 4.2 Cancel/replace budget
- Basic tier write bucket = 100 tokens/s = 10 writes/s with **1 s** of burst [V]; upgrade to
  Advanced (300/300, self-serve) before going live. Router reserves 30 % for cancels.
- Per market: ≤ 1 replace per 5 s unless fair value moved ≥ 1¢; use `decrease-order` when
  only size falls (keeps priority) [V]; any price change forfeits priority [V], so re-quote
  only when the edge after re-queue is still ≥ margin.
- Global: ≤ 120 cancel/replace per minute across all markets; the shadow report (§9) tracks
  fills-per-replace; a market with < 1 fill per 50 replaces over a day is dropped from quoting.

### 4.3 Taker path on WB/IB
- IOC semantics: KD native; IB `IOC` [V]; WB emulated (place LIMIT/DAY at touch → on first
  order event, cancel remainder; the 600/60 s place+cancel budget allows ≤ 5 emulated IOCs/s).
- WB and IB taker orders carry a 2¢ fee; the fair-value margin for those routes is +0.25¢ to
  +1.7¢ higher than on KD by price bucket (fee difference), computed by the router, not by
  the strategy.

---

## 5. Cross-exchange: Kalshi vs ForecastEx via IBKR

### 5.1 Overlap (what is listed on both)
ForecastEx product families found today [V]: IJC (initial claims), UNR (unemployment), RSM
(retail sales), BPMI (permits), PREMP (payrolls), CPIY (CPI), FF (Fed funds target), HS
(housing starts), RGDP (GDP), CP (corporate profits), ND (national debt), GT (global
temperature), plus index/commodity families US 500 (FES on E-mini S&P daily settlement),
US Tech 100 (FNQ), US Industrial 30, US Small Cap 2000, copper, nat gas, WTI, silver, gold,
palladium, platinum, "Bitcoin and Gold comparison". **No hourly or intraday index/crypto
contract was found on ForecastEx [U → §11]**; the Robinhood BTC 15-min / hourly pages are
Kalshi/Rothera flow, not evidence of ForecastEx hourlies. So the overlap with Kalshi is:
- Macro releases: KXFED ↔ FF, KXCPI ↔ CPIY, KXICLAIM ↔ IJC, KXU3 ↔ UNR, KXPAYROLLS ↔ PREMP,
  KXGDP ↔ RGDP (Kalshi tickers to be confirmed per event).
- Index levels: Kalshi daily/weekly S&P (cash index, Kalshi's own real-time print [D]) ↔ FES
  (CME E-mini **futures daily settlement** of the lead month, second month on final-settlement
  days [V-search]). Different underlyings → basis, not arbitrage; treat as a relative-value
  trade with a futures-basis model, not as §5.3.

### 5.2 Settlement differences that change the payoff table
- Threshold: ForecastEx asks "above K" (strict); Kalshi asks "at least K" (≥) [V-search]. For
  discrete releases (CPI 0.1 pp, claims 1k, unemployment 0.1 pp, Fed funds 25 bp) the boundary
  is a real outcome.
- ForecastEx pays an Incentive Coupon on collateral (~3.3–3.8 % APY, monthly) [D][U-rate];
  Kalshi pays nothing.
- ForecastEx is buy-only; a NO is bought at (1 − yes) and IB nets opposing pairs [V].
- Release-revision and source-timing rules per contract are **[U]**; read both T&Cs per event
  before enabling a pair.

Payoff of the only *safe* pair, with v = released value:
| | v < K | v = K | v > K |
|---|---|---|---|
| Kalshi YES(≥K) | 0 | 1 | 1 |
| ForecastEx NO(>K) | 1 | 1 | 0 |
| **Sum** | 1 | **2** | 1 |
Cost = a_kalshi + b_fx + fees(KD 0.07·a(1−a) or 2¢ via IB; FX 1¢). **Arb if a + b + fees < 1**,
with a free boundary lottery. The mirror pair (Kalshi NO(<K) + ForecastEx YES(>K)) pays **0** at
v = K and is forbidden by the router unless the boundary value is impossible (e.g. Fed funds
between meetings-grid values).

### 5.3 Why this is real arbitrage and Kalshi-vs-Kalshi is not
Different DCM/DCO, different books, different counterparties; our two legs can never match
each other; there is no beneficial-ownership problem (Rule 5.17(c) is about matching your own
orders) and no Rule 3.3(b) self-match risk between the legs. The IB account still has to be
disclosed under 3.3(b) because IB *also* routes to Kalshi. Kalshi-through-WB vs Kalshi-through-KD
at "different prices" is impossible: it is one book; any apparent difference is latency or
fees.

### 5.4 Sizing and execution
- Hold-to-resolution: capital is locked until the release (days–weeks). Required gross edge:
  `1 − (a + b + fees) ≥ hurdle_daily × days_to_settle + 0.5¢ leg-risk reserve`, where the
  hurdle is the taker engine's realised return per day per dollar (start 0.03 %/day); the FX
  coupon on the FX leg's collateral is added back.
- Size = min(depth of the thinner leg at or better than price, per-event cap §6.2, 10 % of the
  IB allocation per event). Legs are not atomic: buy the **ForecastEx leg first** (thinner,
  buy-only, no way to be picked off by our own Kalshi quotes), then the Kalshi leg with a
  `TAKE_NOW` intent on the cheapest route; if the Kalshi leg misses, the router re-prices the
  FX leg against fair value and either holds it as a directional position inside the fair-value
  band or unwinds by buying the opposing FX contract (IB nets).
- Registry: the FX leg is on book `FORECASTX`, the Kalshi leg on `KALSHI`; both rows carry the
  same `pair_id` so reconciliation reports the pair, and the Kalshi leg respects I1 like any
  other order.

---

## 6. Compliance controls in code

### 6.1 Rule 3.3(b) disclosure gate
Config `routes.<r>.enabled_live` cannot flip to true for a second Kalshi-book route until a
signed checklist row exists in `compliance_gate`:
```
{route, account_id, disclosure_email_sent_at, disclosure_to, ack_received_at|null,
 ack_ref, kd_rule33c_fcm_list_sent_at (KD names its FCMs), operator_signature, hash}
```
The router's constraint #2 reads this table; missing row → refusal reason `RULE_3_3_B_GATE`.
Checklist: (1) list every account: WB event account no., KD member id, IB account id;
(2) send by email per Rule 3.3(b) (address as Kalshi specifies — `rule33@` is not in v1.29
text [D]); (3) KD as Self-Clearing Member also names its FCMs (3.3(c)); (4) store the sent mail
and any acknowledgement; (5) red-team test §9.3 passed on this build; (6) re-run when adding
IB or any new FCM.

### 6.2 Aggregate position limits across routes
`agg_exposure(book, ticker)` = Σ_routes (yes_qty − no_qty) + Σ live orders (signed). Caps
(config, ceilings enforced by pydantic): per ticker contracts and notional; per event
worst-case terminal PnL over the strike grid (quant.md §6); per series (daily/weekly/hourly
share one underlying); per "sign of vol view" across the day; and the exchange limits (INX
$7M/member, BTC $1M/strike) as absolute ceilings. Rule 5.19(a): bids that *would* breach are
violations, so the check is applied to open orders, not just fills.

### 6.3 Rule 5.11 price-band and notional guards (financial: $3,000 per caused cancel)
Evaluated in the head **and** again in each arm (arm has its own copy of the Kalshi mid):
- `|limit − kalshi_mid| ≤ 10¢` (half the 20¢ no-cancel range) and `|limit − fair_value| ≤ 15¢`.
- Limit must not cross more than 2 levels beyond the touch for TAKE; never cross for MAKE.
- Notional per order ≤ min($50k WB guard, route cap, strategy cap); contracts per order ≤ 5,000
  until equity band allows more (§10).
- Repeat-execution limit: ≤ N identical orders per ticker per minute (start 6), FIA-style.
- Any guard failure = hard reject + audit + Telegram; three in an hour = route halt.

### 6.4 IBKR exchange pinning
IB's prediction-market screen routes "identical" contracts to the best net price across
Kalshi/CME/ForecastEx [V]. An IB order left on `SMART` could land on the Kalshi book against
our KD quote. Rule: `contract.exchange` is always explicit (`FORECASTX`, or the Kalshi
exchange code once §11 identifies it); `SMART` is rejected by the arm's guard.

### 6.5 Audit log format
Append-only JSONL, one file per UTC day per component, each line SHA-256 chained to the
previous (`prev_hash`), shipped to object storage hourly:
```
{"ts":"2026-09-19T14:03:22.123456Z","seq":184233,"head_epoch":17,"component":"router",
 "event":"ROUTE|SEND|ACK|FILL|CANCEL|REJECT|REFUSE|LOCK|UNLOCK|GUARD|GATE|KILL|RECONCILE",
 "intent_id":"…","client_order_id":"…","venue_order_id":"…","route":"KD","book":"KALSHI",
 "ticker":"KXBTC-26SEP1915-B81350","dir":"YES","action":"OPEN","price_c":47,"qty":"25.00",
 "fair_value_c":51.2,"kalshi_mid_c":47.5,"cost_model":{"KD":1.74,"IB":2.0,"WB":2.5},
 "constraints_failed":{"WB":"HOURS"},"lock_state":"HELD","agg_after":{"ticker":125,"event":300},
 "fee_paid":"0.0044","is_taker":false,"operator":null,"note":null,"prev_hash":"…","hash":"…"}
```
Retention ≥ 5 years; a Rule 3.6(a) request (15 days) is answered by a query, not a hunt.

---

## 7. Capital placement and rebalancing

Latencies (business days): KD out 3–5 (debit card 1–3); WB in 3–5 to settle, out 2–5, $50k/day
out; IB in 4 hold, out 5 to originating bank, 44 elsewhere. Broker→broker transfers do not
exist for these accounts; the bank is the hub. Treat any cross-route move as **~1 week**.

Initial split (of event capital E): KD 50 % (maker collateral + 24/7 crypto + cheapest
taker), IB 30 % (ForecastEx legs + Kalshi taker overflow), WB 20 % (taker overflow, FCM
redundancy). Bank buffer outside all three: 15 % of E for weekly top-ups.

Rebalancing rule (weekly, Monday, from the shadow report): target share of route r ∝
(realised net edge captured through r over 4 weeks + capacity r could not serve because of BP
refusals) with a floor of 10 % per live route; move money only when a route's utilisation
(peak committed BP / allocation) was > 85 % or < 30 % for two consecutive weeks; never move
more than 25 % of E per week; settlement proceeds stay where they land (WB/KD credit
immediately [D]). Emergency: an FCM-suspension (§8) triggers no transfers until positions
resolve; the other routes simply carry the flow within their allocations.

---

## 8. Failure handling

- **Route down** (heartbeat > 15 s, auth failure, venue 5xx storm): arm self-cancels (§2.5);
  head sets its rows `CANCEL_SENT`, marks route `DEGRADED`; locks in `HELD` by that route
  alone move to `DRAINING` and stay there until venue reads confirm zero open orders (an arm
  that cannot be reached keeps the lock **held** — other routes may not flip direction in those
  tickers, they may only join same-direction). Positions on the down route are hedged only by
  same-direction rules: to reduce risk the head may buy the *opposing* contract on another
  route (different counterparties, no self-match, allowed) but flags the pair for netting on
  recovery and counts both legs in §6.2.
- **FCM suspension (Rule 5.12)**: Kalshi cancels all that FCM's customer orders and later trades
  are invalid. Detect via mass unsolicited cancels or venue notice → route `FROZEN`; all its
  rows `DONE(EXCHANGE_CANCEL)`; positions there frozen in the ledger at last mark; no new
  intents; §7 transfers paused; operator alert. WB is the realistic candidate (state sports
  enforcement) [D].
- **Partial fills across routes**: an intent may be partially filled on route A, remainder
  re-issued next tick on route B (same dir). The intent ledger sums fills across routes; the
  aggregate check uses the summed position; reconciliation ties every fill to `(route,
  client_order_id)`.
- **Reconciliation**: continuous from venue streams (KD user-fills WS with `fee_cost`,
  `is_taker`; WB gRPC order events + position settlement events; IB `execDetails` +
  `commissionReport`); a 60-s REST sweep per route (respecting WB 2/2 s) compares open orders
  and positions to the registry; any mismatch → affected ticker `FROZEN`, alert, manual
  resolve; nightly statement-level reconciliation of fees (KD sub-cent accumulator vs our
  model; WB $0.02; IB commission reports); PnL only from venue records, never from the journal.
- **Unknown order state** (timeout): §2.3 I4; never re-send an intent whose prior send is
  UNKNOWN (duplicate risk) — `client_order_id` idempotency on KD/WB; IB `orderId` uniqueness.
- **Clock/data staleness**: any route's data age > 3 s or NTP offset > 50 ms → that route
  refuses TAKE and cancels MAKE.

---

## 9. Testing

### 9.1 Three sandboxes concurrently
Run the full head with arms in: KD demo (`external-api.demo.kalshi.co`, demo keys, fake
funds; prices "may not reflect real markets" [V] so use it for lifecycle/STP/expiration tests,
not for fill statistics), WB sandbox (= paper, separate sandbox App Key; 30/60 s limits; event
data latency [U]), IB paper (top-of-book fills only [V]; event contracts [U]). Same code
paths; only endpoints/keys differ.

### 9.2 Shadow routing report (daily, per route, per price bucket, per urgency)
Columns: intents seen; would-route share; actually routed share; est. fee vs paid fee;
ack latency p50/p95; slippage vs Kalshi public book at decision time; miss rate; refusals by
reason (HOURS, BP, LOCK, RATE, GATE, GUARD); cancels per fill (maker); realised markout at
+30 s/+5 min/settlement by route; BP utilisation peak. Acceptance before enabling a second
live route: 10 trading days, fee model error < 0.05¢, zero LOCK violations, zero GUARD
bypasses.

### 9.3 Red-team tests (must fail loudly)
(1) Two arms attempt opposite directions in one ticker within 1 ms → second refused. (2) Kill
the head mid-place → arm cancels within 15 s; lock stays HELD until ack. (3) Force an UNKNOWN
→ no duplicate send. (4) Stale epoch token → arm rejects. (5) KD demo: place bid then ask that
crosses it with `taker_at_cross` → incoming cancelled; with `maker` → resting cancelled. (6)
IB contract with `SMART` → arm guard rejects. (7) Price 12¢ off mid → GUARD reject. (8)
Aggregate cap breach via three small orders on three routes → third refused.

---

## 10. Equity-band behaviour

| Equity | Routes live | Behaviour |
|---|---|---|
| $1–500 | **one** route only (no 3.3(b) burden): KD if approved (sub-cent fees, 24/7), else WB | single-contract intents; TAKE only at ≥ 5¢ shrunk edge; no maker (a lone 1-lot maker's fills are adverse-selected and the cancel budget is wasted); this band is measurement, not income [D] |
| $500–5k | KD live; WB/IB in shadow | maker on KD in 1–5 lots on crypto hourlies (24/7); index TAKE; still one live route until the gate passes |
| $5k–50k | KD + WB (+ IB after gate) | full router; capital split §7; per-order ≤ 200 contracts |
| $50k–250k | three routes + ForecastEx pairs | capacity governed by measured depth (below); maker size raised only while fills-per-replace holds |
| $250k–1M | same | expect BP refusals to be the binding constraint on index hourlies; growth comes from ForecastEx macro pairs and more strikes/hours, not bigger clips |

Where capacity ends (measured, quant.md §5 and addenda A9): BTC hourly touch depth ≤ 300
contracts in the first minutes and ~1,500 at the ATM strike near expiry; only 10–40 of ~188
crypto markets ever trade; KXINXU events run 113k–358k contracts but 50 % of the 4 pm event
trades in the last 30 min and edge-eligible depth at our price is a few hundred to low
thousands of contracts per strike-hour. Working estimate: deployable per hour ≈ Σ over
eligible strikes of (depth within edge band × price) ≈ $500–3,000; ≈ 7 index hours + 24 crypto
hours/day → roughly **$20–60k/day of turnover at 3–5¢ edge**, i.e. the taker engine saturates
around **$50–150k deployed**; the KD maker book adds capacity only up to the point where our
resting size is a visible share of the touch (cap at 20 % of displayed depth). The router
records `depth_refusals` and `bp_refusals` per day; when depth refusals dominate for two weeks
at a band, that is the capacity ceiling for that band and capital moves to ForecastEx pairs
or stays in the bank.

---

## 11. UNVERIFIED items, each as a test

| # | Item | Test |
|---|---|---|
| U1 | KD v2 `side=ask` without a YES position creates a NO position (vs reject) | demo: ask 10 @ 0.40 in a market with no position; expect NO position 10 at cost 0.60 |
| U2 | KD per-order contract cap and whether `post_only` rejects or re-prices | demo: post_only bid at/above ask → expect reject; place 100k-contract order → record error |
| U3 | KD v2 batch maximum at Basic/Advanced tiers | demo: batch of 10, 30, 60 orders; record first rejection |
| U4 | KD `expiration_time` honoured to the second; behaviour at market close | demo: GTC with +60 s; observe cancel timestamp |
| U5 | Kalshi retail API key carries CF Benchmarks WS entitlement | authenticated WS subscribe `cfbenchmarks_value` |
| U6 | WB TIF beyond DAY (GTC/IOC/GTD/FOK per JSON schema) | sandbox place with each TIF; record accept/reject |
| U7 | WB weekend/overnight crypto event hours (FAQ "24/7" vs docs 08–18) | sandbox + read-only live check Saturday 12:00 ET |
| U8 | WB per-order cap 50,000 vs 500,000/$50k | sandbox preview at 50,001 and $50,001 |
| U9 | WB sandbox event-data latency vs live Kalshi book | timestamp diff over 1 hour |
| U10 | IB Kalshi contract definition (secType/exchange code/right mapping) and whether KXINXU/KXBTC hourlies are listed | `reqContractDetails` with symbol from ForecastTrader; enumerate expiries |
| U11 | IB paper supports Kalshi/ForecastEx event contracts | paper: define FORECASTX OPT, place 1-lot limit far from touch, cancel |
| U12 | IB Web API vs TWS for event contracts (10 req/s vs 50 req/s) | place/cancel 1-lot via each; record pacing errors |
| U13 | ForecastEx has any hourly/intraday index or crypto contract | ForecastTrader listing scan; expect none |
| U14 | FES vs Kalshi S&P daily settlement basis (futures settlement vs cash print) | log both settlements for 20 days; fit basis distribution |
| U15 | Macro pair boundary semantics (≥ vs >) and revision handling per contract | read Kalshi and FX T&Cs for each of 6 families; encode per pair |
| U16 | ForecastEx Incentive Coupon rate and accrual on NO legs | IB statements after one month holding a pair |
| U17 | IB `SMART` for event contracts can reach the Kalshi book | paper: submit SMART order, inspect `openOrder` exchange field |
| U18 | Kalshi disclosure address/method for Rule 3.3(b) | ask Kalshi support in writing; store reply in `compliance_gate` |
| U19 | Rule 5.12 detection signal (what an FCM suspension looks like on our feeds) | tabletop with WB support; define detector from mass-cancel reason codes |
| U20 | Funding latencies as actually experienced | time one $100 round trip bank→route→bank per route |
