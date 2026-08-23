"""Manual kill switch: the presence of state/skyfire/KILL halts
everything instantly — reject all intents and flatten. The file is
written by the operator or by the out-of-process MCP monitor; it
survives restarts until deleted by a human."""

from __future__ import annotations

from pathlib import Path


class KillSwitch:
    def __init__(self, state_dir: str) -> None:
        self._file = Path(state_dir) / "KILL"

    @property
    def engaged(self) -> bool:
        return self._file.exists()

    def reason(self) -> str:
        if not self.engaged:
            return ""
        try:
            return self._file.read_text().strip() or "manual kill"
        except OSError:
            return "manual kill"
