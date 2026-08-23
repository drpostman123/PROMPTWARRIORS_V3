"""Snapshot store — thin shim over tradecore.statestore.

Keeps the ``StateStore(cfg: DataConfig)`` constructor and the private
``_jsonable`` name that godmode code and tests import.
"""

from __future__ import annotations

from typing import Any

from godmode0dte.config import DataConfig
from tradecore.statestore import StateStore as _StateStore
from tradecore.statestore import jsonable


def _jsonable(obj: Any) -> Any:
    return jsonable(obj)


class StateStore(_StateStore):
    def __init__(self, cfg: DataConfig) -> None:
        super().__init__(cfg.snapshot_path, cfg.trade_log_path, cfg.decision_log_path)
