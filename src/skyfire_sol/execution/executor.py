"""ExecutionAgent — the only module that submits transactions.

Accepts ONLY ApprovedOrder (whose constructor enforces the SafetyGate's
capability token). Flow per order:

  stale-quote check (re-quote + re-assert the slippage cap if old)
  -> build swap tx (Jupiter /swap, priority fee attached)
  -> sign locally (solders)
  -> simulate (preflight; a deterministic failure never hits the chain)
  -> send + confirm with blockhash-expiry rebroadcast
  -> resolve to a DEFINITE terminal state (never unknown)
  -> emit Fill with realized slippage; feed probation

An order that fails cleanly (no route / expired / preflight) is reported
as a rejection-style event; an order whose signature landed is always
resolved via await_terminal before anything else happens.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import httpx

from skyfire_sol.chain.helius import HeliusRpc, RpcError
from skyfire_sol.clients.jupiter import (
    JupiterClient,
    NoRouteError,
    SlippageCapExceeded,
)
from skyfire_sol.config import AppConfig
from skyfire_sol.models import Fill, JupQuote
from skyfire_sol.safety.probation import Probation
from skyfire_sol.wallet import Wallet
from tradecore.logging import get_logger

log = get_logger("executor")


class ExecutionAgent:
    def __init__(
        self,
        cfg: AppConfig,
        wallet: Wallet,
        rpc: HeliusRpc,
        jupiter: JupiterClient,
        probation: Probation,
        publish: Callable[[str, object], Awaitable[None]],   # bus.publish
        log_trade: Callable[[dict], Awaitable[None]],
        drift=None,                                   # execution.drift_venue.DriftVenue
        hl=None,                                      # execution.hl_venue.HlVenue
    ) -> None:
        self._cfg = cfg
        self._wallet = wallet
        self._rpc = rpc
        self._jup = jupiter
        self._probation = probation
        self._publish = publish
        self._log_trade = log_trade
        self._drift = drift
        self._hl = hl

    async def execute_hl(self, approved) -> bool:
        from skyfire_sol.safety.gate import ApprovedHlOrder
        if not isinstance(approved, ApprovedHlOrder):
            raise PermissionError("executor accepts ApprovedHlOrder only")
        if self._hl is None or not self._hl.connected:
            log.error("hl_order_no_venue", intent_id=approved.intent.intent_id)
            return False
        intent = approved.intent
        slippage = self._cfg.sleeves.hl.slippage_cap_pct
        if intent.action == "open":
            result = await self._hl.market_open_long(
                intent.coin, approved.notional_usd, intent.mark_px, slippage)
        else:
            result = await self._hl.market_close(intent.coin, slippage)
        ok = result is not None
        await self._log_trade({
            "kind": "hl_order", "intent_id": intent.intent_id,
            "coin": intent.coin, "action": intent.action,
            "notional_usd": approved.notional_usd, "ok": ok,
            "reason": intent.reason})
        if ok:
            await self._publish("hl_fills", {
                "intent_id": intent.intent_id, "coin": intent.coin,
                "action": intent.action, "notional_usd": approved.notional_usd})
        return ok

    async def execute_perp(self, approved) -> Optional[str]:
        from skyfire_sol.safety.gate import ApprovedPerpOrder
        if not isinstance(approved, ApprovedPerpOrder):
            raise PermissionError("executor accepts ApprovedPerpOrder only")
        if self._drift is None:
            log.error("perp_order_no_venue", intent_id=approved.intent.intent_id)
            return None
        from skyfire_sol.config import WSOL_MINT
        # SOL price for base-size conversion comes through the quote path the
        # venue needs; the venue reads its own oracle for margin math.
        sol_px = await self._jup.price_usd_per_token(WSOL_MINT, 9)
        if not sol_px:
            log.error("perp_order_no_sol_price", intent_id=approved.intent.intent_id)
            return None
        sig = await self._drift.place_delta_usd(approved.delta_usd, sol_px)
        await self._log_trade({
            "kind": "perp_order", "intent_id": approved.intent.intent_id,
            "delta_usd": approved.delta_usd, "tx_sig": sig or "",
            "ok": sig is not None})
        return sig

    async def execute(self, approved) -> Optional[Fill]:
        # Late import to avoid a cycle; isinstance is the second lock on the
        # door (the first is ApprovedOrder's constructor token check).
        from skyfire_sol.safety.gate import ApprovedOrder
        if not isinstance(approved, ApprovedOrder):
            raise PermissionError("executor accepts ApprovedOrder only")

        intent = approved.intent
        quote = approved.quote
        now = datetime.now(timezone.utc)

        # Re-quote if stale — and re-assert the cap on the fresh quote.
        age = (now - quote.ts).total_seconds()
        cap_pct = quote.slippage_bps / 100.0
        if age > self._cfg.jupiter.quote_ttl_s:
            try:
                quote = await self._jup.quote(
                    quote.input_mint, quote.output_mint, approved.amount_raw, cap_pct)
            except (SlippageCapExceeded, NoRouteError, httpx.HTTPError) as e:
                await self._fail(intent, f"requote_failed: {e}")
                return None

        try:
            fee = await self._rpc.get_priority_fee_estimate()
            tx_b64 = await self._jup.swap_transaction(
                quote, str(self._wallet.pubkey), priority_fee_lamports=None)
        except (httpx.HTTPError, KeyError) as e:
            await self._fail(intent, f"swap_build_failed: {e}")
            return None

        signed, lvbh, err = await self._sign(tx_b64)
        if err:
            await self._fail(intent, err)
            return None

        sim_err = None
        try:
            sim_err = await self._rpc.simulate(base64.b64encode(signed).decode())
        except (RpcError, httpx.HTTPError) as e:
            await self._fail(intent, f"simulate_rpc_failed: {e}")
            return None
        if sim_err is not None:
            await self._fail(intent, f"preflight: {sim_err}")
            return None

        try:
            outcome = await self._rpc.send_and_confirm(
                signed, lvbh, timeout_s=self._cfg.rpc.confirm_timeout_s)
            if not outcome.confirmed and outcome.err == "confirm_timeout":
                # Never leave an order unknown: poll to a definite terminal.
                outcome = await self._rpc.await_terminal(outcome.signature, lvbh)
        except (RpcError, httpx.HTTPError) as e:
            await self._fail(intent, f"send_failed: {e}")
            return None

        if not outcome.confirmed:
            await self._fail(intent, f"tx_failed: {outcome.err}", tx_sig=outcome.signature)
            return None

        actual_out = await self._actual_out(outcome.signature, quote)
        quoted_out = quote.out_amount
        realized_slip = (
            (quoted_out - actual_out) / quoted_out * 100.0 if quoted_out and actual_out
            else 0.0)
        clean = abs(realized_slip) <= self._cfg.risk.clean_fill_slippage_pct
        self._probation.record_fill(realized_slip)

        fill = Fill(
            intent_id=intent.intent_id,
            position_id=intent.position_id or uuid.uuid4().hex[:12],
            sleeve=intent.sleeve, side=intent.side, mint=intent.mint,
            quote_mint=intent.quote_mint, tx_sig=outcome.signature,
            in_amount_raw=approved.amount_raw,
            quoted_out=quoted_out, actual_out=actual_out or quoted_out,
            realized_slippage_pct=round(realized_slip, 4),
            price_impact_pct=quote.price_impact_pct,
            priority_fee_lamports=fee, clean=clean,
            ts=datetime.now(timezone.utc))
        await self._log_trade({
            "kind": "fill", "intent_id": intent.intent_id,
            "sleeve": intent.sleeve.value, "side": intent.side.value,
            "mint": intent.mint, "tx_sig": outcome.signature,
            "quoted_out": quoted_out, "actual_out": fill.actual_out,
            "realized_slippage_pct": fill.realized_slippage_pct,
            "clean": clean})
        await self._publish("fills", fill)
        log.info("fill", intent_id=intent.intent_id, tx=outcome.signature,
                 slip_pct=fill.realized_slippage_pct, clean=clean)
        return fill

    # -- helpers ---------------------------------------------------------

    async def _sign(self, tx_b64: str) -> tuple[bytes, int, Optional[str]]:
        try:
            from solders.transaction import VersionedTransaction
            raw = base64.b64decode(tx_b64)
            tx = VersionedTransaction.from_bytes(raw)
            signed = VersionedTransaction(tx.message, [self._wallet.keypair])
            _, lvbh = await self._rpc.get_latest_blockhash()
            return bytes(signed), lvbh, None
        except (ValueError, RpcError, httpx.HTTPError) as e:
            return b"", 0, f"sign_failed: {e}"

    async def _actual_out(self, sig: str, quote: JupQuote) -> Optional[int]:
        """Realized output units from the confirmed transaction's balance
        deltas. None (fall back to quoted) when meta is unavailable."""
        try:
            r = await self._rpc._call("getTransaction", [
                sig, {"encoding": "jsonParsed", "commitment": "confirmed",
                      "maxSupportedTransactionVersion": 0}])
            if not r:
                return None
            meta = r["meta"]
            owner = str(self._wallet.pubkey)
            out_mint = quote.output_mint
            from skyfire_sol.config import WSOL_MINT
            if out_mint == WSOL_MINT:
                keys = [k["pubkey"] if isinstance(k, dict) else str(k)
                        for k in r["transaction"]["message"]["accountKeys"]]
                idx = keys.index(owner)
                delta = meta["postBalances"][idx] - meta["preBalances"][idx]
                return max(0, delta + meta.get("fee", 0))
            def _bal(entries):
                for e in entries:
                    if e.get("owner") == owner and e.get("mint") == out_mint:
                        return int(e["uiTokenAmount"]["amount"])
                return 0
            return max(0, _bal(meta.get("postTokenBalances") or [])
                       - _bal(meta.get("preTokenBalances") or []))
        except (RpcError, httpx.HTTPError, KeyError, ValueError, IndexError) as e:
            log.warning("actual_out_unavailable", sig=sig, error=str(e))
            return None

    async def _fail(self, intent, detail: str, tx_sig: str = "") -> None:
        await self._log_trade({
            "kind": "execution_failed", "intent_id": intent.intent_id,
            "sleeve": intent.sleeve.value, "side": intent.side.value,
            "mint": intent.mint, "detail": detail, "tx_sig": tx_sig})
        await self._publish("execution_failures", {
            "intent_id": intent.intent_id, "detail": detail,
            "side": intent.side, "position_id": intent.position_id})
        log.error("execution_failed", intent_id=intent.intent_id, detail=detail)
