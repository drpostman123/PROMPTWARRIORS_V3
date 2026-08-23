"""First-session probation: 50% per-position size until 10 clean fills.

A clean fill = realized slippage within tolerance. Violating fills log
loudly and do not increment (the spec says 10 clean fills, not 10
consecutive — misses don't reset the count). Missing/unreadable state
file => probation ON (fail toward small).
"""

from __future__ import annotations

import json
from pathlib import Path

from skyfire_sol.config import RiskConfig
from tradecore.logging import get_logger

log = get_logger("probation")


class Probation:
    def __init__(self, cfg: RiskConfig, state_dir: str) -> None:
        self._cfg = cfg
        self._file = Path(state_dir) / "probation.json"
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self.active = True
        self.clean_fills = 0
        self._restore()

    def _restore(self) -> None:
        if not self._file.exists():
            return                       # fresh boot: probation ON
        try:
            d = json.loads(self._file.read_text())
            self.active = bool(d.get("active", True))
            self.clean_fills = int(d.get("clean_fills", 0))
        except (json.JSONDecodeError, ValueError, OSError) as e:
            self.active, self.clean_fills = True, 0
            log.error("probation_state_unreadable_failing_small", error=str(e))

    def _persist(self) -> None:
        try:
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text(json.dumps(
                {"active": self.active, "clean_fills": self.clean_fills}))
            tmp.replace(self._file)
        except OSError as e:
            log.error("probation_persist_failed", error=str(e))

    @property
    def size_mult(self) -> float:
        return self._cfg.probation_size_frac if self.active else 1.0

    def record_fill(self, realized_slippage_pct: float) -> None:
        if not self.active:
            return
        if abs(realized_slippage_pct) <= self._cfg.clean_fill_slippage_pct:
            self.clean_fills += 1
            if self.clean_fills >= self._cfg.probation_clean_fills:
                self.active = False
                log.info("probation_lifted", clean_fills=self.clean_fills)
            self._persist()
        else:
            log.warning("probation_dirty_fill",
                        realized_slippage_pct=round(realized_slippage_pct, 3),
                        clean_fills=self.clean_fills)
