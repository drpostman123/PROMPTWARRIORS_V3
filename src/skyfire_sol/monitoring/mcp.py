"""SKYFIRE MCP monitor — a separate PROCESS, never inside the trading loop.

A minimal, dependency-free MCP stdio server (JSON-RPC 2.0) for remote
monitoring: read-only views over the heartbeat, snapshot, decision
journal, and phantom/trade DB, plus exactly one write: the kill switch
(creating state/skyfire/KILL). It holds no wallet, no RPC, no executor —
even a fully compromised monitor can only observe and halt.

Run:  python -m skyfire_sol.monitoring.mcp [--state-dir state/skyfire]
Wire it into an MCP client as a stdio server with that command.

Implements the minimum of the MCP protocol needed by standard clients:
initialize, notifications/initialized, tools/list, tools/call, ping.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {"name": "status",
     "description": "Heartbeat + safety state + NAV summary (staleness flagged).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "positions",
     "description": "Open positions with marks and drawdown from peak.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "journal_tail",
     "description": "Last N gate/CEO decisions from decisions.jsonl.",
     "inputSchema": {"type": "object", "properties": {
         "n": {"type": "integer", "default": 20}}}},
    {"name": "phantom_stats",
     "description": "Phantom-log counts by stage/reason + mean forward returns.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "trades_tail",
     "description": "Last N rows from the trade journal DB.",
     "inputSchema": {"type": "object", "properties": {
         "n": {"type": "integer", "default": 20}}}},
    {"name": "kill",
     "description": "ENGAGE THE KILL SWITCH: halts all trading and flattens. "
                    "The KILL file persists until an operator deletes it.",
     "inputSchema": {"type": "object", "properties": {
         "reason": {"type": "string"}}, "required": ["reason"]}},
    {"name": "go_full_size",
     "description": "THE MANUAL PROBATION BUTTON: lift the reduced-size "
                    "probation throttle to full size. Human-only decision — "
                    "there is no automatic lift. Check status.clean_fills "
                    "readiness first.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "back_to_probation",
     "description": "Re-engage the probation size throttle (deletes the "
                    "FULL_SIZE button file).",
     "inputSchema": {"type": "object", "properties": {}}},
]


class Monitor:
    def __init__(self, state_dir: str) -> None:
        self.dir = Path(state_dir)

    def _read_json(self, name: str) -> dict:
        try:
            return json.loads((self.dir / name).read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def status(self) -> dict:
        hb = self._read_json("heartbeat.json")
        snap = self._read_json("snapshot.json")
        age = None
        if hb.get("ts"):
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(hb["ts"])).total_seconds()
        return {
            "heartbeat": hb, "heartbeat_age_s": age,
            "stale": age is None or age > 30,
            "nav_usd": snap.get("nav_usd"),
            "allocations_target": snap.get("allocations_target"),
            "safety": snap.get("safety"),
            "kill_engaged": (self.dir / "KILL").exists(),
        }

    def positions(self) -> list[dict]:
        return self._read_json("snapshot.json").get("positions", [])

    def journal_tail(self, n: int = 20) -> list[dict]:
        path = self.dir / "decisions.jsonl"
        if not path.exists():
            return []
        lines = path.read_text().splitlines()[-n:]
        return [json.loads(ln) for ln in lines if ln.strip()]

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.dir / 'skyfire.db'}?mode=ro", uri=True)

    def phantom_stats(self) -> dict:
        try:
            with self._db() as db:
                by_stage = db.execute(
                    "SELECT stage, reject_reason, COUNT(*) FROM phantom_log "
                    "GROUP BY stage, reject_reason ORDER BY 3 DESC").fetchall()
                fwd = db.execute(
                    "SELECT stage, AVG(fwd_1h), AVG(fwd_6h), AVG(fwd_24h), "
                    "SUM(dead) FROM phantom_log GROUP BY stage").fetchall()
            return {"counts": [{"stage": s, "reason": r, "n": n}
                               for s, r, n in by_stage],
                    "forward_returns": [
                        {"stage": s, "avg_fwd_1h": a, "avg_fwd_6h": b,
                         "avg_fwd_24h": c, "dead": d}
                        for s, a, b, c, d in fwd]}
        except sqlite3.Error as e:
            return {"error": str(e)}

    def trades_tail(self, n: int = 20) -> list[dict]:
        try:
            with self._db() as db:
                db.row_factory = sqlite3.Row
                rows = db.execute(
                    "SELECT * FROM trades ORDER BY fill_ts DESC LIMIT ?",
                    (n,)).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            return [{"error": str(e)}]

    def kill(self, reason: str) -> dict:
        (self.dir / "KILL").write_text(
            f"{reason} (via MCP, {datetime.now(timezone.utc).isoformat()})\n")
        return {"engaged": True, "reason": reason,
                "note": "delete state/skyfire/KILL to re-enable trading"}

    def go_full_size(self) -> dict:
        (self.dir / "FULL_SIZE").write_text(
            f"lifted via MCP {datetime.now(timezone.utc).isoformat()}\n")
        return {"probation": False,
                "note": "full size engaged; back_to_probation reverses this"}

    def back_to_probation(self) -> dict:
        try:
            (self.dir / "FULL_SIZE").unlink()
        except FileNotFoundError:
            pass
        return {"probation": True}


def handle(monitor: Monitor, req: dict) -> dict | None:
    rid = req.get("id")
    method = req.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "skyfire-monitor", "version": "0.1.0"}}}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = req["params"]["name"]
        args = req["params"].get("arguments") or {}
        try:
            fn = getattr(monitor, name, None)
            if fn is None or name.startswith("_"):
                raise ValueError(f"unknown tool {name}")
            result: Any = fn(**args)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps(result, indent=2, default=str)}]}}
        except Exception as e:                       # noqa: BLE001 — reported to client
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"error: {e}"}],
                "isError": True}}
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir", default="state/skyfire")
    args = ap.parse_args()
    monitor = Monitor(args.state_dir)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(monitor, req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
