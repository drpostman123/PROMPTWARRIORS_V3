"""Heartbeat file: tiny JSON written every few seconds. External
watchers (MCP monitor, ops scripts) alarm on staleness — a stale
heartbeat means the dashboard is showing a corpse."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from skyfire_sol.blackboard import Blackboard


def write_heartbeat(path: str, bb: Blackboard, state: str) -> None:
    snap = bb.snapshot()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "nav_usd": round(snap.nav_usd, 2),
        "positions": len(snap.positions),
        "breaker": snap.safety.breaker,
        "kill": snap.safety.kill,
    }))
    tmp.replace(p)
