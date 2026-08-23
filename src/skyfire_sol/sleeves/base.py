"""Sleeve agent base: publishes TradeIntents to the bus, reacts to fills
and allocation targets. Sleeves never see the executor — intents flow
bus -> CEO verdict -> SafetyGate -> (token-minted, direct call) executor."""

from __future__ import annotations

import abc
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from skyfire_sol.blackboard import Blackboard
from skyfire_sol.config import AppConfig
from skyfire_sol.models import (
    Fill,
    IntentKind,
    Side,
    SleeveId,
    TradeIntent,
    Urgency,
)


class SleeveAgent(abc.ABC):
    sleeve: SleeveId

    def __init__(self, cfg: AppConfig, blackboard: Blackboard,
                 publish: Callable[[str, object], Awaitable[None]]) -> None:
        self._cfg = cfg
        self._bb = blackboard
        self._publish = publish

    def target_nav_usd(self) -> float:
        snap = self._bb.snapshot()
        return snap.nav_usd * snap.allocations_target.get(self.sleeve.value, 0.0) / 100.0

    async def publish_intent(
        self, kind: IntentKind, side: Side, mint: str, quote_mint: str,
        size_usd: float, reason: str, urgency: Urgency = Urgency.NORMAL,
        position_id: Optional[str] = None, qty_raw: Optional[int] = None,
    ) -> TradeIntent:
        intent = TradeIntent(
            intent_id=uuid.uuid4().hex[:12], sleeve=self.sleeve, kind=kind,
            side=side, mint=mint, quote_mint=quote_mint, size_usd=size_usd,
            urgency=urgency, reason=reason, ts=datetime.now(timezone.utc),
            position_id=position_id, qty_raw=qty_raw)
        await self._publish("intents", intent)
        return intent

    @abc.abstractmethod
    async def run(self) -> None: ...

    async def on_fill(self, fill: Fill) -> None: ...

    async def on_allocation(self, targets: dict[str, float]) -> None: ...
