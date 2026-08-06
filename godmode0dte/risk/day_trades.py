"""Day-trade budget: an OPT-IN guard for brokers still enforcing PDT-style
day-trade limits during the FINRA phase-in.

Regulatory status (verified August 2026): the SEC approved eliminating the
FINRA Pattern Day Trader rule on 2026-04-14; the amendments became effective
2026-06-04, replacing the $25k/3-in-5 regime with intraday-margin-exposure
monitoring. HOWEVER the change carries an 18-month implementation phase-in
(through October 2027) and enforcement timing varies by broker — some firms
still apply day-trade counters. Until your broker confirms the new framework
applies to YOUR account (VERIFY-LIVE), ``account_type: margin_small`` keeps
the classic 3-per-5-business-days budget as a self-imposed guard.

Every trade this system takes is a same-day round trip (0DTE, force-flat) —
a day trade by definition. The budget is enforced BEFORE the broker can
restrict us and persisted to disk so a restart cannot forget a used trade.

account_type:
  margin_large — post-PDT default / >=$25k: no self-imposed budget.
  margin_small — broker still enforcing a day-trade counter: budget on.
  cash         — no day-trade counter, but T+1 settlement means same-day
                 reuse of sale proceeds risks good-faith violations; caveat
                 logged once per session.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from godmode0dte.config import RiskConfig
from godmode0dte.monitoring.logging import get_logger

log = get_logger("day_trades")


def _business_window(end: date, n: int) -> set[date]:
    days: set[date] = set()
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.add(d)
        d -= timedelta(days=1)
    return days


class DayTradeBudget:
    """Rolling N-in-5-business-days day-trade ledger, persisted on disk.

    Also serves as the persistence for TODAY'S approval count (spec §2.3
    daily trade cap) regardless of account type — approvals are recorded
    for every account type; the rolling-window *budget* only gates
    ``margin_small``.
    """

    def __init__(self, cfg: RiskConfig, session_date: date) -> None:
        self._cfg = cfg
        self._session_date = session_date
        self._path = Path(cfg.day_trade_file)
        self._trades: list[date] = []
        self._caveat_logged = False
        self._restore()

    def _restore(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text())
            self._trades = [date.fromisoformat(d) for d in payload.get("day_trades", [])]
        except (json.JSONDecodeError, ValueError, OSError):
            # Fail SAFE: an unreadable ledger counts as a full window.
            log.error("day_trade_ledger_unreadable_failsafe")
            self._trades = [self._session_date] * max(self._cfg.day_trades_per_5d,
                                                      self._cfg.daily_trade_cap)

    def used_in_window(self) -> int:
        window = _business_window(self._session_date, 5)
        return sum(1 for d in self._trades if d in window)

    def used_today(self) -> int:
        return sum(1 for d in self._trades if d == self._session_date)

    def allows(self) -> bool:
        if self._cfg.account_type == "margin_large":
            return True
        if self._cfg.account_type == "cash":
            if not self._caveat_logged:
                log.warning("cash_account_gfv_caveat",
                            detail="no day-trade counter, but T+1 settlement: same-day "
                                   "reuse of sale proceeds risks good-faith violations")
                self._caveat_logged = True
            return True
        return self.used_in_window() < self._cfg.day_trades_per_5d

    def record(self) -> None:
        """Record an approval (consumed at ENTRY — an open 0DTE will close
        today; overcounting is the safe direction). Recorded for every
        account type so the daily cap survives a mid-day restart."""
        self._trades.append(self._session_date)
        keep = _business_window(self._session_date, 10)
        self._trades = [d for d in self._trades if d in keep]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(
                {"day_trades": [d.isoformat() for d in sorted(self._trades)]}))
        except OSError as e:
            log.error("day_trade_ledger_persist_failed", error=str(e))
        log.info("day_trade_recorded", used_window=self.used_in_window(),
                 used_today=self.used_today())
