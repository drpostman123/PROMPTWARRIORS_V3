"""Snapshot store: the runtime writes JSON state; dashboards read it.
Decisions and trades append to JSONL logs for calibration.

The snapshot write is tmp + rename (atomic on POSIX) so a reader never
sees a torn file. JSONL appends reopen the file per record so logrotate
can rename the log losslessly.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "value"):  # Enum
        return obj.value
    return obj


class StateStore:
    def __init__(self, snapshot_path: str, trade_log_path: str, decision_log_path: str) -> None:
        self._snapshot_path = Path(snapshot_path)
        self._trade_log = Path(trade_log_path)
        self._decision_log = Path(decision_log_path)
        for p in (self._snapshot_path, self._trade_log, self._decision_log):
            p.parent.mkdir(parents=True, exist_ok=True)

    def write_snapshot(self, snapshot: dict) -> None:
        snapshot = dict(snapshot, ts=datetime.now(timezone.utc).isoformat())
        tmp = self._snapshot_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(jsonable(snapshot), indent=2))
        tmp.replace(self._snapshot_path)          # atomic on POSIX

    def read_snapshot(self) -> dict:
        if not self._snapshot_path.exists():
            return {}
        try:
            return json.loads(self._snapshot_path.read_text())
        except json.JSONDecodeError:
            return {}

    def log_decision(self, record: dict) -> None:
        self._append(self._decision_log, record)

    def log_trade(self, record: dict) -> None:
        self._append(self._trade_log, record)

    def read_trades(self) -> list[dict]:
        if not self._trade_log.exists():
            return []
        return [json.loads(line) for line in self._trade_log.read_text().splitlines() if line]

    @staticmethod
    def _append(path: Path, record: dict) -> None:
        record = dict(record, ts=datetime.now(timezone.utc).isoformat())
        with path.open("a") as f:
            f.write(json.dumps(jsonable(record)) + "\n")
