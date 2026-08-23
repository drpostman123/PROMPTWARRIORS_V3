"""Phantom log — the training-data flywheel.

Every candidate that passes discovery but is not entered (rug reject,
entry reject, CEO veto, gate reject, or near-miss ranking) is recorded
with its full feature vector, then a periodic backfill job fills in
forward returns at 1h/6h/24h. Tokens that become unpriceable are marked
dead (treat as ~-100% in analysis).

Structural harmlessness (the godmode ShadowBook discipline): this module
holds no wallet, no executor, no gate reference — it can observe and
write rows, never act.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from skyfire_sol.models import PhantomCandidate, TokenFacts
from skyfire_sol.persistence.db import Database
from tradecore.logging import get_logger
from tradecore.statestore import jsonable

log = get_logger("phantom")

HORIZONS = (("fwd_1h", timedelta(hours=1)), ("fwd_6h", timedelta(hours=6)),
            ("fwd_24h", timedelta(hours=24)))


def features_of(facts: TokenFacts, extra: Optional[dict] = None) -> dict:
    d = jsonable(facts)
    if extra:
        d.update(extra)
    return d


class PhantomLog:
    def __init__(self, publish: Callable[[str, object], Awaitable[None]]) -> None:
        self._publish = publish        # rows ride the persist topic

    async def record(self, cand: PhantomCandidate) -> None:
        await self._publish("persist", {
            "table": "phantom_log",
            "ts": cand.ts.isoformat(),
            "mint": cand.mint, "symbol": cand.symbol,
            "stage": cand.stage, "reject_reason": cand.reject_reason,
            "features_json": cand.features, "price_usd": cand.price_usd})
        log.info("phantom_logged", mint=cand.mint, stage=cand.stage,
                 reason=cand.reject_reason)


class BackfillJob:
    """Every 10 minutes, resolve forward returns for due phantom rows."""

    def __init__(self, db: Database,
                 price_usd: Callable[[str], Awaitable[Optional[float]]],
                 interval_s: float = 600.0) -> None:
        self._db = db
        self._price = price_usd
        self._interval = interval_s

    async def run(self) -> None:
        while True:
            try:
                await self.backfill_once()
            except Exception as e:                     # noqa: BLE001 — never die
                log.error("backfill_failed", error=str(e))
            await asyncio.sleep(self._interval)

    async def backfill_once(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(timezone.utc)
        touched = 0
        for col, horizon in HORIZONS:
            rows = await self._db.rows(
                f"SELECT id, mint, price_usd, ts FROM phantom_log "
                f"WHERE {col} IS NULL AND dead = 0 AND price_usd > 0 "
                f"AND ts <= ? LIMIT 50",
                ((now - horizon).isoformat(),))
            for row_id, mint, base_price, _ts in rows:
                px = await self._price(mint)
                if px is None or px <= 0:
                    await self._db.execute(
                        "UPDATE phantom_log SET dead = 1, backfilled_at = ? WHERE id = ?",
                        (now.isoformat(), row_id))
                else:
                    ret = (px - base_price) / base_price * 100.0
                    await self._db.execute(
                        f"UPDATE phantom_log SET {col} = ?, backfilled_at = ? WHERE id = ?",
                        (round(ret, 4), now.isoformat(), row_id))
                touched += 1
        if touched:
            log.info("backfill_pass", rows=touched)
        return touched
