"""PerpsAgent — PERPS sleeve: directional SOL-PERP on Drift.

Ships last (build order §7) and defaults to perps.enabled=false in YAML.
Signal = the CEO's regime call + the funding-rate filter (never pay more
than max_funding_pct_hr to hold a crowd position). Max 3x leverage
against the sleeve's margin; the dedicated Drift subaccount isolates a
liquidation from every other sleeve.

The agent computes a desired notional and publishes PerpIntents — the
SafetyGate clamps (correlation bucket + leverage) and the executor is
the only Drift submitter.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from skyfire_sol.blackboard import Blackboard
from skyfire_sol.config import AppConfig
from skyfire_sol.models import PerpIntent, RegimeState, SleeveId
from tradecore.logging import get_logger

log = get_logger("perps_sleeve")

LOOP_S = 300.0


class PerpsAgent:
    sleeve = SleeveId.PERPS

    def __init__(self, cfg: AppConfig, blackboard: Blackboard,
                 publish: Callable[[str, object], Awaitable[None]],
                 notional_usd: Callable[[], Awaitable[float]],
                 funding_pct_hr: Callable[[], Awaitable[Optional[float]]]) -> None:
        self._cfg = cfg
        self._bb = blackboard
        self._publish = publish
        self._notional = notional_usd
        self._funding = funding_pct_hr

    async def run(self) -> None:
        while True:
            try:
                await self.pass_once()
            except Exception as e:                  # noqa: BLE001 — supervised anyway
                log.error("perps_pass_failed", error=str(e))
            await asyncio.sleep(LOOP_S)

    async def pass_once(self) -> None:
        if not self._cfg.sleeves.perps.enabled:
            return
        snap = self._bb.snapshot()
        current = await self._notional()
        funding = await self._funding()
        sleeve_nav = snap.nav_usd * snap.allocations_target.get(
            self.sleeve.value, 0.0) / 100.0

        desired = 0.0
        reason = f"regime {snap.regime.state.value}: flat"
        if snap.regime.state is RegimeState.RISK_ON and sleeve_nav > 0:
            if funding is not None and funding > self._cfg.sleeves.perps.max_funding_pct_hr:
                reason = f"risk_on but funding {funding:.3f}%/hr too rich: flat"
            else:
                desired = sleeve_nav * self._cfg.sleeves.perps.max_leverage
                reason = (f"risk_on long: {self._cfg.sleeves.perps.max_leverage}x "
                          f"of ${sleeve_nav:,.0f} margin")

        delta = desired - current
        if abs(delta) < 25.0:
            return
        intent = PerpIntent(
            intent_id=uuid.uuid4().hex[:12], delta_usd=delta,
            current_notional_usd=current, reason=reason,
            ts=datetime.now(timezone.utc))
        await self._publish("perp_intents", intent)
        log.info("perp_intent", delta_usd=round(delta, 2), reason=reason)
