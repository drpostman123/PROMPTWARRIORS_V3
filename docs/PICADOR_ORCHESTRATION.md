# Picador Octopus — Multi-Broker Orchestration Design (v0.9 draft, 2026-09-19)

Scope: one operator, several brokerage accounts (Webull, Robinhood, tastytrade, Public,
Kalshi direct; IBKR and Alpaca as candidates), each run by an independent "arm" bot, all
coordinated by one "head" (orchestrator) on a rented VPS, with a warm standby copy on the
operator's Linux desktop. The venue fact table in §2 is finalized from the broker research
agent (see `docs/research/brokers.md`); everything else here follows from rules already
verified.

## 1. The constraint that defines the design

**All the event-contract brokers route into the same Kalshi order book.** Webull,
tastytrade (via Apex), Public (if/when it offers events), IBKR and Alpaca are Kalshi
distribution; Robinhood additionally routes to ForecastEx and Rothera. Kalshi's Rulebook
v1.29 (text extracted locally, `docs/research/` has the source):

- **Rule 3.3(b):** "FCM Customers are allowed to have accounts with multiple FCMs. However,
  an FCM Customer must not intentionally match its own orders against each other. … Before
  an FCM Customer trades on Kalshi with multiple accounts, it must disclose the identity of
  each account to the Exchange by email."
- **Rule 5.17(c):** no trade that "does not result in a change in beneficial ownership" or
  "is designed to unnaturally inflate trading volume"; **5.17(bb):** no wash trades, money
  passes or front-running; **5.17(aa):** no spoofing or reckless disregard for orderly
  execution during the closing period.
- **Rule 5.19(f):** position limits apply to all accounts one person controls, and to
  positions of persons acting under an agreement, "as if held by a single Person."
- **Rule 5.11:** an error-trade cancel caused by the trader costs $3,000; no
  automated-malfunction exception for FCM customers.
- Broker terms (Webull ToS §2, and typically all of them): the account may be operated
  only by its owner; Public's API program and Webull's OpenAPI approval are individual
  grants.

Therefore the octopus is **not** five bots competing on the same book. It is one trading
brain with five execution arms, and its first job is to guarantee that no two arms are
ever on opposite sides of the same market, that aggregate exposure per market respects
one position limit, and that every account is disclosed to Kalshi before the second one
trades.

What the multi-account structure legitimately buys:
1. **Fee routing.** The same Kalshi contract costs different amounts by broker and price
   (Webull flat $0.02/side; Robinhood k·P·(1−P)·C with k = 10 % or 5 % Gold since
   2026-06-01; Kalshi direct quadratic taker with ~¼ maker; tastytrade/Apex, IBKR, Alpaca
   per §2). A router picks the cheapest arm per price bucket and side.
2. **Asset-class coverage for hedges and pricing data.** Index options (SPX/XSP) via Public
   or tastytrade; equity options via all three; crypto via Robinhood's official Crypto API
   or Webull; futures via tastytrade/Webull. The options-implied fair value engine can be
   fed by whichever broker gives the best index-option data.
3. **Different exchanges.** Robinhood is the only retail door to ForecastEx and Rothera;
   genuine cross-exchange price differences between Kalshi and ForecastEx on the same
   event are real arbitrage (different books, different owners on the other side), unlike
   Kalshi-vs-Kalshi-via-another-broker which is nothing.
4. **Capacity and redundancy.** Per-broker rate limits and per-order caps add up;
   a broker outage or a product pull (Webull is under state enforcement for sports)
   does not stop the system.
5. **Testing breadth.** Each broker's sandbox/paper environment behaves differently;
   running the same strategy through several paper arms shows execution assumptions
   that one venue would hide.

## 2. Venue fact table

Finalized from the broker research agent; see `docs/research/brokers.md` for sources and
the unverified items.

