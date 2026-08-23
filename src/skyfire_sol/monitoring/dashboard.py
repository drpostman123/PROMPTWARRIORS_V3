"""Rich terminal dashboard — reads the blackboard, renders a live view.
Pure observer: no handles to anything that can act."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from skyfire_sol.blackboard import Blackboard


def render(bb: Blackboard) -> Group:
    snap = bb.snapshot()
    now = datetime.now(timezone.utc)
    age = (now - snap.ts).total_seconds()

    header = Table.grid(expand=True)
    header.add_column(justify="left")
    header.add_column(justify="right")
    stale = age > 30
    status = Text(
        f"NAV ${snap.nav_usd:,.2f}   regime {snap.regime.state.value}   "
        f"breaker {snap.safety.breaker}   dd {snap.safety.drawdown_pct:.1f}%",
        style="bold red" if stale or snap.safety.breaker != "armed" else "bold green")
    badges = []
    if stale:
        badges.append("STALE")
    if snap.safety.kill:
        badges.append("KILL")
    if snap.safety.probation:
        badges.append(f"PROBATION {snap.safety.clean_fills} clean "
                      "(manual lift: MCP go_full_size)")
    if snap.safety.soft_tier_active:
        badges.append("SOFT-TIER")
    if snap.safety.daily_pause_until:
        badges.append("DAILY-PAUSE")
    header.add_row(status, Text("  ".join(badges), style="bold yellow"))

    sleeves = Table(title="Sleeves", expand=True)
    for col in ("sleeve", "nav $", "current %", "target %"):
        sleeves.add_column(col)
    for name, nav in sorted(snap.sleeve_navs.items()):
        cur = snap.allocations_current.get(name, 0.0)
        tgt = snap.allocations_target.get(name, 0.0)
        sleeves.add_row(name, f"{nav:,.2f}", f"{cur:.1f}", f"{tgt:.1f}")

    pos = Table(title="Positions", expand=True)
    for col in ("id", "sleeve", "symbol", "qty", "entry $", "mark $", "pnl %", "peak dd %"):
        pos.add_column(col)
    for p in snap.positions:
        qty = p.qty_raw / 10 ** p.decimals
        mark = p.last_mark_usd or p.entry_price_usd
        pnl = (mark / p.entry_price_usd - 1) * 100 if p.entry_price_usd else 0.0
        peak_dd = ((p.peak_price_usd - mark) / p.peak_price_usd * 100
                   if p.peak_price_usd else 0.0)
        pos.add_row(p.position_id[:8], p.sleeve.value, p.symbol, f"{qty:,.0f}",
                    f"{p.entry_price_usd:.6g}", f"{mark:.6g}",
                    f"{pnl:+.1f}", f"{peak_dd:.1f}")

    return Group(Panel(header), sleeves, pos)
