"""Streamlit dashboard — reads state/snapshot.json written by the runtime.

Run:  streamlit run godmode0dte/dashboard/app.py

Panels: header status row (state, breaker, equity, heat, score), score
component breakdown, macro cluster, open positions, recent transitions,
trade log with cumulative P&L.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

SNAPSHOT = Path("state/snapshot.json")
TRADES = Path("state/trades.jsonl")

# Reference palette (validated): status colors are reserved for state,
# sequential blue carries magnitude, ink/grid tones stay recessive.
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"
BLUE = "#2a78d6"
BLUE_LIGHT = "#9ec5f4"
GOOD = "#0ca30c"
WARNING = "#fab219"
SERIOUS = "#ec835a"
CRITICAL = "#d03b3b"

st.set_page_config(page_title="GodMode0DTE", layout="wide")
st.markdown(
    f"<style>.stMetric label {{color: {INK_2};}}</style>", unsafe_allow_html=True
)


def load_snapshot() -> dict:
    if SNAPSHOT.exists():
        try:
            return json.loads(SNAPSHOT.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def load_trades() -> pd.DataFrame:
    if not TRADES.exists():
        return pd.DataFrame()
    rows = [json.loads(l) for l in TRADES.read_text().splitlines() if l]
    return pd.DataFrame(rows)


snap = load_snapshot()
st.title("GodMode0DTE")
if not snap:
    st.info("Waiting for the runtime to write state/snapshot.json — start it with `godmode`.")
    st.stop()

mode = "PAPER" if snap.get("paper_mode", True) else "LIVE"
breaker = snap.get("breaker", "armed")
breaker_icon = {"armed": "✅", "tripped": "⛔", "locked": "🔒"}.get(breaker, "❓")

# --- status row -------------------------------------------------------
c = st.columns(6)
c[0].metric("Mode / State", f"{mode} · {snap.get('state', '?')}")
c[1].metric(f"Breaker {breaker_icon}", breaker.upper(),
            help=snap.get("breaker_reason") or "daily -6% circuit breaker")
equity = snap.get("equity") or 0.0
start_eq = snap.get("starting_equity") or equity
day_pnl = equity - start_eq
c[2].metric("Equity", f"${equity:,.0f}", delta=f"{day_pnl:+,.0f} today")
heat = snap.get("heat_pct", 0.0)
c[3].metric("Heat", f"{heat:.1f}% / 7%")
score = (snap.get("score") or {}).get("total", 0.0)
c[4].metric("Setup Score", f"{score:.1f} / 100", help="trades only at ≥ 93")
c[5].metric(snap.get("underlying", "SPY"), f"{snap.get('price') or 0:,.2f}")

if breaker != "armed":
    st.error(f"⛔ CIRCUIT BREAKER {breaker.upper()} — {snap.get('breaker_reason', '')}", icon="⛔")

left, right = st.columns([3, 2])

# --- score breakdown --------------------------------------------------
with left:
    st.subheader("Setup Score breakdown")
    comps = (snap.get("score") or {}).get("components", [])
    if comps:
        names = [c_["name"].replace("_", " ") for c_ in comps]
        pts = [c_["points"] for c_ in comps]
        maxes = [c_["max"] for c_ in comps]
        fig = go.Figure()
        fig.add_bar(y=names, x=maxes, orientation="h", marker_color=GRID,
                    hoverinfo="skip", name="max")
        fig.add_bar(y=names, x=pts, orientation="h", marker_color=BLUE,
                    marker_line_width=0,
                    customdata=[c_.get("detail", "") for c_ in comps],
                    hovertemplate="%{y}: %{x:.1f} pts<br>%{customdata}<extra></extra>",
                    name="points")
        fig.update_layout(
            barmode="overlay", showlegend=False, height=320,
            margin=dict(l=0, r=10, t=10, b=10),
            paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
            font=dict(color=INK_2), xaxis=dict(gridcolor=GRID, zerolinecolor=GRID),
            yaxis=dict(autorange="reversed"),
            bargap=0.35,
        )
        st.plotly_chart(fig, use_container_width=True)
        gates = (snap.get("score") or {}).get("gates", [])
        if gates:
            st.warning("Hard gates: " + "; ".join(gates), icon="🚫")
        direction = (snap.get("score") or {}).get("direction")
        if direction:
            st.caption(f"Signal direction: **{direction.upper()}** · regime "
                       f"{snap.get('score', {}).get('regime')} / "
                       f"{snap.get('score', {}).get('vol_regime')} vol")
    else:
        st.caption("No score computed yet this session.")

# --- macro panel ------------------------------------------------------
with right:
    st.subheader("Macro cluster")
    macro = snap.get("macro") or {}
    if macro:
        mc = st.columns(3)
        for i, (k, v) in enumerate(sorted(macro.items())):
            mc[i % 3].metric(k.upper(), f"{v:,.2f}" if v is not None else "—")
    else:
        st.caption("No macro data yet.")

    st.subheader("State transitions")
    trans = snap.get("transitions") or []
    if trans:
        st.dataframe(pd.DataFrame(trans)[["ts", "from", "to", "reason"]],
                     hide_index=True, height=220)
    else:
        st.caption("No transitions logged yet.")

# --- positions --------------------------------------------------------
st.subheader("Open positions")
positions = snap.get("positions") or []
if positions:
    rows = []
    for p in positions:
        v = p.get("vertical", {})
        rows.append({
            "trade": p.get("trade_id"),
            "dir": v.get("direction"),
            "strikes": f"{v.get('long_strike')}/{v.get('short_strike')}",
            "contracts": v.get("contracts"),
            "entry debit": p.get("entry_debit"),
            "value": p.get("current_value"),
            "score": p.get("score_at_entry"),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True)
else:
    st.caption("Flat. (Most days: zero trades — that is the design.)")

# --- trade log --------------------------------------------------------
st.subheader("Trade log")
trades = load_trades()
if not trades.empty:
    exits = trades[trades["kind"] == "exit"].copy()
    if not exits.empty:
        exits["cum_pnl"] = exits["pnl"].cumsum()
        fig2 = go.Figure()
        fig2.add_scatter(x=pd.to_datetime(exits["ts"]), y=exits["cum_pnl"],
                         mode="lines+markers", line=dict(color=BLUE, width=2),
                         marker=dict(size=8),
                         hovertemplate="%{x|%b %d %H:%M}<br>cum P&L $%{y:,.0f}<extra></extra>")
        fig2.update_layout(height=260, margin=dict(l=0, r=10, t=10, b=10),
                           paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
                           font=dict(color=INK_2),
                           xaxis=dict(gridcolor=GRID), yaxis=dict(gridcolor=GRID,
                           title="cumulative P&L ($)"))
        st.plotly_chart(fig2, use_container_width=True)
    st.dataframe(trades.tail(50), hide_index=True)
else:
    st.caption("No trades logged yet.")

st.caption("Auto-refresh: use the ⋮ menu → rerun, or run with "
           "`streamlit run ... --server.runOnSave true`; the runtime rewrites the "
           "snapshot every 2 s.")