| Venue | Official retail API | Asset classes via API | Event contracts via API? | Event exchange(s) | Event fee per contract | Sandbox/paper | Role in the octopus |
|---|---|---|---|---|---|---|---|
| Webull | Yes (OpenAPI) | stocks, options (equity), futures, crypto, events | **Yes** | Kalshi | $0.02/side flat | sandbox = paper | Primary event arm (already planned) |
| Kalshi direct | Yes | events | **Yes** | Kalshi | quadratic taker, ~¼ maker | demo env | Cheapest maker route; resolution data feed; 24/7 crypto |
| Robinhood | Crypto API only | crypto | **No** (app only) | Kalshi, ForecastEx, Rothera | k·P·(1−P), k=10 %/5 % Gold | none | Crypto hedge arm via official API; events manual only (no unofficial libraries) |
| tastytrade | Yes (Open API) | equities, options, futures, futures options, crypto | pending | Kalshi via Apex | pending | cert sandbox | Options/futures hedge arm; event arm only if API-exposed |
| Public | Yes (Individual Trader API) | stocks, options, **index options**, crypto, bonds | pending (no events product found) | — | — | pending | Index-options data and hedge arm |
| IBKR (candidate) | Yes (TWS / Client Portal) | everything incl. Kalshi + ForecastEx contracts | Yes | Kalshi, ForecastEx | pending | paper | Possible cheapest all-in-one route |
| Alpaca (candidate) | Yes | stocks, options, crypto; Kalshi events announced 2026-08-31 | pending | Kalshi | pending | paper | Watch |

## 3. Architecture

```
                         ┌──────────────────────────── HEAD (orchestrator) ─────────────────────────────┐
                         │ market registry · ownership lock (one arm per market-side) · aggregate risk  │
 pricing feeds ──────────▶ fair-value service · smart order router (fee/limit/hours aware) · kill switch │
 (Kalshi WS BRTI, index, │ leader lease · journal of record · reconciliation · metrics · alerts         │
  SPX/XSP options)       └──────┬──────────┬──────────┬──────────┬──────────┬──────────────────────────┘
                                │ NATS JetStream (intents, fills, positions, heartbeats)
        ┌───────────────────────┼──────────┼──────────┼──────────┼──────────┐
   ┌────▼─────┐ ┌─────▼────┐ ┌───▼──────┐ ┌─▼────────┐ ┌─▼──────┐ ┌─▼──────────┐
   │ arm:     │ │ arm:     │ │ arm:     │ │ arm:     │ │ arm:   │ │ arm:       │
   │ webull   │ │ kalshi   │ │ tasty    │ │ public   │ │ rh-    │ │ (ibkr /    │
   │ events   │ │ events   │ │ options/ │ │ index    │ │ crypto │ │  alpaca)   │
   │          │ │ +data    │ │ futures  │ │ options  │ │        │ │            │
   └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘ └────────────┘
   each arm: own credentials, own rate limiter, own venue adapter, own paper mode,
   own systemd/container unit; speaks only the common Intent/Fill/Position schema
```

### 3.1 Head responsibilities
- **Strategy + pricing** live in the head (one fair value per market, one opinion). Arms never price.
- **Ownership lock:** before any intent is routed, the head acquires `(market, side)` for one arm in the registry (SQLite/Postgres row with lease). Another arm cannot open the opposite side of that market while any own-position or resting order exists anywhere. This is the self-match guard demanded by Rule 3.3(b) and 5.17(c).
- **Aggregate risk:** per-market and per-event exposure summed across all arms against one limit (Rule 5.19(f)); daily loss lock summed across arms; kill switch fans out to every arm and cancels everything.
- **Smart order router:** given an intent (market, side, price bucket, size, urgency), choose the arm minimizing `fee(price) + expected slippage + hours/limit penalties`, subject to: arm healthy, arm has buying power, arm's per-order caps, arm's hours for that category, arm's rate budget remaining. Fee tables are config with an effective date.
- **Journal of record:** the head's journal is the truth for aggregate PnL, reconciled nightly per arm from each broker's own history endpoints.
- **Leader lease:** a single row in the shared store with TTL; only the lease holder may route live intents. The standby renews nothing until promoted.

### 3.2 Arm responsibilities
- Venue adapter (official SDK only), per-endpoint rate limiter, order lifecycle state machine, fills/positions streaming, paper mode against the same interface, local hard guards (price band, notional cap, max contracts, category hours), heartbeat every 5 s, and a **self-cancel-on-disconnect** rule: if the arm loses the head for N seconds it cancels its own resting orders.
- An arm may refuse an intent (insufficient BP, hours, cap) and must say why; the router retries elsewhere or drops.
- Arms are stateless beyond their local order cache; everything durable flows to the head.

