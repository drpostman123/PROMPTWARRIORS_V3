"""Phantom log + forward-return backfill against a tmp SQLite DB."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from skyfire_sol.persistence.db import Database
from skyfire_sol.persistence.phantom import BackfillJob

NOW = datetime.now(timezone.utc)


async def seeded_db(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.open()
    for i, age_h in enumerate((2.0, 7.0, 26.0, 0.1)):
        await db._write({
            "table": "phantom_log",
            "ts": (NOW - timedelta(hours=age_h)).isoformat(),
            "mint": f"m{i}", "symbol": f"T{i}", "stage": "rug_reject",
            "reject_reason": "liquidity_floor",
            "features_json": {"liquidity_usd": 1.0}, "price_usd": 1.0})
    return db


async def test_backfill_horizons(tmp_path):
    db = await seeded_db(tmp_path)

    async def price(mint):
        return 1.5                                   # +50% everywhere

    await BackfillJob(db, price).backfill_once(NOW)
    rows = await db.rows(
        "SELECT mint, fwd_1h, fwd_6h, fwd_24h, dead FROM phantom_log ORDER BY mint")
    by = {r[0]: r for r in rows}
    assert by["m0"][1] == pytest.approx(50.0)        # 2h old: 1h due
    assert by["m0"][2] is None                       # 6h not yet due
    assert by["m1"][2] == pytest.approx(50.0)        # 7h old: 6h due
    assert by["m2"][3] == pytest.approx(50.0)        # 26h old: 24h due
    assert by["m3"][1] is None                       # 6 minutes old: nothing due
    assert all(r[4] == 0 for r in rows)
    await db.close()


async def test_backfill_marks_dead_tokens(tmp_path):
    db = await seeded_db(tmp_path)

    async def price(mint):
        return None                                  # unpriceable everywhere

    await BackfillJob(db, price).backfill_once(NOW)
    rows = await db.rows("SELECT mint, dead FROM phantom_log WHERE mint != 'm3'")
    assert all(r[1] == 1 for r in rows)
    # dead rows leave the backfill queue: a second pass touches nothing
    touched = await BackfillJob(db, price).backfill_once(NOW)
    assert touched == 0
    await db.close()


async def test_trades_upsert(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.open()
    await db._write({"table": "trades", "trade_id": "t1", "sleeve": "MEME_ROTATION",
                     "side": "buy", "slippage_pct": 0.4})
    await db._write({"table": "trades", "trade_id": "t1", "exit_reason": "trail_stop",
                     "pnl_pct": -12.0})
    rows = await db.rows("SELECT sleeve, exit_reason, pnl_pct FROM trades")
    assert rows == [("MEME_ROTATION", "trail_stop", -12.0)]
    await db.close()
