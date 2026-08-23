"""HlRotationAgent — HL_ROTATION sleeve: meme momentum rotation on
Hyperliquid perps.

Universe = HL's liquid tail with majors excluded (config `exclude`) —
in practice the meme/momentum set. Long-only rotation, every 5 minutes:

  1. rank eligible markets by 24h momentum (volume- and funding-gated)
  2. target book = top-N, equal-weight slices of the sleeve target,
     scaled by regime (risk_on 1.0, choppy 0.5, risk_off/unknown 0)
  3. close what fell out of the ranking, tripped the -40% trail from
     the session peak, or now pays rich funding; open what entered it

All orders leave as HlIntents through the SafetyGate; the sleeve holds
no exchange handle. Skipped candidates go to the phantom log — the same
training flywheel as the Solana sleeve.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from skyfire_sol.blackboard import Blackboard
from skyfire_sol.config import AppConfig, HlConfig
from skyfire_sol.models import (
    HlIntent,
    HlMarketStat,
    PhantomCandidate,
    RegimeState,
    SleeveId,
)
from skyfire_sol.persistence.phantom import PhantomLog
from skyfire_sol.safety.breakers import BreakerState, PortfolioBreaker
from tradecore.logging import get_logger

log = get_logger("hl_sleeve")


def eligible(stats: list[HlMarketStat], cfg: HlConfig) -> list[HlMarketStat]:
    """Volume/funding/exclusion gates, then positive 24h momentum only."""
    out = []
    for s in stats:
        if s.coin in cfg.exclude:
            continue
        if s.day_volume_usd < cfg.min_day_volume_usd:
            continue
        if s.funding_pct_hr > cfg.max_funding_pct_hr:
            continue                     # paying to hold a crowd position
        if s.ret_24h_pct <= 0 or s.mark_px <= 0:
            continue
        out.append(s)
    return out


def rank(stats: list[HlMarketStat], cfg: HlConfig) -> list[HlMarketStat]:
    """Momentum ranking: 24h return, liquidity-weighted tiebreak."""
    return sorted(stats,
                  key=lambda s: (s.ret_24h_pct, s.day_volume_usd),
                  reverse=True)[: cfg.top_n]


def regime_factor(state: RegimeState) -> float:
    if state is RegimeState.RISK_ON:
        return 1.0
    if state is RegimeState.CHOPPY:
        return 0.5
    return 0.0                           # risk_off / unknown: flat


class HlRotationAgent:
    sleeve = SleeveId.HL_ROTATION

    def __init__(self, cfg: AppConfig, blackboard: Blackboard,
                 publish: Callable[[str, object], Awaitable[None]],
                 venue,                                  # execution.hl_venue.HlVenue
                 breaker: PortfolioBreaker,
                 phantom: Optional[PhantomLog] = None) -> None:
        self._cfg = cfg
        self._bb = blackboard
        self._publish = publish
        self._venue = venue
        self._breaker = breaker
        self._phantom = phantom
        self._peaks: dict[str, float] = {}           # coin -> session peak mark

    async def run(self) -> None:
        while True:
            try:
                await self.rotate_once()
            except Exception as e:                  # noqa: BLE001 — supervised anyway
                log.error("hl_rotate_failed", error=str(e))
            await asyncio.sleep(self._cfg.sleeves.hl.loop_seconds)

    async def _emit(self, coin: str, action: str, notional: float,
                    current: float, mark: float, reason: str) -> None:
        await self._publish("hl_intents", HlIntent(
            intent_id=uuid.uuid4().hex[:12], coin=coin, action=action,
            notional_usd=notional, current_notional_usd=current,
            mark_px=mark, reason=reason, ts=datetime.now(timezone.utc)))

    async def flatten_all(self, reason: str) -> None:
        for coin, pos in (await self._venue.positions()).items():
            await self._emit(coin, "close", 0.0, pos["notional_usd"], 0.0, reason)

    async def rotate_once(self) -> None:
        cfg = self._cfg.sleeves.hl
        if not cfg.enabled or not self._venue.connected:
            return
        snap = self._bb.snapshot()
        positions = await self._venue.positions()

        if self._breaker.state is not BreakerState.ARMED:
            if positions:
                await self.flatten_all("breaker_flatten")
            return

        stats = await self._venue.market_stats()
        by_coin = {s.coin: s for s in stats}
        pool = eligible(stats, cfg)
        top = rank(pool, cfg)
        top_coins = {s.coin for s in top}
        now = datetime.now(timezone.utc)

        # Track session peaks for held coins; trail-stop off the peak.
        for coin in positions:
            mark = by_coin[coin].mark_px if coin in by_coin else 0.0
            if mark > 0:
                self._peaks[coin] = max(self._peaks.get(coin, mark), mark)

        # Closes: fell out of the ranking, funding turned rich, or trailed.
        for coin, pos in positions.items():
            mark = by_coin[coin].mark_px if coin in by_coin else 0.0
            peak = self._peaks.get(coin, mark)
            reason = None
            if coin not in top_coins:
                reason = "rotated_out"
            elif peak > 0 and mark > 0 \
                    and (peak - mark) / peak * 100.0 >= cfg.trail_from_peak_pct:
                reason = "trail_stop"
            if reason:
                await self._emit(coin, "close", 0.0, pos["notional_usd"],
                                 mark, reason)
                self._peaks.pop(coin, None)

        # Opens: new entrants, sized as equal-weight slices of the regime-
        # scaled sleeve target. The gate re-clamps (bucket, probation, caps).
        factor = regime_factor(snap.regime.state)
        sleeve_target = snap.nav_usd * snap.allocations_target.get(
            self.sleeve.value, 0.0) / 100.0
        gross_target = sleeve_target * cfg.max_leverage * factor
        slice_usd = gross_target / cfg.top_n if cfg.top_n else 0.0

        for s in top:
            if s.coin in positions:
                continue
            if slice_usd < cfg.min_order_usd:
                if self._phantom:
                    await self._phantom.record(PhantomCandidate(
                        mint=f"HL:{s.coin}", symbol=s.coin, stage="near_miss",
                        reject_reason=("regime_flat" if factor == 0.0
                                       else "no_capital"),
                        features={"ret_24h_pct": s.ret_24h_pct,
                                  "day_volume_usd": s.day_volume_usd,
                                  "funding_pct_hr": s.funding_pct_hr},
                        price_usd=s.mark_px, ts=now))
                continue
            self._peaks[s.coin] = s.mark_px
            await self._emit(s.coin, "open", slice_usd, 0.0, s.mark_px,
                             f"momentum rank: 24h {s.ret_24h_pct:+.1f}%, "
                             f"vol ${s.day_volume_usd / 1e6:,.0f}M")

        log.info("hl_rotation_pass", eligible=len(pool), top=sorted(top_coins),
                 held=sorted(positions), regime=snap.regime.state.value,
                 slice_usd=round(slice_usd, 2))