### 3.3 Messaging and schema
- NATS JetStream (or Redis Streams) with durable subjects: `intent.<arm>`, `fill.<arm>`, `position.<arm>`, `hb.<arm>`, `risk.kill`. Every message carries `owner_id`, `arm_id`, `intent_id`, `client_order_id`, monotonic sequence, and wall-clock + NTP offset.
- Common schema (pydantic): `Instrument{venue_symbol, kalshi_ticker|occ_symbol, asset_class, event_id, strike, expiry_utc}`, `Intent{...}`, `Fill{...}`, `Position{...}`. Kalshi tickers are the join key across event arms.

### 3.4 Deployment: rented VPS primary, desktop warm standby
- **Primary:** one VPS in AWS us-east-1 (Webull's execution origin; Kalshi is us-east-2, ~14 ms). Docker Compose or plain systemd units: `head`, `nats`, `postgres` (or SQLite + Litestream), one unit per arm. Secrets via systemd `LoadCredentialEncrypted` or Docker secrets; never in images or env files on disk unencrypted.
- **State replication:** Postgres streaming replica on the desktop over WireGuard, or Litestream continuous SQLite backup to object storage that the desktop restores from. Config and code in git; a tagged release is what both machines run.
- **Failover rule:** the desktop runs the full stack in `standby` mode: arms connected read-only, head not holding the lease. Promotion is manual (one command) or automatic only after the primary has missed heartbeats for a long threshold **and** the desktop has verified through each broker API that no resting orders remain from the primary (query open orders per arm; cancel-all if any). Two live heads are the one thing this design must make impossible: the lease is stored at the broker-independent shared store, and each arm also refuses live intents whose `head_epoch` is older than the newest it has seen.
- **Broker-side 2FA and device rules:** Webull's 2FA token, Public's personal access token, tastytrade OAuth refresh tokens and Kalshi's RSA key each live in exactly one place (the primary); failover copies them via the encrypted credential store, never by hand. Webull's token dies after 15 idle days, so the standby must also make an authenticated read call daily.
- **Drills:** monthly failover drill in paper mode; quarterly in live with all positions flat.

### 3.5 Testing maximization
- Every arm ships with a paper mode using the arm's own sandbox where one exists (Webull sandbox, Kalshi demo, tastytrade cert, Public/IBKR/Alpaca paper) and a local replay executor where none does (Robinhood).
- The head can run **shadow arms**: the same intent is priced for every arm, only one executes, the others log what they would have paid. That produces the fee-routing evidence and reveals venue-specific fill assumptions without extra risk.
- Category report per arm per day: fills, slippage vs the Kalshi book, fee paid vs router estimate, rejects by reason, latency percentiles.

## 4. Compliance checklist (before the second event arm goes live)
1. Email Kalshi (rule33@kalshi.com, or as Kalshi specifies) listing every account: Webull Event account number, Kalshi member id, tastytrade/Apex account, Robinhood Derivatives account, IBKR/Alpaca if used. Keep the acknowledgement.
2. Ownership lock and aggregate position limits proven by tests; a red-team test that tries to open opposite sides through two arms must fail.
3. Confirm each broker's terms permit API/automated trading by the account owner (Webull FAQ: algorithmic trading is intended use; Public: Individual Trader API terms; tastytrade: Open API terms; Robinhood: Crypto API terms only — no unofficial equities/events access).
4. State check for each product category (sports excluded in several states; index/crypto currently unrestricted).
5. Tax: one consolidated export across arms; CPA opinion on characterization before scaling (see ops report).
6. Error-trade exposure: per-arm price band ±10 ¢ from the Kalshi mid and notional caps, because a cancelled error trade costs $3,000 with no malfunction exception.

## 5. Build order for the octopus (after the single-arm Picador is live in paper)
1. Extract the common schema + NATS bus + head registry from the Webull-only build (the Webull adapter becomes arm #1).
2. Kalshi arm #2: data first (BRTI/orderbook WS), then trading with maker fees; disclosure email before its first live trade.
3. Hedge/data arms: Public (index options) or tastytrade (options/futures), whichever the fact table shows has usable SPX/XSP data; Robinhood Crypto API as the crypto hedge arm.
4. Router + shadow arms; fee-routing report after 10 trading days.
5. Standby deployment on the desktop; failover drill.
6. Candidate arms (IBKR, Alpaca) only if the fact table shows lower all-in cost than Webull/Kalshi for the same contracts.
