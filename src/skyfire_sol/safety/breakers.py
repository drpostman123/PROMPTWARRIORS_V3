"""Portfolio circuit breakers, persisted, fail-safe.

Modeled on godmode0dte's CircuitBreaker discipline:
- state lives in tiny JSON files owned by THIS module (never SQLite — a
  corrupt DB must not be able to un-trip a breaker);
- an unreadable state file restores to the SAFE side (locked, paused);
- the daily baseline persists so a mid-day restart cannot re-anchor the
  -10% limit to depleted equity;
- the -60% HWM trip survives restarts and clears only when the operator
  sets SKYFIRE_ACK_DRAWDOWN=YES (the spec's "manual restart");
- a NAV plausibility quarantine keeps one bad price read from moving any
  of it.

Tiers (drawdown from high-water mark unless stated):
  -10% intraday   -> 24h entry pause (positions held)
  -30%            -> soft tier: MEME+PERPS halve, no new entries until -20%
  -60%            -> TRIP: flatten everything to USDC, LOCK, manual restart

Crypto is 24/7: the daily baseline rolls at UTC midnight in-process (no
restart-based rollover like godmode's 08:30 ET timer).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from skyfire_sol.config import RiskConfig
from tradecore.logging import get_logger

log = get_logger("breakers")


class BreakerState(str, Enum):
    ARMED = "armed"
    TRIPPED = "tripped"     # flatten-to-USDC in progress
    LOCKED = "locked"       # flat (or gave up flattening); manual restart only


class PortfolioBreaker:
    def __init__(self, cfg: RiskConfig, state_dir: str) -> None:
        self._cfg = cfg
        d = Path(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        self._hwm_file = d / "hwm.json"
        self._soft_file = d / "soft_tier.json"
        self._daily_file = d / "daily.json"

        self.state = BreakerState.ARMED
        self.hwm_usd = 0.0
        self.soft_tier_active = False
        self.pause_until: Optional[datetime] = None
        self._daily_date: Optional[str] = None
        self._daily_baseline: Optional[float] = None

        # NAV plausibility quarantine
        self._last_sane_nav: Optional[float] = None
        self._suspect_streak = 0

        self._restore()

    # -- persistence (atomic, fail-safe) --------------------------------

    @staticmethod
    def _write(path: Path, payload: dict) -> None:
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(path)
        except OSError as e:
            # In-memory state stays authoritative; only cross-restart
            # persistence is lost. Never raise out of a safety write.
            log.error("breaker_persist_failed", file=str(path), error=str(e))

    def _restore(self) -> None:
        # HWM + trip state. Unreadable file => LOCKED (fail safe).
        if self._hwm_file.exists():
            try:
                d = json.loads(self._hwm_file.read_text())
                self.hwm_usd = float(d["hwm_usd"])
                if d.get("tripped"):
                    if os.environ.get("SKYFIRE_ACK_DRAWDOWN") == "YES":
                        log.warning("hwm_trip_acknowledged",
                                    detail="operator ack — re-arming, HWM reset to current NAV on first read")
                        self.hwm_usd = 0.0
                        self._persist_hwm(tripped=False)
                    else:
                        self.state = BreakerState.LOCKED
                        log.error("boot_locked_from_disk",
                                  detail="-60% HWM trip persisted; set SKYFIRE_ACK_DRAWDOWN=YES to re-arm")
            except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
                self.state = BreakerState.LOCKED
                log.error("hwm_state_unreadable_failing_safe", error=str(e))
        if self._soft_file.exists():
            try:
                self.soft_tier_active = bool(
                    json.loads(self._soft_file.read_text()).get("active", True))
            except (json.JSONDecodeError, ValueError, OSError):
                self.soft_tier_active = True    # unreadable => assume active
        if self._daily_file.exists():
            try:
                d = json.loads(self._daily_file.read_text())
                self._daily_date = d.get("date")
                self._daily_baseline = d.get("baseline")
                pu = d.get("pause_until")
                self.pause_until = datetime.fromisoformat(pu) if pu else None
            except (json.JSONDecodeError, ValueError, OSError) as e:
                # Unreadable daily state: entries off until the next clean roll.
                self.pause_until = datetime.now(timezone.utc) + timedelta(hours=1)
                log.error("daily_state_unreadable_pausing", error=str(e))

    def _persist_hwm(self, tripped: bool) -> None:
        self._write(self._hwm_file, {"hwm_usd": self.hwm_usd, "tripped": tripped})

    def _persist_daily(self) -> None:
        self._write(self._daily_file, {
            "date": self._daily_date, "baseline": self._daily_baseline,
            "pause_until": self.pause_until.isoformat() if self.pause_until else None})

    # -- NAV plausibility quarantine ------------------------------------

    def accept_nav(self, nav_usd: float) -> Optional[float]:
        """Returns the NAV to use, or None while a suspicious reading is
        quarantined. A >X% jump from the last sane reading needs 3
        consecutive consistent confirmations before it moves anything."""
        if nav_usd <= 0:
            return None
        if self._last_sane_nav is None:
            self._last_sane_nav = nav_usd
            return nav_usd
        jump_pct = abs(nav_usd - self._last_sane_nav) / self._last_sane_nav * 100.0
        if jump_pct <= self._cfg.nav_quarantine_jump_pct:
            self._last_sane_nav = nav_usd
            self._suspect_streak = 0
            return nav_usd
        self._suspect_streak += 1
        if self._suspect_streak >= 3:
            log.warning("nav_jump_confirmed", nav=nav_usd, prev=self._last_sane_nav)
            self._last_sane_nav = nav_usd
            self._suspect_streak = 0
            return nav_usd
        log.warning("nav_quarantined", nav=nav_usd, prev=self._last_sane_nav,
                    streak=self._suspect_streak)
        return None

    # -- the ladder ------------------------------------------------------

    def on_nav(self, nav_usd: float, now: Optional[datetime] = None) -> None:
        """Feed one SANE NAV reading (post-quarantine) through every tier."""
        now = now or datetime.now(timezone.utc)
        if self.state is not BreakerState.ARMED:
            return

        # High-water mark
        if nav_usd > self.hwm_usd:
            self.hwm_usd = nav_usd
            self._persist_hwm(tripped=False)

        dd_pct = self.drawdown_pct(nav_usd)

        # Daily baseline: roll at UTC midnight, never re-anchor mid-day.
        today = now.date().isoformat()
        if self._daily_date != today:
            self._daily_date = today
            self._daily_baseline = nav_usd
            self._persist_daily()
        day_dd = 0.0
        if self._daily_baseline and self._daily_baseline > 0:
            day_dd = (self._daily_baseline - nav_usd) / self._daily_baseline * 100.0

        # -60% hard trip
        if dd_pct >= self._cfg.hwm_breaker_pct:
            self.trip(f"drawdown {dd_pct:.1f}% >= {self._cfg.hwm_breaker_pct}% from HWM")
            return

        # -30% soft tier with -20% hysteresis
        if not self.soft_tier_active and dd_pct >= self._cfg.soft_tier_pct:
            self.soft_tier_active = True
            self._write(self._soft_file, {"active": True})
            log.warning("soft_tier_entered", drawdown_pct=round(dd_pct, 2))
        elif self.soft_tier_active and dd_pct <= self._cfg.soft_tier_clear_pct:
            self.soft_tier_active = False
            self._write(self._soft_file, {"active": False})
            log.info("soft_tier_cleared", drawdown_pct=round(dd_pct, 2))

        # -10% daily pause
        if (self.pause_until is None or now >= self.pause_until) \
                and day_dd >= self._cfg.daily_pause_pct:
            self.pause_until = now + timedelta(hours=self._cfg.daily_pause_hours)
            self._persist_daily()
            log.warning("daily_pause_engaged", day_drawdown_pct=round(day_dd, 2),
                        until=self.pause_until.isoformat())

    def drawdown_pct(self, nav_usd: float) -> float:
        if self.hwm_usd <= 0:
            return 0.0
        return max(0.0, (self.hwm_usd - nav_usd) / self.hwm_usd * 100.0)

    def trip(self, reason: str) -> None:
        """Irreversible for the session. Persist FIRST — a flatten failure
        must not lose the trip."""
        if self.state is BreakerState.ARMED:
            self.state = BreakerState.TRIPPED
            self._persist_hwm(tripped=True)
            log.error("breaker_tripped", reason=reason)

    def confirm_flat(self) -> None:
        if self.state is BreakerState.TRIPPED:
            self.state = BreakerState.LOCKED
            log.error("breaker_locked", detail="flat confirmed; manual restart required")

    # -- queries ---------------------------------------------------------

    def entries_allowed(self, now: Optional[datetime] = None) -> tuple[bool, str]:
        now = now or datetime.now(timezone.utc)
        if self.state is not BreakerState.ARMED:
            return False, f"breaker_{self.state.value}"
        if self.pause_until and now < self.pause_until:
            return False, "daily_pause"
        if self.soft_tier_active:
            return False, "soft_tier"
        return True, ""
