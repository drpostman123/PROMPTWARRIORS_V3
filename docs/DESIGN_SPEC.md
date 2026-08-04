<!-- Produced by a 5-persona agent debate (signal researcher, risk manager,
     execution engineer, quant statistician, systems architect): propose ->
     adversarial cross-critique -> chief-quant synthesis. 11 agents total.
     This is the design authority; code defaults track it, with deltas noted
     in the Implementation Status section appended at the end. -->

# GodMode0DTE — Final Design Specification

**Version 1.0-FINAL. Chief Quant arbitration. Every number below is the shipping default.** Instrument: SPY 0DTE debit verticals (SPX escalation rule §6.6). All times ET. Two-process topology (§9) is mandatory for live. Score threshold 93 (config-raisable, compiled floor 90).

Arbitration summary of the five contested axes: entry window **09:50–11:30 only** (SR's 09:36 lost: regime layer emits UNKNOWN until 4 closed 5m bars, so a 09:36 intent is structurally unscoreable); force-flat **15:30** (RM's 15:15 lost: surrenders managed time for no reliability gain given 11:30 cutoff + 90-min time stop; EE's 15:50 lost on terminal gamma); weights = **Quant's set** with graduated RVOL (SR's raw macro/MTF-heavy set lost on the |ρ|≈0.5–0.9 double-counting arithmetic); heat = **max(entry_debit, mark)** (RM's entry-debit-only lost: undercounts winners' at-risk mark; his "winners must not free budget" concern is preserved by the max); sizing = **phased two-tier** (SR's launch 3-tier lost: ~590 obs/tier needed to falsify, top tier collects <20/yr).

---

## 1. Setup Score (0–100) + Hard Gates

### 1.1 Score algebra (canonical, governor-audited)

```
Score = clamp( Σ components + Σ adjustments, 0, 100 ) × Π multipliers
```

`ScoreBreakdown` (frozen dataclass): `components: dict`, `adjustments: dict` (signed), `multipliers: dict` (each ∈ {0.0, 0.5, 1.0}). Governor recomputes and requires equality within **0.01** else `REJECT("SCORE_MISMATCH")`.

### 1.2 Components (sum of maxima = 100)

| # | Component | Max | Exact formula / condition (LONG shown; mirror for SHORT) |
|---|---|---|---|
| 1 | **ORB** | 25 | Sub-parts, then whole component × decay `max(0, 1 − minutes_since_breakout/30)`:<br>• Breakout close: first 1-min close > `ORH + 0.0003×ORM` → **10**<br>• CLV of breakout bar `((C−L)−(H−C))/(H−L)`: ≥0.80 → **8**; 0.60–0.80 → **4**; <0.60 → 0<br>• Timing: breakout bar ≤10:35 → **7**; 10:35–11:30 → **3** |
| 2 | **RVOL** | 20 | `RVOL = cum_vol(09:30→t) / median_20d(cum_vol same window)`:<br>≥2.5 → **20**; 2.0–2.5 → **15**; 1.5–2.0 → **10**; 1.0–1.5 → **4**; <1.0 → **0 AND hard gate** (G-S6). OPEX days: floor raised to 1.75. *(Quant's cliff "≥1.5→20" lost: 20 free points on median-plus tape at a 93 threshold.)* |
| 3 | **VWAP** | 15 | Close > session VWAP → **6**; VWAP slope over last 6×1m bars > `+0.05×ATR_5m` → **5**; pullback within `0.25×ATR_5m` of VWAP in last 12 bars that held → **4** |
| 4 | **REGIME** | 20 | `RegimeOutput.state` matches direction AND `confidence ≥ 0.85` → **20**; confidence 0.70–0.85 → **12**; <0.70 → **0 AND hard gate** (G-S8) |
| 5 | **MACRO** | 8 | Residual-only (§5). Strongest confirmer (`0.5≤|z|<1.5` → 3; `|z|≥1.5` → 4) + second confirmer +2 + third +1, cap 8 |
| 6 | **VIXPREF** | 6 | VIX 14–22 AND `VIX9D/VIX < 1.0` (contango) → **6**; VIX 22–28 contango/flat → **4**; VIX 12–14 → **2**; else (VIX<12, VIX>28, or backwardation) → **0** |
| 7 | **MTF** | 6 | 15m `close > EMA9 > EMA21` (direction-aligned) → **4**; daily `close > EMA20` aligned → **2**. EMA lengths **9/21 everywhere** (8/21 was a typo, unified). |

**Adjustments:** `macro_contradiction: −6` when any macro residual `z ≤ −1.0` against direction (§5); `dow_learned: ∈ [−2, +2]` — default 0, activatable per weekday only after ≥40 same-weekday outcomes, binomial p<0.05, quarterly review.

**Multipliers:** `event_gate ∈ {0,1}` (blackout table §5.3); `stale_gate ∈ {0,1}` (score older than one 1-min bar OR >90 s unexecuted → 0); `bias_conflict ∈ {0.5,1}` (trade against a forced sentiment directional bias → 0.5).

**Zero-weight, logged features (may earn weight only via §2.4 per-component logistic test: slope>0, p<0.05, ≥200 obs):** RSI(14) all TFs, ADX(14) all TFs, day-of-week, gold, oil, ES–NQ spread, non-USD FX, OR-width ideal-band bonus. *(SR's 6 microstructure points deleted — cost control is not alpha; SR's F=8 calendar-cleanliness points deleted — rewards the modal state, inflates scores exactly at threshold.)*

Arithmetic consequence (intentional): at 93 you may drop ≤7 pts, so every trade requires REGIME=20, RVOL≥15, CLV≥0.60, a fresh (<~10 min old) breakout, and MACRO can never manufacture a trade (score ex-macro ≥ 85).

### 1.3 Hard gates — signal-side (zero the score; logged `ScoreRejection` with reason code)

| ID | Gate | Fail condition |
|---|---|---|
| G-S1 | Scoring window | outside 09:50:00–11:30:00 ET |
| G-S2 | OR width (dual test) | NOT (`0.10 ≤ ORW/ATR_d ≤ 0.35` AND `0.07% ≤ ORW/ORM ≤ 0.40%`). Priors; re-fit after 60 shadow days. *(Quant's original 0.25–1.20×ATR band rejected as a units error; RM's 0.25 ceiling was close but loses valid elevated-vol opens.)* |
| G-S3 | Breakout validity | breakout bar CLV < 0.50, or close not beyond OR edge + 0.03% buffer |
| G-S4 | Chase | `(C_breakout − ORH)/ORW > 0.50` |
| G-S5 | VWAP side | price on wrong side of session VWAP for direction |
| G-S6 | RVOL floor | RVOL < 1.0 (1.75 on OPEX days) |
| G-S7 | Trend proxy | `f_ema = (EMA9_5m − EMA21_5m)/ATR_5m` not > +0.30 (LONG) / < −0.30 (SHORT). Replaces ADX gate. |
| G-S8 | Regime | state ≠ direction-matching TREND, or confidence < 0.70, or `vol_regime == EXTREME` |
| G-S9 | One-shot | this OR level already produced a losing trade today, same side |
| G-S10 | Data freshness | any input feed older than 10 s at evaluation |

### 1.4 Hard gates — governor-side (evaluable from governor's OWN data only; §8 gives exact order)

Lockout/state, schema/sanity, duplicate/burst/rate, staleness/clock, time window (independent copy), event blackout (governor's own calendar), daily loss, position count, VIX ≥ 32 hard reject (governor's own VIX subscription — outermost wall over Quant's band edges), microstructure/liquidity (§6.3), debit sanity `0.05W < net_mid ≤ 0.55W` (outer wall; execution's operating band is tighter), sizing, worst-case-day, heat, BP, pre-flight re-quote. **No gate appears in both layers with the governor looser.** Signal-side liquidity pre-check runs at 0.8× governor thresholds (advisory, avoids dead intents).

---

## 2. Sizing Ladder + Enforcement Order

### 2.1 Phased ladder (risk = max loss = ladder-cap price × contracts × 100)

| Phase | Unlock condition | 93.00–96.99 | ≥ 97.00 |
|---|---|---|---|
| **A** (launch) | — | **2.0%** | **2.0%** |
| **B** | ≥200 shadow+paper outcomes AND logistic `logit(P(win))=a+b·Score` slope b>0 at p<0.05 AND Wilson 90% LB(hit-rate at ≥93) ≥ p_be+3pts | **2.0%** | **3.0%** |
| **C** | Phase B held one quarter AND ≥50 outcomes at ≥93 still clearing the Wilson bound | **2.0%** | **4.0%** (hard ceiling) |

Phase transitions are `governor.yaml` changes requiring the human unlock CLI, logged with acknowledged calibration stats. Breakeven for validation: `p_be = (SL_frac + friction_frac) / (TP_frac + SL_frac)` computed from the **actual** exit params and **logged** slippage distribution (EE's managed-exit formula adopted; Quant's hold-to-expiry 0.55 bar lost — nobody holds to expiry). At launch params ≈ 0.61.

### 2.2 Sizing formula (governor-only; engine's `est_debit` never used)

```
E          = live net-liq, governor-fetched ≤5 s old, garbage-banded (§8 Gate 6)
cap_price  = ladder price cap = min(combo_mid + 0.50×edge, 0.45×W)     # §6.4
risk_pct   = phase_ladder(score)
budget     = E × risk_pct
qty        = floor( budget / (cap_price × 100) )                        # sized at WORST fill
qty        = min(qty, book_depth_each_leg, 50 SPY / 5 SPX)              # fat-finger ceiling
HARD ASSERT (not configurable): qty × cap_price × 100 ≤ 0.04 × E
```

### 2.3 Enforcement order (first failure rejects/resizes)

1. **Daily loss lock** — day P&L ≤ −6% of `E_open` → circuit breaker (already in FLATTENING/LOCKED, Gate 0 catches it). Soft tier: realized ≤ −4% → max one more trade, size ×0.5.
2. **Position count** — governor count = `max(broker-reconciled, journal-open)` ≥ 2 → reject.
3. **Daily trade cap** — approved trades today ≥ 3 → reject.
4. **Per-trade 4%** — §2.2 assert.
5. **Portfolio heat 7%** — `heat_i = max(entry_debit_i, mark_i) × qty_i × 100`; `Σheat + new ≤ 0.07×E` else resize down; if resized qty < 1 → reject.
6. **Worst-case-day gate (Gate 11b, new)** — `realized_day_loss + Σheat + heat_new ≤ 0.06 × E_open` else resize/reject. Closes the arithmetic hole where 7% open heat can gap through the −6% "limit."
7. **Broker BP** — required × 1.02 ≤ option BP else reject.

---

## 3. Opening Range, Breakout, MTF — Exact Parameters

- **OR**: 1-min bars 09:30:00–09:34:59. `ORH/ORL/ORW = ORH−ORL`, `ORM = (ORH+ORL)/2`. Width gates per G-S2. OR coherence logged, not scored.
- **Breakout**: first 1-min close beyond `OR edge ± 0.0003×ORM`. Evaluated at bar close only. CLV per §1.2. Extension gate 0.50×ORW (G-S4). One breakout attempt per level per side per day (G-S9).
- **RVOL**: cumulative volume 09:30→t ÷ 20-day **median** same-window cumulative. Bands per §1.2.
- **MTF**: EMA(9,21) on 5m/15m/daily(EMA20); session-anchored VWAP with slope over 6×1m bars; `f_ema` trend proxy on 5m (G-S7). ATR(14) Wilder on 5m and daily. RSI/ADX computed and logged, zero weight, no gates.
- **Timing**: scoring 09:50–11:30; score TTL = one 1-min bar AND 90 s to first order submit; mid-ladder rescore before each re-peg, abort ladder if recomputed score < 91. Afternoon window exists in config, **default off**, unlockable only by ≥30 afternoon shadow outcomes clearing the §2.1 Wilson bound.

---

## 4. Regime Module

### 4.1 Interface (contract identical for rules and HMM)

Quant's protocol verbatim: `RegimeState {TREND_UP, TREND_DOWN, RANGE, UNKNOWN}`, `VolRegime {LOW, NORMAL, HIGH, EXTREME}`, frozen `RegimeOutput{ts, state, probs (Σ=1±1e−6), confidence=max(probs), vol_regime, vol_probs, features, model_id}`; `RegimeModel.update(bar_5m, daily_ctx, vix_ctx) → RegimeOutput`, `.warm() → bool`. Gating: LONG only in TREND_UP, SHORT only in TREND_DOWN, RANGE/UNKNOWN = no trade, confidence ≥ 0.70 floor, EXTREME = lockout. Model swap is a config line.

### 4.2 rules_v1 (exact)

On closed SPY 5m bars, emits UNKNOWN until 09:50 (4 bars):

```
f_ema  = (EMA9_5m − EMA21_5m)/ATR_5m(14);  f_vwap = (Close − VWAP)/ATR_5m
f_day  = (Close − PrevDayClose)/ATR_daily(14)
TREND_UP   iff f_ema > +0.30 AND f_vwap > +0.25 AND f_day > −0.25   (mirror DOWN)
RANGE      otherwise
probs: winner 0.85, runner-up 0.10, rest 0.05 (softened degenerate)
```

Vol regime: VIX bands **14 / 22 / 30** (≈25th/75th/95th pct 2015–2025; Architect's 13/18/26 and SR's 13/20/26/32 replaced). Escalate-only bumps, max one: `VIX9D/VIX > 1.05` (backwardation) +1 level; `RV5_YangZhang/VIX > 1.20` +1 level. HIGH bumped = EXTREME = locked out. Governor independently hard-rejects at VIX ≥ 32 regardless.

### 4.3 hmm3_v1 upgrade path

Gaussian HMM, 3 states (4 only if BIC improves >10). Daily features `[log_ret_close, log(RV5_YZ), |overnight_gap|/ATR_d]`, standardized; ≥3 yr training, EM 10 restarts, refit Sunday, params frozen intraday (forward filter only). State labeling by (mean ret, vol); relabel on refit requires manual ack. Composition: HMM argmax, but if HMM=TREND_UP while intraday `f_ema` AND `f_vwap` both negative → emit RANGE. Confidence = filtered prob, same 0.70 gate. Fail-closed: model artifact >10 trading days old or checksum failure → fall back to rules_v1, logged.

---

## 5. Macro Cluster + Event Layer

### 5.1 Residual-only scoring (instruments: VIX, TNX 10y, DXY — nothing else scores)

1. Weekly refit, 60 days of 5-min returns: `r_m = α + β_m·r_SPY + ε` (VIX in level changes; expect β ≈ −1.0 to −1.3 pts per +1% SPY).
2. At signal time: `resid_m = Δm(09:30→t) − β_m × ΔSPY(09:30→t)`; `z_m = resid_m / σ(ε_m)` session-window-matched.
3. Residual established using data through the bar **before** the breakout bar (co-move on the breakout bar is definitionally not confirmation).
4. Points per §1.2 row 5; **contradiction** (any `z ≤ −1.0` against direction) → adjustment −6.
5. ES–NQ dropped entirely (β≈1, residual is noise); gold/oil/FX logged at zero weight.

**Fail-closed ops (Architect):** nightly systemd timer refits σ, Sunday full β refit; artifact versioned + checksummed in `state/models/`. Artifact >10 trading days old or checksum fail → MACRO forced 0 AND −6 penalty forced OFF.

### 5.2 Event blackout table (governor keeps its own calendar copy; also mirrored signal-side as `event_gate=0`)

| Event | Rule |
|---|---|
| FOMC statement/presser, CPI, PPI, NFP, GDP-adv, PCE, Powell testimony | No entries `[t−30 min, t+15 min]` |
| FOMC day | No entries after 12:30 ET (belt; 11:30 cutoff already binds) |
| CPI / NFP day | No entries before 10:00 ET |
| Monthly OPEX / quad-witching | RVOL floor 1.75; quad-witch open hour = blackout |
| High-impact contradicting headline (Fed speaker, geopolitical, policy post) ≤30 min | `event_gate = 0` (sentiment veto) |
| Forced directional bias active, trade against it | `bias_conflict = 0.5` |
| Half-day session | No entries after (close − 90 min); force-flat scales to (close − 30 min) |

---

## 6. Vertical Construction, Liquidity Gates, Order Ladders

### 6.1 Structure
LONG bias → bull call debit vertical; SHORT → bear put debit vertical; expiry = today only. SPX: SPXW PM-settled roots only, AM-settled refused.

### 6.2 Strikes, width, debit
- Long leg: |Δ| ∈ **[0.45, 0.60], target 0.50**, tie-break tighter leg spread. *(EE's 0.55 target lost: it prices 0.48–0.55W, unreachable under the debit cap — Quant's C3 consistency fix adopted.)*
- Width: SPY **2** (fallback 3, never 1); SPX **20** (fallback 25, never <15).
- Debit acceptance at combo mid: **0.30W ≤ net_mid ≤ 0.42W**. Ladder price cap **min(mid + 0.50×edge, 0.45W)** → RR at worst fill = 0.55/0.45 = **1.22 ≥ 1.20 floor** (resolves EE's internal 0.50-cap-vs-RR-1.20 contradiction in favor of the RR floor). Governor outer wall: reject net_mid < 0.05W (quote error) or > 0.55W. *(SR's 0.60 and RM's 0.65 ceilings rejected: post-friction breakeven 63–66%, structurally unprofitable.)*

### 6.3 Liquidity gates (both legs; at decision, pre-submit, and between rungs)

| Metric | SPY | SPX |
|---|---|---|
| Per-leg spread | ≤ max($0.05, 10% of mid) | ≤ max($0.60, 8% of mid) |
| Combined vertical spread `(long_ask−short_bid)−(long_bid−short_ask)` | ≤ $0.08 | ≤ $1.00 |
| Open interest per leg | ≥ 500 | ≥ 200 |
| Top-of-book each side, each leg | ≥ max(25, contracts) — **size down to book**, reject < 1 | ≥ max(10, contracts) |
| Leg volume today | ≥ 500 | ≥ 200 |
| Quote staleness | ≤ 1500 ms | ≤ 1500 ms |
| Mid sanity | leg mid > $0.05 | leg mid > $0.30 |

*(SR's $0.03/1.5% gates lost: 4% of a $0.50 OTM-leg mid = 2 cents, self-DOS; his 25×contracts depth = 1,100 resting contracts at $100k equity.)*

### 6.4 Entry ladder (atomic multi-leg limit, TIF=DAY; never market, never leg in)

```
rung_0 = combo_mid;  step = max(1 tick, 0.15×edge);  dwell = 4 s;  max_rungs = 4
price_cap = min(combo_mid + 0.50×edge, 0.45×W);  never above combo_natural
tick: SPY 0.01; SPX 0.05 (<$3.00) / 0.10 — round DOWN entries, UP exit-sells
```
Re-peg via cancel-replace; re-anchor to new mid if it improved ≥1 tick. **Abandon** on: rungs exhausted (~16–20 s), underlying moves 0.15% against direction mid-ladder, any §6.3 gate fails on re-check, intent age > 90 s, or recomputed score < 91. Partial fill: work remainder one extra rung, then cancel; filled portion stands (trivially compliant). Cooldown 300 s per (symbol, direction, strikes), journaled, restart-surviving. Pre-submit and pre-replace re-assert: `qty × price_cap × 100 ≤ 0.04×E`. *(RM's rung-0 at mid+0.30×edge lost: donates ~1.5–2% of debit on the ~60% of entries that fill at mid.)*

### 6.5 Exit ladders

- **(a) Profit/time (normal):** rung 0 = mid; step max(1 tick, 0.25×edge); dwell 3 s; 5 rungs; floor mid − 0.75×edge.
- **(b) Stop-loss:** rung 0 = mid − 0.25×edge; step max(1 tick, 0.35×edge); dwell 2 s; 4 rungs; floor = natural (hit the bid).
- **(c) Flatten (breaker/EOD/kill):** limit at natural, dwell 2 s, one re-peg natural − 2 ticks, then chase the bid every 2 s; never resting > 6 s; if quotes crossed/garbage, price at `max(intrinsic_est − 2 ticks, 1 tick)`. **No true market orders anywhere in the system.** If legging out is ever forced: **buy the short leg back FIRST**, then sell the long leg (unit test asserts ordering — selling long first manufactures a naked short).

### 6.6 SPY vs SPX rule
1. `contracts_spy ≤ 15` → SPY.
2. `contracts_spy > 15` → SPX iff `floor(contracts_spy/10) ≥ 1` AND SPX passes §6.3 AND SPX granularity (1 contract ≈ $900 risk at 0.45×20) does not break the 4% / heat / worst-case-day gates on this account — if it does, stay SPY reduced, **never round up**.
3. Refuse both (`NO_VENUE`) when: both fail gates; VIX > 40 with spreads > 2× gate; inside blackout; staleness > 10 s on the index complex. Most days the correct venue is neither.

---

## 7. Exit Priority Engine (governor 1-s loop; strict numeric pre-emption; higher rule cancel-confirms lower rule's working order before replacing; unconfirmed cancel in 3 s → no replacement until status resolves)

| P | Trigger (exact) | Action |
|---|---|---|
| **P0** | Any circuit-breaker trigger (day P&L ≤ −6% E_open, manual kill, DEGRADED escalation, heat > 8.5% on reconcile) | Flatten all, class (c). Beats a position up 80%. |
| **P1** | **15:30:00 ET** | Unconditional flatten all, class (c). Escalate every open position to bid-chase at **15:38**; alert if any open at **15:42**; incident + LOCKED assert if any exists at **15:45**. SPY and SPX identical. |
| **P2** | Reconciled heat `Σ max(entry_debit, mark)×qty×100 > 7%×E` | Close largest-heat position until ≤ 7%. |
| **P3** | Spread mark ≤ **0.50 × entry_debit** on **2 consecutive marks ≥1 s apart**; mid valid only if combo spread ≤ 2× its gate, else REST snapshot; both paths stale → escalate as P5 | Close, class (b). |
| **P4a** | Mark ≥ **min(1.65 × entry_debit, 0.80 × W)** | Close, class (a). (Reconciles SR's +65%-of-debit with RM's 60%-of-remaining; beyond 0.80W the bid thins and reward per unit terminal gamma is negative-sum.) |
| **P4b** | 1-min close back through the OR trigger level AND position P&L < +10% of debit | Close, class (a) — breakout failure. |
| **P5** | Held ≥ **90 min** AND mark < 1.10 × entry_debit; OR open at **14:50** with mark < entry_debit | Close, class (a). (14:50 flush mostly moot under the 11:30 cutoff; kept as belt.) |

Stops fire on the **spread mark**, never underlying-only (vol crush/spike decouples them); P4b is the only underlying-referencing rule and it is confirmation, not the stop.

---

## 8. RiskGovernor + Circuit Breaker + Lockout

### 8.1 TradeIntent (the only message signal→governor; JSON over ZeroMQ, pydantic-validated, unknown fields rejected)

`{intent_id (uuid4), ts_created, symbol ∈ {SPY,SPX}, direction ∈ {CALL_DEBIT,PUT_DEBIT}, long_strike, short_strike, expiry (=today), score, score_breakdown (components/adjustments/multipliers), signal_snapshot_ts, est_debit (audit-only)}`. **No quantity field. No prices used for money math.**

### 8.2 Check pipeline (strict order, fail-fast; all data governor-sourced)

```
G0  LOCKOUT file exists or state ≠ NORMAL                → REJECT
G1  Schema/sanity: symbol, direction, expiry==today, strike ordering,
    width ∈ {SPY:1,2,3 | SPX:5..25}, score ≥ min_score(93),
    recomputed clamp(Σc+Σa,0,100)×Πm == score ± 0.01     → REJECT("SCORE_MISMATCH")
G2  Duplicate/burst: intent_id LRU 24h; same structure <300 s; >3 intents/60 s
    (burst_counter ≥3/session → SOFT_PAUSE); trades today ≥ 3
G3  Staleness/clock: now−ts_created >5 s; now−snapshot_ts >3 s; score age >90 s;
    own underlying tick age >2 s; |clock−broker| >2 s → DEGRADED
G4  Window: 09:50–11:30 ET, exchange-calendar aware, half-day rule
G5  Event blackout: governor's OWN calendar (§5.3); own VIX subscription ≥32 → REJECT
G6  Equity fetch: E ≤ 0, non-finite, or |E/E_last −1| >0.15 unexplained → last-known-good;
    LKG >15 min old → DEGRADED, REJECT
G7  Daily loss: day_pnl ≤ −6%×E_open → FLATTENING; ≤ −4% → soft tier (1 trade, ×0.5)
G8  Position count: max(broker, journal) ≥ 2
G9  Liquidity both legs (§6.3) + debit outer wall (0.05W, 0.55W]
G10 Sizing (§2.2) + fat-finger ceiling + HARD ASSERT ≤4%×E
G11 Heat ≤7%×E (max(debit,mark) basis; resize-down allowed)
G11b Worst-case-day: realized_loss + Σheat + heat_new ≤ 6%×E_open
G12 Broker BP ≥ required×1.02
G13 Pre-flight re-quote <1 s old; natural moved >10% since G9 → REJECT("QUOTE_MOVED")
→ single atomic multi-leg limit order, §6.4 ladder, client tag GM0-{date}-{seq}
```

Every decision → `decisions.jsonl` with full check trace and stable enum reason code. A governor that rejects nothing is broken (dashboard rejection histogram).

### 8.3 Governor state machine (authoritative; signal machine is advisory)

States: `RECONCILING, NORMAL, SOFT_PAUSE, DEGRADED, ENTERING, MANAGING, FLATTENING, LOCKED`.

```
NORMAL→SOFT_PAUSE : burst≥3 | 2 stop-outs within 30 min | 5 consecutive liquidity rejects
NORMAL→DEGRADED   : clock skew>2 s | equity fetch fail ×3 | stream stale>10 s w/ position
                    (marks fall back to REST) | broker heartbeat lost
DEGRADED→NORMAL   : integrity restored, stable 5 continuous min
DEGRADED→FLATTENING: unresolved 10 min with any open position | both data paths stale >30 s
ANY→FLATTENING    : day_pnl ≤ −6%×E_open | manual kill | 15:30 ET | reconciled heat >8.5%
FLATTENING→LOCKED : all positions broker-confirmed closed, or 10 close attempts exhausted (+page)
LOCKED→NORMAL     : ONLY next session ≥09:29 ET AND human CLI `gm0 unlock --ack-loss`
                    (logged with acknowledged loss). No other code path exists.
```

Flatten mechanics: (1) write `LOCKOUT` file atomically FIRST (tmp+rename) — crash mid-flatten reboots into LOCKED; (2) cancel all working orders, verify by re-list; (3) close each spread via class (c); legging forced → short leg bought back first; (4) poll flat; (5) LOCKED + push/email + red banner.

### 8.4 Persistence
`E_open` snapshotted 09:29:00 to `session_YYYYMMDD.json`, atomic, **immutable** — intraday restart reloads, never re-snapshots; missing file → boot DEGRADED (no entries), restore only via `gm0 restore-eopen --from-journal` (logged). Intent LRU, trade count, cooldowns in `governor.db` (SQLite WAL) — restart cannot reset caps. Boot: read LOCKOUT → reconcile broker-as-truth → position triage: journal-matched → adopt with journaled entry/stops; **GM0-tagged but unjournaled → close immediately + LOCKED** (our books are corrupt); **untagged → foreign: never touch, never count in position cap, BP impact flows via G12**. *(RM's close-everything-unknown lost: liquidates the human's discretionary positions; Architect's adopt-at-mark lost: re-anchors stops 40% low.)*

### 8.5 Non-bypass (three layers, one boundary)
1. **Boundary = OS process split + credential quarantine**: `TT_*` env vars exist only in governor_proc; governor verifies their absence from signal_proc `/proc/<pid>/environ` at boot. No credentials → no Session → no orders under arbitrary signal-side code execution.
2. **Tripwires**: import-linter CI contract (signals/features/regime/scoring forbidden from `tastytrade`, `execution`, `governor`) + `_RISK_TOKEN` capability check inside governor_proc. Explicitly not the boundary.
3. **Chokepoint**: exactly one SDK order call site, `RiskGovernor._submit`, unit-test-asserted. Hard constraints (4/7/2/−6, min-score floor 90) live in `governor.yaml`, checksummed at boot, code-validated as ceilings — config may tighten, never loosen.

---

## 9. Architecture: Processes, Modules, Trading-Day State Machine

### 9.1 Topology (three systemd units)

```
gm0-signal.service     data/ features/ regime/ scoring/       — NO TT credentials
gm0-governor.service   risk/ execution/ engine-authority      — sole holder of BOTH sessions
                       (data_session prod market data; trade_session per mode)
gm0-dashboard.service  Streamlit, 127.0.0.1, read-only
IPC: ZeroMQ PUSH/PULL 127.0.0.1:7301, JSON TradeIntent.
Governor pushes its state to signal 1×/s (optimization only — governor rejects regardless).
DBs, one writer each: governor→journal.db (orders/fills/decisions/transitions = money truth);
signal→signal.db (score_history/features/shadow trades). Dashboard reads both read-only.
```

### 9.2 Module tree (Architect's, revised) + import layers

```
godmode0dte/
├── app_signal.py / app_governor.py      # composition roots, one per process
├── clock.py  domain/{types,intent,errors}.py  config/{schema,loader}.py     (L0)
├── data/{feed,bars,macro,calendar_feed,sentiment,journal}.py               (L1)
├── features/{opening_range,breakout,mtf,featureset}.py                     (L2)
├── regime/{classifier,vol_regime,hmm_stub}.py                              (L3)
├── scoring/{components,engine,explain}.py                                  (L4)  ← signal_proc top
├── risk/{gate,sizing,heat,breakers,lockout}.py                             (L5)
├── execution/{broker,paper,chains,orders,reconcile}.py                     (L6)  ← governor only
├── engine/{states,machine,events}.py                                       (L7)
└── monitoring/{logsetup,snapshot,tradelog,dashboard/streamlit_app.py}      (L7)
```
Lower layers never import higher; `scoring` cannot import `risk`/`execution`; only `execution` imports `tastytrade`. Enforced by import-linter layered contract in CI + the process split at runtime. Single asyncio loop per process; single-writer `AppState` owned by the engine task; `asyncio.PriorityQueue` (CRITICAL=breakers/clock, HIGH=fills, NORMAL=signals, LOW=telemetry) so a breaker can never race behind a signal; immutable snapshot published by atomic reference swap; TaskGroup crash → log fatal → if positions open, emergency class-(c) flatten with LOCKOUT written first → exit(1) → systemd `Restart=on-failure` → RECONCILING.

### 9.3 Signal-proc trading-day state machine (advisory)

| From | Trigger | Guard | To |
|---|---|---|---|
| BOOT | config valid | — | PRE_MARKET / (intraday) SCANNING or NO_TRADE_DAY |
| PRE_MARKET | 09:30:00 | feeds healthy (else HALTED_ERROR) | OPENING_RANGE |
| OPENING_RANGE | 09:35:00 | — | RANGE_EVAL |
| RANGE_EVAL | width dual-test pass AND no full-day blackout | — | WARMUP (until 09:50) → SCANNING |
| RANGE_EVAL | width fail OR blackout | — | NO_TRADE_DAY |
| SCANNING | score ≥ 93 on 1m close | window 09:50–11:30, governor state = NORMAL | SIGNAL_PENDING (emit intent; TTL 15 s) |
| SIGNAL_PENDING | governor APPROVED | — | (governor: ENTERING) → SCANNING continues if capacity |
| SIGNAL_PENDING | rejection / TTL / rescore < 91 | — | SCANNING (RejectionEvent) |
| SCANNING | 11:30 | — | MONITOR_ONLY (shadow logging continues to 16:00) |
| any | 16:00 | — | END_OF_DAY |

Governor-side ENTERING/MANAGING/FLATTENING/LOCKED per §8.3. `ENTERING` is reachable only with an `ApprovedOrder` in hand (transition function signature requires it). Every transition and rejection is a structured JSON event (`TransitionEvent`, `RejectionEvent` schemas per Architect §2.3, with `governor_version` + `score_version` stamped on every row; **calibration pools only within version**).

---

## 10. Config, Trade Log, Dashboard

### 10.1 `governor.yaml` (hard-side; checksummed at boot; code ceilings 4/7/2/6/90 cannot be exceeded)

```yaml
mode: paper                      # paper | cert | live; live also requires env GM0_CONFIRM_LIVE=YES + no LOCKOUT
risk: {max_trade_pct: 4.0, max_heat_pct: 7.0, max_positions: 2, daily_loss_pct: 6.0,
       soft_loss_pct: 4.0, soft_size_mult: 0.5, max_trades_per_day: 3,
       worst_case_day_pct: 6.0, fat_finger_max_contracts: {SPY: 50, SPX: 5},
       min_score: 93, min_score_compiled_floor: 90,
       ladder_phase: A, ladder: {A: {93: 2.0}, B: {93: 2.0, 97: 3.0}, C: {93: 2.0, 97: 4.0}}}
windows: {entry: ["09:50","11:30"], afternoon_enabled: false,
          force_flat: "15:30", flat_escalate: "15:38", flat_alert: "15:42",
          flat_assert: "15:45", eopen_snapshot: "09:29"}
staleness: {intent_s: 5, snapshot_s: 3, score_s: 90, tick_s: 2, clock_skew_s: 2,
            quote_ms: 1500, both_paths_flatten_s: 30}
events: {blackout_pre_min: 30, blackout_post_min: 15, fomc_entry_cutoff: "12:30",
         cpi_nfp_no_entry_before: "10:00", opex_rvol_floor: 1.75}
liquidity:
  spy: {leg_spread: "max(0.05, 0.10*mid)", combo_spread: 0.08, oi: 500, tob: 25, vol: 500}
  spx: {leg_spread: "max(0.60, 0.08*mid)", combo_spread: 1.00, oi: 200, tob: 10, vol: 200}
structure: {delta_band: [0.45, 0.60], delta_target: 0.50,
            width: {SPY: 2, SPY_fallback: 3, SPX: 20, SPX_fallback: 25},
            debit_mid_band: [0.30, 0.42], ladder_cap_w: 0.45, rr_floor: 1.20,
            debit_outer_wall: [0.05, 0.55], spx_escalation_contracts: 15}
entry_ladder: {rung0: mid, step: "max(1_tick, 0.15*edge)", dwell_s: 4, max_rungs: 4,
               abandon_underlying_adverse_pct: 0.15, cooldown_s: 300}
exits: {stop_frac_debit: 0.50, stop_debounce_marks: 2, tp: "min(1.65*debit, 0.80*W)",
        time_stop_min: 90, time_stop_mark_mult: 1.10, loser_flush: "14:50"}
vix: {bands: [14, 22, 30], governor_hard_reject: 32}
```

### 10.2 `signal.yaml` (score-side; may be tuned; governor unaffected)

```yaml
score: {weights: {orb: 25, rvol: 20, vwap: 15, regime: 20, macro: 8, vixpref: 6, mtf: 6},
        macro_contradiction_adj: -6, dow_adj_ceiling: 2,
        multipliers: [event_gate, stale_gate, bias_conflict], rescore_abort_below: 91}
or: {window: ["09:30","09:35"], breakout_buffer_pct: 0.0003,
     w_atr_gate: [0.10, 0.35], w_pct_gate: [0.0007, 0.0040], ext_gate: 0.50, clv_gate: 0.50}
rvol: {bands: {2.5: 20, 2.0: 15, 1.5: 10, 1.0: 4}, hard_zero_below: 1.0, lookback_days: 20, stat: median}
mtf: {ema: [9, 21], daily_ema: 20, f_ema_gate: 0.30}
regime: {model: rules, hmm_path: null, confidence_floor: 0.70, full_credit_conf: 0.85}
macro: {instruments: [VIX, TNX, DXY], beta_window_days: 60, refit: weekly,
        z_confirm: 0.5, z_strong: 1.5, z_contradict: -1.0, artifact_max_age_days: 10}
shadow: {log_score_floor: 75, quote_cadence_s: 5}
paper_fill: {model: "worse(mid + 0.35*edge, friction_floor)",
             friction: {spy_leg_side: 0.04, spx_leg_side: 0.30, fee_per_contract: 1.00, spx_all_in_side: 1.60}}
```

### 10.3 Trade log schema (`trades` in journal.db — every field required for §2.1 phase gates)

```
trade_id, session_date, mode, governor_version, score_version,
signal_ts, entry_ts, exit_ts, holding_minutes,
underlying, direction, long_strike, short_strike, width, expiry,
score_total, breakdown_components_json, breakdown_adjustments_json, breakdown_multipliers_json,
regime, regime_confidence, vol_regime, vix_at_entry, vix9d_ratio, dow,
or_width_pct, or_width_atr, breakout_ts, breakout_clv, rvol_at_entry, macro_z_json,
equity_at_entry, e_open, phase, risk_pct, contracts, cap_price, debit_fill,
entry_mid_at_signal, entry_slippage, leg_spreads_at_entry_json, quote_age_ms,
order_replace_count, time_to_fill_s,
exit_reason (P0|P1|P2|P3|P4a|P4b|P5|abandoned), exit_fill, exit_slippage,
pnl_gross, pnl_net_fees, pnl_pct_of_debit, pnl_pct_of_equity,
mae_pct_of_debit, mfe_pct_of_debit,
heat_before, heat_after, concurrent_at_entry, shadow (bool)
```

`score_history` (signal.db) logs **every** 1-min evaluation with full breakdown (sub-75 evals are the null distribution); `rejections` logs every gate refusal with stage + reason enum. Shadow capture: every eval ≥ 75 journals hypothetical legs (EE §6 selection) at 5-s quote cadence through simulated exit under live management rules, filled at the paper model.

### 10.4 Dashboard (Streamlit, read-only)

1. Status header: governor state badge, mode, equity, day P&L $ / % vs −6%, heat gauge vs 7%, worst-case-day gauge vs 6%, LOCKOUT banner.
2. Score strip: current score vs 93 line, today's sparkline, component/adjustment/multiplier bars.
3. OR card: H/L/width (pct + ATR), dual-gate verdict, breakout levels, price marker, decay clock.
4. Regime & macro: state + confidence, VIX band + bumps, residual-z table with β artifact age.
5. Positions: legs, debit, mark, P&L, distance to P3/P4a, time-stop countdown, exit-rule armed.
6. Rejection feed: last 50 with stage + reason code; daily rejection histogram (empty histogram = alarm).
7. Transition timeline (both machines).
8. Feed health: stream vs REST age, macro/calendar age, clock skew, heartbeat.

### 10.5 Go-live acceptance (all required)
≥30 paper trading days; ≥200 shadow outcomes logged; three archived drills (forced −6% flatten+lock, feed-kill with open position, restart with open position — transition logs are the acceptance artifacts); Phase A sizing only; two-process topology verified (credentials absent from signal_proc environ); `mode: live` + `GM0_CONFIRM_LIVE=YES` + no LOCKOUT. Zero-trade days are the product working, not failing.
---

## Implementation Status (code vs. spec)

The Python package implements the spec's decisions with these mappings and
known deltas:

**Implemented as specified:** arbitrated score weights (micro/event/DOW at
zero weight, gates instead); 09:50–11:30 entry window; Phase-A flat 2% ladder
with 4% compiled ceiling; heat = Σ max(entry_debit, mark); exit priority table
P0–P5 including the 2-consecutive-marks stop confirmation, the 1.65×debit /
0.80×width profit cap, the OR-trigger structure stop with the +10% P&L
condition, the 90-min stale-hold stop, the 14:50 flush, and 15:30 force-flat;
one-shot rule (G-S9); delta band 0.45–0.60; debit acceptance 0.30–0.42 W with
0.45 W ladder cap; VIX 14–22 preference band and ≥32 hard gate; regime
confidence floor 0.70; circuit-breaker lockout persistence with fail-safe
restore; config validators that allow tightening but reject loosening.

**Simplified relative to spec (single-process, same guarantees):** the
two-process ZeroMQ topology is collapsed into one asyncio process — the
structural guarantee (signal code cannot reach the broker) is enforced by the
token-gated `ApprovedTrade` constructor and import direction instead of a
process boundary. Score algebra uses weighted components + hard gates rather
than the components/adjustments/multipliers triple; the governor re-checks
threshold, staleness, window, and sizing rather than recomputing the full
score. RVOL uses a rolling 20-day per-minute-slot baseline with session-median
fallback (graduated banding via config), not the cumulative-volume ratio.

**Governance items (operator process, not code):** Phase B/C ladder unlocks,
per-weekday adjustment activation, threshold recalibration, OPEX RVOL floor,
and the SPY→SPX escalation rule are documented procedures gated on the logged
decision/trade history in `state/`.

## Audit Round 2 (applied)

A second 11-agent loop audited the implementation itself (5 domain auditors
reading the code → adversarial cross-examination → lead-engineer synthesis).
Confirmed and fixed: equity-fetch failure no longer disables exits (breaker
trips after ~30s of dead feed with positions open); all runtime tasks are
supervised with a fatal-crash flatten path; entry/exit cancel-vs-fill races
resolve order status to terminal before proceeding (an unconfirmed cancel gets
no replacement rung); boot now ADOPTS pairable live positions instead of
abandoning them under lockout; the −6% baseline persists across restarts;
marks no longer freeze on zero-bid short legs and stops fire on 0.00 marks
but never on stale ones; DXLink loops reconnect with full resubscription.
Upgrades: sizing at the worst permitted fill (cap_price) with a broker-side
assert; session-anchored bars everywhere; spec §4.2 regime rules (fixed 0.85
probabilities, 4-bar warmup) plus the ADX warmup-seed fix; chase gate,
breakout freshness decay, no-free-points MTF scoring; SPXW-only SPX chains,
tick snapping, between-rung re-quoting, combo-spread gate, depth-clamped
sizing; dashboard staleness banner + autorefresh; MAE/MFE and full score
context logged per trade. Killed in debate: ApprovedTrade forgery hardening
(no boundary exists inside one interpreter — the broker-reference control is
the real wall), HMM rewrite (dead code until v2 is scheduled), and the
backtest HAC-SE fix (offline-only; required before any weight promotion).
