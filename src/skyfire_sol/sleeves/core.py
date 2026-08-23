"""CoreAgent — CORE_HOLD sleeve: SOL + wBTC + wETH + USDC ballast.

Mostly passive: drift-band rebalance toward configured weights, routed
through the same intent -> gate -> executor pipeline as everything else
(REBALANCE intents; majors get the 0.5% slippage cap at the gate).
The sleeve also holds the USDC buffer the breakers retreat into.
"""

from __future__ import annotations

import asyncio

from skyfire_sol.config import USDC_MINT, WBTC_MINT, WETH_MINT, WSOL_MINT
from skyfire_sol.models import IntentKind, Side, SleeveId, Urgency
from skyfire_sol.sleeves.base import SleeveAgent
from tradecore.logging import get_logger

log = get_logger("core_sleeve")

ASSET_MINTS = {"SOL": WSOL_MINT, "wBTC": WBTC_MINT, "wETH": WETH_MINT, "USDC": USDC_MINT}
DECIMALS = {WSOL_MINT: 9, WBTC_MINT: 8, WETH_MINT: 8, USDC_MINT: 6}
REBALANCE_LOOP_S = 900.0


class CoreAgent(SleeveAgent):
    sleeve = SleeveId.CORE_HOLD

    async def run(self) -> None:
        while True:
            try:
                await self.rebalance_pass()
            except Exception as e:                  # noqa: BLE001 — supervised anyway
                log.error("core_rebalance_failed", error=str(e))
            await asyncio.sleep(REBALANCE_LOOP_S)

    def _holdings_usd(self) -> dict[str, float]:
        snap = self._bb.snapshot()
        out: dict[str, float] = {}
        for name, mint in ASSET_MINTS.items():
            raw = snap.balances_raw.get(mint, 0)
            px = 1.0 if mint == USDC_MINT else snap.prices_usd.get(mint, 0.0)
            out[name] = raw / 10 ** DECIMALS[mint] * px
        return out

    async def rebalance_pass(self) -> None:
        cfg = self._cfg.sleeves.core
        target_nav = self.target_nav_usd()
        if target_nav <= 0:
            return
        held = self._holdings_usd()
        # The sleeve claims up to target_nav of the majors held in the wallet;
        # meme positions are separate mints so no double-count.
        for name, weight in cfg.weights.items():
            want = target_nav * weight / 100.0
            have = held.get(name, 0.0)
            drift_pct = abs(have - want) / target_nav * 100.0
            if drift_pct < cfg.rebalance_band_pct or name == "USDC":
                continue
            mint = ASSET_MINTS[name]
            delta = want - have
            if abs(delta) < 10.0:
                continue
            side = Side.BUY if delta > 0 else Side.SELL
            qty_raw = None
            if side is Side.SELL:
                snap = self._bb.snapshot()
                px = snap.prices_usd.get(mint, 0.0)
                if px <= 0:
                    continue
                qty_raw = min(int(abs(delta) / px * 10 ** DECIMALS[mint]),
                              snap.balances_raw.get(mint, 0))
                if qty_raw <= 0:
                    continue
            await self.publish_intent(
                IntentKind.REBALANCE, side, mint, USDC_MINT, abs(delta),
                reason=f"core drift {drift_pct:.1f}pp on {name}",
                urgency=Urgency.NORMAL, qty_raw=qty_raw)
            log.info("core_rebalance_intent", asset=name, side=side.value,
                     delta_usd=round(delta, 2))
