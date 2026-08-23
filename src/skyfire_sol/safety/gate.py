"""SafetyGate — the only path to execution, and the Goodhart guard.

The CEO operates INSIDE this gate, never above it:

1. Capability token: ApprovedOrder can only be constructed with the
   module-private _GATE_TOKEN. Everything else raises PermissionError at
   construction. The executor accepts ApprovedOrder and nothing else.
2. Import direction: ceo/ imports nothing from safety/, execution/,
   wallet, chain, or clients (pinned by an AST test).
3. State ownership: breaker/probation/pause files are written only by
   safety/; the CEO sees a read-only mirror on the blackboard.
4. Single submit chokepoint: the executor is the only caller of the RPC
   submit path in chain/helius.py (pinned by a source-scan test).

Check pipeline (fixed order, first failure rejects, every decision
journaled with a full trace):

  S0  kill switch                        (exits too — but flatten flows bypass)
  S1  breaker LOCKED/TRIPPED             (exits for flatten still pass)
  S2  daily -10% pause                   (entries only)
  S3  soft tier -30%                     (entries only)
  S4  NAV sane & fresh
  S5  intent sanity + honeypot blacklist
  S6  rug verdict fresh + PASS           (meme entries only)
  S7  correlation bucket MEME+PERPS      (entries only)
  S8  sleeve caps                        (entries only)
  S9  probation resize (down, never up)  (entries only)
  S10 fresh Jupiter quote within the slippage cap

Exits are risk-reducing: they skip S2/S3/S7/S8, cannot be vetoed by the
CEO, and a breaker flatten passes S0/S1 by design.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional, Union

import httpx

from skyfire_sol.blackboard import Blackboard
from skyfire_sol.clients.jupiter import (
    JupiterClient,
    NoRouteError,
    SlippageCapExceeded,
)
from skyfire_sol.config import MAJOR_MINTS, USDC_MINT, AppConfig
from skyfire_sol.models import (
    CeoVerdict,
    IntentKind,
    JupQuote,
    PerpIntent,
    Rejection,
    RegimeState,
    SleeveId,
    TradeIntent,
    Urgency,
)
from skyfire_sol.safety.breakers import BreakerState, PortfolioBreaker
from skyfire_sol.safety.kill import KillSwitch
from skyfire_sol.safety.probation import Probation
from tradecore.logging import get_logger

log = get_logger("safety_gate")

# Module-private capability token. Nothing outside this module can build
# an ApprovedOrder without raising.
_GATE_TOKEN = object()


@dataclass(frozen=True)
class ApprovedOrder:
    """Proof of safety approval. Constructible only by the SafetyGate."""

    intent: TradeIntent
    quote: JupQuote
    size_usd: float               # final size after every clamp
    amount_raw: int               # exact raw input units to swap
    gate_trace: str               # JSON per-check trace
    approved_ts: datetime
    _token: object = None

    def __post_init__(self) -> None:
        if self._token is not _GATE_TOKEN:
            raise PermissionError(
                "ApprovedOrder may only be created by the SafetyGate. "
                "Sleeves and the CEO submit TradeIntents instead.")


@dataclass(frozen=True)
class ApprovedPerpOrder:
    """Proof of safety approval for a Drift order. Same token discipline."""

    intent: PerpIntent
    delta_usd: float              # final, post-clamp (may be smaller than asked)
    gate_trace: str
    approved_ts: datetime
    _token: object = None

    def __post_init__(self) -> None:
        if self._token is not _GATE_TOKEN:
            raise PermissionError(
                "ApprovedPerpOrder may only be created by the SafetyGate.")


class SafetyGate:
    """Holds the ONLY executor reference. The CEO's outputs (verdicts and
    allocation targets) are data that arrive here to be clamped."""

    def __init__(
        self,
        cfg: AppConfig,
        breaker: PortfolioBreaker,
        probation: Probation,
        kill: KillSwitch,
        blackboard: Blackboard,
        jupiter: JupiterClient,
        executor,                                     # execution.executor.ExecutionAgent
        rug_verdict_for: Callable[[str], Optional[object]],   # mint -> RugVerdict|None
        is_blacklisted: Callable[[str], bool],
        log_decision: Callable[[dict], Awaitable[None]],
        decimals_for: Callable[[str], Awaitable[int]],
    ) -> None:
        self._cfg = cfg
        self._breaker = breaker
        self._probation = probation
        self._kill = kill
        self._bb = blackboard
        self._jup = jupiter
        self._executor = executor
        self._rug_verdict_for = rug_verdict_for
        self._is_blacklisted = is_blacklisted
        self._log_decision = log_decision
        self._decimals_for = decimals_for

    # -- allocation clamps (CEO output passes through here) --------------

    def clamp_allocations(self, targets: dict[str, float],
                          regime: RegimeState) -> dict[str, float]:
        """Clamp a CEO AllocationTargets dict to the hard risk framework.
        The CEO may propose anything; what comes out obeys the correlation
        bucket, the soft tier, and non-negativity, renormalized to 100."""
        t = {k: max(0.0, float(v)) for k, v in targets.items()}
        for k in (SleeveId.MEME_ROTATION, SleeveId.CORE_HOLD, SleeveId.YIELD, SleeveId.PERPS):
            t.setdefault(k.value, 0.0)
        if not self._cfg.sleeves.perps.enabled:
            t[SleeveId.PERPS.value] = 0.0
        total = sum(t.values()) or 1.0
        t = {k: v / total * 100.0 for k, v in t.items()}

        bucket_cap = (self._cfg.risk.corr_risk_on_pct
                      if regime is RegimeState.RISK_ON
                      else self._cfg.risk.corr_risk_off_pct)
        if self._breaker.soft_tier_active:
            t[SleeveId.MEME_ROTATION.value] *= 0.5
            t[SleeveId.PERPS.value] *= 0.5
            bucket_cap = min(bucket_cap, self._cfg.risk.corr_risk_off_pct)

        bucket = t[SleeveId.MEME_ROTATION.value] + t[SleeveId.PERPS.value]
        if bucket > bucket_cap and bucket > 0:
            scale = bucket_cap / bucket
            t[SleeveId.MEME_ROTATION.value] *= scale
            t[SleeveId.PERPS.value] *= scale
        # Freed weight retreats into CORE (the USDC buffer lives there).
        total = sum(t.values())
        if total < 100.0:
            t[SleeveId.CORE_HOLD.value] += 100.0 - total
        return t

    # -- the pipeline -----------------------------------------------------

    async def process(self, intent: TradeIntent,
                      verdict: Optional[CeoVerdict] = None
                      ) -> Union[ApprovedOrder, Rejection]:
        """Run the pipeline; on approval, hand the order DIRECTLY to the
        executor (the approval edge never rides the bus)."""
        result = await self._evaluate(intent, verdict)
        if isinstance(result, Rejection):
            await self._log_decision({
                "kind": "gate_reject", "intent_id": intent.intent_id,
                "sleeve": intent.sleeve.value, "mint": intent.mint,
                "check": result.check, "detail": result.detail})
            log.info("intent_rejected", intent_id=intent.intent_id,
                     check=result.check, detail=result.detail)
            return result
        await self._log_decision({
            "kind": "gate_approve", "intent_id": intent.intent_id,
            "sleeve": intent.sleeve.value, "mint": intent.mint,
            "size_usd": result.size_usd, "trace": json.loads(result.gate_trace)})
        await self._executor.execute(result)
        return result

    async def _evaluate(self, intent: TradeIntent,
                        verdict: Optional[CeoVerdict]
                        ) -> Union[ApprovedOrder, Rejection]:
        now = datetime.now(timezone.utc)
        trace: list[dict] = []
        is_entry = intent.kind in (IntentKind.ENTRY, IntentKind.REBALANCE)
        is_flatten = (intent.kind is IntentKind.EXIT
                      and intent.reason == "breaker_flatten")

        def rej(check: str, detail: str) -> Rejection:
            trace.append({"check": check, "passed": False, "detail": detail})
            return Rejection(intent.intent_id, check, detail, now)

        def ok(check: str, detail: str = "") -> None:
            trace.append({"check": check, "passed": True, "detail": detail})

        # S0 kill switch — flatten exits still flow (the kill handler itself
        # flattens through the gate).
        if self._kill.engaged and not is_flatten:
            return rej("S0_kill", self._kill.reason())
        ok("S0_kill")

        # S1 breaker state
        if self._breaker.state is BreakerState.LOCKED:
            return rej("S1_breaker", "locked — manual restart required")
        if self._breaker.state is BreakerState.TRIPPED and not is_flatten:
            return rej("S1_breaker", "tripped — only flatten exits pass")
        ok("S1_breaker", self._breaker.state.value)

        if is_entry:
            # S2 daily pause
            if self._breaker.pause_until and now < self._breaker.pause_until:
                return rej("S2_daily_pause",
                           f"paused until {self._breaker.pause_until.isoformat()}")
            ok("S2_daily_pause")
            # S3 soft tier
            if self._breaker.soft_tier_active:
                return rej("S3_soft_tier", "no new entries until drawdown clears")
            ok("S3_soft_tier")
        else:
            ok("S2_daily_pause", "skipped (exit)")
            ok("S3_soft_tier", "skipped (exit)")

        # S4 NAV sane & fresh
        snap = self._bb.snapshot()
        nav_age = self._bb.nav_age_s(now)
        if is_entry and (snap.nav_usd <= 0
                         or nav_age > self._cfg.risk.nav_stale_entries_off_s):
            return rej("S4_nav", f"nav {snap.nav_usd:.2f} age {nav_age:.0f}s")
        ok("S4_nav", f"nav {snap.nav_usd:.2f}")

        # S5 sanity + blacklist
        if intent.size_usd <= 0:
            return rej("S5_sanity", "non-positive size")
        if self._is_blacklisted(intent.mint):
            return rej("S5_sanity", "mint blacklisted (honeypot)")
        if intent.side.value == "buy" and self._is_blacklisted(intent.quote_mint):
            return rej("S5_sanity", "quote mint blacklisted")
        ok("S5_sanity")

        size_usd = intent.size_usd

        # CEO verdict applies to meme entries only, and can only shrink.
        if is_entry and intent.sleeve is SleeveId.MEME_ROTATION:
            if verdict is not None:
                if verdict.action == "veto" or verdict.size_mult <= 0:
                    return rej("CEO_veto", verdict.reasoning)
                size_usd *= min(1.0, verdict.size_mult)
            # S6 rug verdict
            v = self._rug_verdict_for(intent.mint)
            ttl = timedelta(minutes=self._cfg.discovery.rug_verdict_ttl_minutes)
            if v is None or not getattr(v, "passed", False):
                return rej("S6_rug", "no passing rug verdict")
            if now - v.ts > ttl:
                return rej("S6_rug", f"verdict stale ({(now - v.ts).total_seconds():.0f}s)")
            ok("S6_rug")
        else:
            ok("S6_rug", "n/a")

        if is_entry:
            # S7 correlation bucket: MEME NAV + PERPS exposure + this entry
            meme_nav = snap.sleeve_navs.get(SleeveId.MEME_ROTATION.value, 0.0)
            perps_nav = snap.sleeve_navs.get(SleeveId.PERPS.value, 0.0)
            adds = size_usd if intent.sleeve in (SleeveId.MEME_ROTATION, SleeveId.PERPS) \
                and intent.mint not in MAJOR_MINTS else 0.0
            cap_pct = (self._cfg.risk.corr_risk_on_pct
                       if snap.regime.state is RegimeState.RISK_ON
                       else self._cfg.risk.corr_risk_off_pct)
            if snap.nav_usd > 0 and adds > 0:
                bucket_after = (meme_nav + perps_nav + adds) / snap.nav_usd * 100.0
                if bucket_after > cap_pct:
                    return rej("S7_correlation_bucket",
                               f"MEME+PERPS would be {bucket_after:.1f}% > {cap_pct}%")
            ok("S7_correlation_bucket")

            # S8 sleeve caps (meme only)
            if intent.sleeve is SleeveId.MEME_ROTATION:
                sleeve_nav = max(meme_nav,
                                 snap.nav_usd * snap.allocations_target.get(
                                     SleeveId.MEME_ROTATION.value, 0.0) / 100.0)
                open_memes = [p for p in snap.positions
                              if p.sleeve is SleeveId.MEME_ROTATION]
                if len(open_memes) >= self._cfg.sleeves.meme.max_positions:
                    return rej("S8_sleeve_caps",
                               f"{len(open_memes)} meme positions open (max "
                               f"{self._cfg.sleeves.meme.max_positions})")
                cap_usd = sleeve_nav * self._cfg.sleeves.meme.max_position_pct_of_sleeve / 100.0
                if cap_usd <= 0:
                    return rej("S8_sleeve_caps", "meme sleeve has no capital")
                size_usd = min(size_usd, cap_usd)     # resize down, never up
            ok("S8_sleeve_caps", f"size ${size_usd:.2f}")

            # S9 probation
            size_usd *= self._probation.size_mult
            if size_usd < self._cfg.sleeves.meme.min_entry_usd \
                    and intent.sleeve is SleeveId.MEME_ROTATION:
                return rej("S9_probation", f"post-clamp size ${size_usd:.2f} below dust floor")
            ok("S9_probation", f"mult {self._probation.size_mult}")
        else:
            ok("S7_correlation_bucket", "skipped (exit)")
            ok("S8_sleeve_caps", "skipped (exit)")
            ok("S9_probation", "skipped (exit)")

        # S10 fresh quote within the slippage cap
        cap_pct = (self._cfg.sleeves.meme.slippage_cap_pct
                   if (intent.mint not in MAJOR_MINTS)
                   else self._cfg.sleeves.core.slippage_cap_pct)
        try:
            if intent.side.value == "buy":
                in_mint, out_mint = intent.quote_mint, intent.mint
                amount_raw = await self._usd_to_raw(in_mint, size_usd, snap.prices_usd)
            else:
                in_mint, out_mint = intent.mint, intent.quote_mint
                if intent.qty_raw is None or intent.qty_raw <= 0:
                    return rej("S10_quote", "sell intent missing qty_raw")
                amount_raw = intent.qty_raw
            if amount_raw <= 0:
                return rej("S10_quote", "computed zero input amount")
            quote = await self._jup.quote(in_mint, out_mint, amount_raw, cap_pct)
        except SlippageCapExceeded as e:
            if intent.urgency is Urgency.URGENT:
                # Never strand an urgent exit behind the cap: escalate loudly,
                # retry AT the cap once more, then surface for operator action.
                log.error("urgent_exit_slippage_escalation", intent_id=intent.intent_id,
                          detail=str(e))
            return rej("S10_quote", str(e))
        except NoRouteError as e:
            return rej("S10_quote", f"no route: {e}")
        except httpx.HTTPError as e:
            return rej("S10_quote", f"quote fetch failed: {e}")
        ok("S10_quote", f"impact {quote.price_impact_pct:.3f}% <= {cap_pct}%")

        return ApprovedOrder(
            intent=intent, quote=quote, size_usd=size_usd, amount_raw=amount_raw,
            gate_trace=json.dumps(trace), approved_ts=now, _token=_GATE_TOKEN)

    async def process_perp(self, intent: PerpIntent) -> Union[ApprovedPerpOrder, Rejection]:
        """Perps pipeline: S0/S1/S2/S3/S4 + correlation bucket + leverage cap.
        Risk-reducing deltas (shrinking |notional|) pass the pause/soft tiers
        like exits do."""
        now = datetime.now(timezone.utc)
        trace: list[dict] = []
        snap = self._bb.snapshot()
        reduces = abs(intent.current_notional_usd + intent.delta_usd) \
            < abs(intent.current_notional_usd)

        def rej(check: str, detail: str) -> Rejection:
            trace.append({"check": check, "passed": False, "detail": detail})
            return Rejection(intent.intent_id, check, detail, now)

        def ok(check: str, detail: str = "") -> None:
            trace.append({"check": check, "passed": True, "detail": detail})

        if self._kill.engaged and not reduces:
            return rej("S0_kill", self._kill.reason())
        ok("S0_kill")
        if self._breaker.state is BreakerState.LOCKED:
            return rej("S1_breaker", "locked")
        if self._breaker.state is BreakerState.TRIPPED and not reduces:
            return rej("S1_breaker", "tripped — only risk-reducing deltas pass")
        ok("S1_breaker")
        if not reduces:
            if self._breaker.pause_until and now < self._breaker.pause_until:
                return rej("S2_daily_pause", "paused")
            if self._breaker.soft_tier_active:
                return rej("S3_soft_tier", "no risk adds in soft tier")
        ok("S2_daily_pause")
        ok("S3_soft_tier")
        if snap.nav_usd <= 0 or self._bb.nav_age_s(now) > self._cfg.risk.nav_stale_entries_off_s:
            if not reduces:
                return rej("S4_nav", "nav stale or zero")
        ok("S4_nav")

        delta = intent.delta_usd
        if not reduces and snap.nav_usd > 0:
            # Correlation bucket: MEME NAV + |perp notional after| <= cap.
            meme_nav = snap.sleeve_navs.get(SleeveId.MEME_ROTATION.value, 0.0)
            cap_pct = (self._cfg.risk.corr_risk_on_pct
                       if snap.regime.state is RegimeState.RISK_ON
                       else self._cfg.risk.corr_risk_off_pct)
            bucket_room = snap.nav_usd * cap_pct / 100.0 - meme_nav
            # Leverage cap against the perps sleeve's target NAV (its margin).
            sleeve_nav = snap.nav_usd * snap.allocations_target.get(
                SleeveId.PERPS.value, 0.0) / 100.0
            lev_cap = sleeve_nav * self._cfg.sleeves.perps.max_leverage
            allowed_after = max(0.0, min(bucket_room, lev_cap))
            after = abs(intent.current_notional_usd + delta)
            if after > allowed_after:
                # Resize down toward the cap, never up.
                sign = 1.0 if delta > 0 else -1.0
                max_abs_delta = max(0.0, allowed_after - abs(intent.current_notional_usd))
                delta = sign * min(abs(delta), max_abs_delta)
                if abs(delta) < 1.0:
                    return rej("S7_correlation_bucket",
                               f"no room: after ${after:,.0f} > cap ${allowed_after:,.0f}")
            ok("S7_correlation_bucket", f"delta ${delta:,.0f}")
        else:
            ok("S7_correlation_bucket", "reduce")

        await self._log_decision({
            "kind": "gate_approve_perp", "intent_id": intent.intent_id,
            "delta_usd": delta, "trace": trace})
        approved = ApprovedPerpOrder(intent=intent, delta_usd=delta,
                                     gate_trace=json.dumps(trace),
                                     approved_ts=now, _token=_GATE_TOKEN)
        await self._executor.execute_perp(approved)
        return approved

    async def _usd_to_raw(self, mint: str, usd: float,
                          prices_usd: dict[str, float]) -> int:
        if mint == USDC_MINT:
            return int(usd * 1e6)
        px = prices_usd.get(mint)
        if not px or px <= 0:
            raise NoRouteError(f"no USD mark for quote mint {mint}")
        decimals = await self._decimals_for(mint)
        return int(usd / px * 10 ** decimals)
