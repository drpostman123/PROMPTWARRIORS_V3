"""Probation: reduced-size throttle until the OPERATOR pushes the button.

While probation is active every entry is sized down by
probation_size_frac, and the CEO's verdict multiplier modulates size
further (it can only shrink — the gate clamps mult to <= 1). There is
NO automatic lift: full size engages only when a human creates
state/skyfire/FULL_SIZE (via the MCP go_full_size tool or
`touch state/skyfire/FULL_SIZE`). Deleting the file re-engages
probation instantly.

Clean fills (realized slippage within tolerance) are still counted and
persisted — as a READINESS SIGNAL for the operator on the dashboard,
never as a trigger.
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
        d = Path(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        self._counter_file = d / "probation.json"
        self._button_file = d / "FULL_SIZE"
        self.clean_fills = 0
        self._restore()

    def _restore(self) -> None:
        if not self._counter_file.exists():
            return
        try:
            self.clean_fills = int(
                json.loads(self._counter_file.read_text()).get("clean_fills", 0))
        except (json.JSONDecodeError, ValueError, OSError) as e:
            self.clean_fills = 0
            log.error("probation_counter_unreadable", error=str(e))

    def _persist(self) -> None:
        try:
            tmp = self._counter_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"clean_fills": self.clean_fills}))
            tmp.replace(self._counter_file)
        except OSError as e:
            log.error("probation_persist_failed", error=str(e))

    @property
    def active(self) -> bool:
        """Probation holds unless the operator's FULL_SIZE button exists."""
        return not self._button_file.exists()

    @property
    def size_mult(self) -> float:
        return self._cfg.probation_size_frac if self.active else 1.0

    @property
    def ready(self) -> bool:
        """Readiness signal for the operator: enough clean fills observed."""
        return self.clean_fills >= self._cfg.probation_clean_fills

    def record_fill(self, realized_slippage_pct: float) -> None:
        if abs(realized_slippage_pct) <= self._cfg.clean_fill_slippage_pct:
            self.clean_fills += 1
            self._persist()
            if self.active and self.ready:
                log.info("probation_ready",
                         clean_fills=self.clean_fills,
                         detail="operator may push the FULL_SIZE button")
        else:
            log.warning("probation_dirty_fill",
                        realized_slippage_pct=round(realized_slippage_pct, 3),
                        clean_fills=self.clean_fills)
