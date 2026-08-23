"""YieldAgent — YIELD sleeve: the ballast.

v1 venue: jitoSOL (liquid staking, ~7-8% APY + MEV). Holding jitoSOL IS
the position; the sleeve converts SOL<->jitoSOL through Jupiter to track
its target NAV. The concentrated Orca USDC/SOL LP from the spec is a
fast-follow behind the LpVenue protocol (no maintained Python SDK today
— NullLp stubs it, and the sleeve is complete without it).
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from skyfire_sol.config import JITOSOL_MINT, WSOL_MINT
from skyfire_sol.models import IntentKind, Side, SleeveId, Urgency
from skyfire_sol.sleeves.base import SleeveAgent
from tradecore.logging import get_logger

log = get_logger("yield_sleeve")

LOOP_S = 900.0
JITOSOL_DECIMALS = 9


class LpVenue(Protocol):
    """Future concentrated-LP venue (Orca whirlpool). Deliberately tiny."""

    async def position_value_usd(self) -> float: ...
    async def rebalance(self, target_usd: float) -> None: ...


class NullLp:
    async def position_value_usd(self) -> float:
        return 0.0

    async def rebalance(self, target_usd: float) -> None:
        return None


class YieldAgent(SleeveAgent):
    sleeve = SleeveId.YIELD

    def __init__(self, cfg, blackboard, publish, lp: LpVenue | None = None) -> None:
        super().__init__(cfg, blackboard, publish)
        self._lp: LpVenue = lp or NullLp()

    async def run(self) -> None:
        while True:
            try:
                await self.rebalance_pass()
            except Exception as e:                  # noqa: BLE001 — supervised anyway
                log.error("yield_rebalance_failed", error=str(e))
            await asyncio.sleep(LOOP_S)

    async def rebalance_pass(self) -> None:
        cfg = self._cfg.sleeves.yield_
        snap = self._bb.snapshot()
        target = self.target_nav_usd()
        jito_px = snap.prices_usd.get(JITOSOL_MINT, 0.0)
        held_raw = snap.balances_raw.get(JITOSOL_MINT, 0)
        held_usd = held_raw / 10 ** JITOSOL_DECIMALS * jito_px if jito_px > 0 else 0.0
        delta = target - held_usd
        if abs(delta) < cfg.min_conversion_usd:
            return
        if delta > 0:
            # Stake more: buy jitoSOL with wSOL.
            await self.publish_intent(
                IntentKind.REBALANCE, Side.BUY, JITOSOL_MINT, WSOL_MINT, delta,
                reason=f"yield under target by ${delta:.2f}", urgency=Urgency.NORMAL)
        else:
            if jito_px <= 0:
                return
            qty_raw = min(int(abs(delta) / jito_px * 10 ** JITOSOL_DECIMALS), held_raw)
            if qty_raw <= 0:
                return
            await self.publish_intent(
                IntentKind.REBALANCE, Side.SELL, JITOSOL_MINT, WSOL_MINT, abs(delta),
                reason=f"yield over target by ${-delta:.2f}", urgency=Urgency.NORMAL,
                qty_raw=qty_raw)
        log.info("yield_rebalance_intent", delta_usd=round(delta, 2))
