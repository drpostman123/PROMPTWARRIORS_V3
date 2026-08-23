"""SQLite persistence (WAL, single writer task).

All agents publish rows to the bus topic "persist"; exactly one task
consumes it and writes, so there is never write contention. Safety state
does NOT live here — the breakers keep their own tiny JSON files, so a
corrupt DB can never un-trip a breaker.

Tables:
  phantom_log  every filtered/vetoed/near-miss candidate + forward returns
  trades       full intent -> verdict -> gate trace -> fill -> exit lineage
  allocations  every CEO reallocation with reasoning and the clamped result
  nav_history  per-sleeve NAV series (feeds 7d Sharpe)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from tradecore.logging import get_logger
from tradecore.statestore import jsonable

log = get_logger("db")

DDL = """
CREATE TABLE IF NOT EXISTS phantom_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  mint TEXT NOT NULL,
  symbol TEXT,
  stage TEXT NOT NULL,
  reject_reason TEXT NOT NULL,
  features_json TEXT NOT NULL,
  price_usd REAL,
  fwd_1h REAL, fwd_6h REAL, fwd_24h REAL,
  dead INTEGER DEFAULT 0,
  backfilled_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_phantom_backfill ON phantom_log (ts)
  WHERE fwd_24h IS NULL AND dead = 0;

CREATE TABLE IF NOT EXISTS trades (
  trade_id TEXT PRIMARY KEY,
  sleeve TEXT, mint TEXT, symbol TEXT, side TEXT,
  intent_ts TEXT, ceo_verdict TEXT, ceo_reasoning TEXT,
  approved_ts TEXT, gate_trace_json TEXT,
  quote_out REAL, fill_out REAL, slippage_pct REAL, price_impact_pct REAL,
  priority_fee_lamports INTEGER, tx_sig TEXT, fill_ts TEXT,
  exit_reason TEXT, pnl_usd REAL, pnl_pct REAL, probation INTEGER
);

CREATE TABLE IF NOT EXISTS allocations (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  regime TEXT,
  greedy_scores_json TEXT,
  targets_json TEXT,
  clamped_targets_json TEXT,
  reasoning TEXT,
  applied INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS nav_history (
  ts TEXT NOT NULL,
  sleeve TEXT NOT NULL,
  nav_usd REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nav_sleeve_ts ON nav_history (sleeve, ts);
"""


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def open(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(DDL)
        await self._db.commit()
        log.info("db_open", path=self._path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # -- writer task ------------------------------------------------------

    async def writer_task(self, queue: asyncio.Queue) -> None:
        """The single writer: consumes {"table": ..., **row} messages."""
        assert self._db is not None
        while True:
            msg = await queue.get()
            try:
                await self._write(msg)
            except (aiosqlite.Error, KeyError, TypeError) as e:
                log.error("db_write_failed", error=str(e), table=msg.get("table"))

    async def _write(self, msg: dict[str, Any]) -> None:
        assert self._db is not None
        table = msg.pop("table")
        row = {k: (json.dumps(jsonable(v)) if isinstance(v, (dict, list)) else v)
               for k, v in msg.items()}
        if table == "trades":
            cols = ", ".join(row)
            updates = ", ".join(f"{k}=excluded.{k}" for k in row if k != "trade_id")
            await self._db.execute(
                f"INSERT INTO trades ({cols}) VALUES ({', '.join('?' * len(row))}) "
                f"ON CONFLICT(trade_id) DO UPDATE SET {updates}",
                list(row.values()))
        else:
            cols = ", ".join(row)
            await self._db.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(row))})",
                list(row.values()))
        await self._db.commit()

    # -- reads used by backfill / CEO / dashboard -------------------------

    async def rows(self, sql: str, params: tuple = ()) -> list[tuple]:
        assert self._db is not None
        async with self._db.execute(sql, params) as cur:
            return list(await cur.fetchall())

    async def execute(self, sql: str, params: tuple = ()) -> None:
        assert self._db is not None
        await self._db.execute(sql, params)
        await self._db.commit()
