"""The Goodhart guard, pinned three ways:

1. Capability-token forgery raises (ApprovedOrder / ApprovedPerpOrder).
2. The executor rejects anything that is not an ApprovedOrder.
3. ceo/ imports nothing from safety/, execution/, wallet, chain/,
   clients/ (AST walk), and the sole sendTransaction call site lives in
   chain/helius.py with only the executor calling send.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import skyfire_sol
from skyfire_sol.config import USDC_MINT
from skyfire_sol.models import (
    HlIntent,
    IntentKind,
    JupQuote,
    PerpIntent,
    Side,
    SleeveId,
    TradeIntent,
    Urgency,
)
from skyfire_sol.safety.gate import ApprovedHlOrder, ApprovedOrder, ApprovedPerpOrder

NOW = datetime.now(timezone.utc)
PKG_ROOT = Path(skyfire_sol.__file__).parent


def _intent() -> TradeIntent:
    return TradeIntent("i1", SleeveId.MEME_ROTATION, IntentKind.ENTRY, Side.BUY,
                       "M", USDC_MINT, 100.0, Urgency.NORMAL, "t", NOW)


def _quote() -> JupQuote:
    return JupQuote("a", "b", 1, 1, 0.1, 300, "{}", NOW)


def test_approved_order_forgery_raises():
    with pytest.raises(PermissionError):
        ApprovedOrder(intent=_intent(), quote=_quote(), size_usd=100.0,
                      amount_raw=1, gate_trace="[]", approved_ts=NOW)
    with pytest.raises(PermissionError):
        ApprovedOrder(intent=_intent(), quote=_quote(), size_usd=100.0,
                      amount_raw=1, gate_trace="[]", approved_ts=NOW,
                      _token=object())               # wrong token


def test_approved_perp_order_forgery_raises():
    pi = PerpIntent("p1", 100.0, 0.0, "t", NOW)
    with pytest.raises(PermissionError):
        ApprovedPerpOrder(intent=pi, delta_usd=100.0, gate_trace="[]",
                          approved_ts=NOW)


def test_approved_hl_order_forgery_raises():
    hi = HlIntent("h1", "PUMP", "open", 100.0, 0.0, 0.005, "t", NOW)
    with pytest.raises(PermissionError):
        ApprovedHlOrder(intent=hi, notional_usd=100.0, gate_trace="[]",
                        approved_ts=NOW)
    with pytest.raises(PermissionError):
        ApprovedHlOrder(intent=hi, notional_usd=100.0, gate_trace="[]",
                        approved_ts=NOW, _token=object())


def test_gate_minted_order_constructs(tmp_path):
    # the only legitimate mint site works (via the module-private token)
    from skyfire_sol.safety import gate as gate_mod
    order = ApprovedOrder(intent=_intent(), quote=_quote(), size_usd=1.0,
                          amount_raw=1, gate_trace="[]", approved_ts=NOW,
                          _token=gate_mod._GATE_TOKEN)
    assert order.size_usd == 1.0


async def test_executor_rejects_non_approved():
    from skyfire_sol.execution.executor import ExecutionAgent
    ex = ExecutionAgent.__new__(ExecutionAgent)     # no deps needed to hit the check
    with pytest.raises(PermissionError):
        await ex.execute(_intent())                  # a bare intent is not approval
    with pytest.raises(PermissionError):
        await ex.execute_perp(_intent())
    with pytest.raises(PermissionError):
        await ex.execute_hl(_intent())


FORBIDDEN_FOR_CEO = ("skyfire_sol.safety", "skyfire_sol.execution",
                     "skyfire_sol.wallet", "skyfire_sol.chain",
                     "skyfire_sol.clients")


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_ceo_import_hygiene():
    for py in (PKG_ROOT / "ceo").rglob("*.py"):
        for imp in _imports_of(py):
            assert not imp.startswith(FORBIDDEN_FOR_CEO), \
                f"{py.name} imports {imp} — the CEO must not reach the action layer"


def test_sleeves_never_import_executor_or_wallet():
    for py in (PKG_ROOT / "sleeves").rglob("*.py"):
        for imp in _imports_of(py):
            assert not imp.startswith(("skyfire_sol.execution", "skyfire_sol.wallet")), \
                f"{py.name} imports {imp}"


def test_single_send_transaction_call_site():
    offenders = []
    for py in PKG_ROOT.rglob("*.py"):
        if "sendTransaction" in py.read_text() and py.name != "helius.py":
            offenders.append(str(py))
    assert not offenders, f"sendTransaction outside chain/helius.py: {offenders}"


def test_send_and_confirm_called_only_by_executor():
    offenders = []
    for py in PKG_ROOT.rglob("*.py"):
        text = py.read_text()
        if py.name in ("helius.py",) or py.parent.name == "execution":
            continue
        if ".send_and_confirm(" in text or ".send_raw(" in text:
            offenders.append(str(py))
    assert not offenders, f"raw submit outside execution/: {offenders}"


def test_hl_orders_placed_only_by_execution_layer():
    """market_open/market_close (the HL submit calls) live only under
    execution/ — sleeves read via the venue but cannot place orders."""
    offenders = []
    for py in PKG_ROOT.rglob("*.py"):
        if py.parent.name == "execution":
            continue
        text = py.read_text()
        if ".market_open" in text or ".market_close(" in text:
            offenders.append(str(py))
    assert not offenders, f"HL order calls outside execution/: {offenders}"


def test_gate_trace_is_json():
    from skyfire_sol.safety import gate as gate_mod
    order = ApprovedOrder(intent=_intent(), quote=_quote(), size_usd=1.0,
                          amount_raw=1, gate_trace=json.dumps([{"check": "S0"}]),
                          approved_ts=NOW, _token=gate_mod._GATE_TOKEN)
    assert json.loads(order.gate_trace)[0]["check"] == "S0"
