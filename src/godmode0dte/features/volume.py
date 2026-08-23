"""Relative volume vs a rolling 20-day same-minute-of-day baseline.

The baseline persists in ``state/volume_profile.json`` and updates at
end of day. Until 5 days of history exist, falls back to the current
session's median bar volume (documented conservatism: with a weak
baseline, rel-volume scores are less trustworthy and the breakout
component will rarely max out).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from godmode0dte.models import Bar


class RelativeVolume:
    def __init__(self, path: str = "state/volume_profile.json",
                 tz: str = "America/New_York", days: int = 20) -> None:
        self._path = Path(path)
        self._tz = ZoneInfo(tz)
        self._days = days
        # slot ("HH:MM") -> list of last N daily volumes
        self._profile: dict[str, list[float]] = {}
        if self._path.exists():
            try:
                self._profile = json.loads(self._path.read_text())
            except json.JSONDecodeError:
                self._profile = {}

    def _slot(self, ts: datetime) -> str:
        local = ts.astimezone(self._tz)
        return f"{local.hour:02d}:{local.minute:02d}"

    def ratio(self, bar: Bar, session_bars: list[Bar]) -> float:
        """This bar's volume vs baseline for its minute slot."""
        hist = self._profile.get(self._slot(bar.ts), [])
        if len(hist) >= 5:
            base = median(hist)
        else:
            vols = [b.volume for b in session_bars if b.volume > 0]
            base = median(vols) if vols else 0.0
        return bar.volume / base if base > 0 else 1.0

    def end_of_day_update(self, session_bars: list[Bar]) -> None:
        for b in session_bars:
            slot = self._slot(b.ts)
            arr = self._profile.setdefault(slot, [])
            arr.append(b.volume)
            del arr[:-self._days]
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._profile))
