"""Economic calendar / event layer.

Events load from a YAML file the operator maintains (or generates from a
calendar API). High-impact events create entry blackouts; FOMC days can
lock the whole session. Headline risk (Fed speakers, high-impact
political posts) is handled the same way: add an event row with a time
window, or an all-day ``bias`` restriction.

Example ``config/econ_calendar.yaml``:

    events:
      - name: CPI
        date: 2026-08-12
        time: "08:30"
        impact: high
      - name: FOMC
        date: 2026-09-16
        time: "14:00"
        impact: high
      - name: "White House tariff announcement watch"
        date: 2026-08-05
        all_day: true
        impact: high
        bias: short          # optional: only allow this direction today
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

from godmode0dte.config import EventConfig
from godmode0dte.models import Direction


@dataclass(frozen=True)
class EconEvent:
    name: str
    date: date
    time: Optional[time]          # None => all-day
    impact: str                   # "high" | "medium" | "low"
    bias: Optional[Direction] = None


@dataclass(frozen=True)
class EventVerdict:
    blackout: bool
    reason: str
    forced_bias: Optional[Direction] = None
    points: int = 0               # event-layer score contribution (0..max)


class EventCalendar:
    def __init__(self, cfg: EventConfig, tz: str = "America/New_York") -> None:
        self._cfg = cfg
        self._tz = ZoneInfo(tz)
        self._events: list[EconEvent] = []
        self._load()

    def _load(self) -> None:
        p = Path(self._cfg.calendar_file)
        if not p.exists():
            return
        raw = yaml.safe_load(p.read_text()) or {}
        for e in raw.get("events", []):
            t = None if e.get("all_day") else time.fromisoformat(str(e.get("time", "09:30")))
            bias = Direction(e["bias"]) if e.get("bias") in ("long", "short") else None
            self._events.append(
                EconEvent(
                    name=str(e["name"]),
                    date=e["date"] if isinstance(e["date"], date) else date.fromisoformat(str(e["date"])),
                    time=t,
                    impact=str(e.get("impact", "medium")).lower(),
                    bias=bias,
                )
            )

    def todays_events(self, today: date) -> list[EconEvent]:
        return [e for e in self._events if e.date == today]

    def evaluate(self, now: datetime, max_points: int) -> EventVerdict:
        """Blackout / bias / points verdict for the current instant.

        Full points when the day is clean; zero (and blackout) inside a
        high-impact window; forced bias passes through when configured.
        """
        now_local = now.astimezone(self._tz)
        today = now_local.date()
        forced: Optional[Direction] = None
        for e in self.todays_events(today):
            if e.impact != "high":
                continue
            if e.name.upper().startswith("FOMC") and self._cfg.fomc_day_lockout:
                return EventVerdict(True, f"FOMC day lockout ({e.name})")
            if e.bias is not None:
                forced = e.bias
            if e.time is None:
                # All-day high-impact watch without full lockout: bias only, zero points.
                if e.bias is None:
                    return EventVerdict(True, f"all-day high-impact event: {e.name}")
                continue
            event_dt = datetime.combine(e.date, e.time, tzinfo=self._tz)
            start = event_dt - timedelta(minutes=self._cfg.blackout_before_min)
            end = event_dt + timedelta(minutes=self._cfg.blackout_after_min)
            if start <= now_local <= end:
                return EventVerdict(True, f"blackout window for {e.name} at {e.time}")
        clean = not self.todays_events(today)
        points = max_points if clean and forced is None else max(0, max_points - 3)
        return EventVerdict(False, "clear" if clean else "events today, outside windows",
                            forced_bias=forced, points=points)
