"""Solana JSON-RPC client (Helius primary, public RPC fallback).

Covers exactly what the system needs: blockhash, send + confirm with
lastValidBlockHeight expiry and bounded rebroadcast, simulate, account
reads for the rug filter (mint/freeze authorities, largest accounts),
balances, and Helius' priority-fee estimate.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from skyfire_sol.clients.ratelimit import TokenBucket
from tradecore.logging import get_logger

log = get_logger("helius")

@dataclass(frozen=True)
class SendOutcome:
    signature: str
    confirmed: bool
    err: Optional[str]
    slot: Optional[int] = None


class RpcError(RuntimeError):
    pass


class HeliusRpc:
    def __init__(self, url: str, client: httpx.AsyncClient,
                 rps: float = 8.0, commitment: str = "confirmed") -> None:
        self._url = url
        self._http = client
        self._bucket = TokenBucket(rps)
        self._commitment = commitment
        self._id = 0

    async def _call(self, method: str, params: list[Any]) -> Any:
        await self._bucket.acquire()
        self._id += 1
        resp = await self._http.post(self._url, json={
            "jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RpcError(f"{method}: {body['error']}")
        return body["result"]

    # -- reads ----------------------------------------------------------

    async def get_latest_blockhash(self) -> tuple[str, int]:
        r = await self._call("getLatestBlockhash", [{"commitment": self._commitment}])
        v = r["value"]
        return v["blockhash"], v["lastValidBlockHeight"]

    async def get_block_height(self) -> int:
        return await self._call("getBlockHeight", [{"commitment": self._commitment}])

    async def get_sol_balance(self, pubkey: str) -> int:
        r = await self._call("getBalance", [pubkey, {"commitment": self._commitment}])
        return r["value"]

    async def get_token_accounts(self, owner: str) -> dict[str, int]:
        """mint -> raw amount for all SPL token accounts of ``owner``."""
        r = await self._call("getTokenAccountsByOwner", [
            owner,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed", "commitment": self._commitment},
        ])
        out: dict[str, int] = {}
        for acc in r["value"]:
            info = acc["account"]["data"]["parsed"]["info"]
            out[info["mint"]] = out.get(info["mint"], 0) + int(
                info["tokenAmount"]["amount"])
        return out

    async def get_mint_authorities(self, mint: str) -> tuple[Optional[bool], Optional[bool]]:
        """(mint_authority_revoked, freeze_authority_revoked); None on fetch failure."""
        try:
            r = await self._call("getAccountInfo", [
                mint, {"encoding": "jsonParsed", "commitment": self._commitment}])
            v = r.get("value")
            if not v:
                return None, None
            info = v["data"]["parsed"]["info"]
            return info.get("mintAuthority") is None, info.get("freezeAuthority") is None
        except (RpcError, httpx.HTTPError, KeyError, TypeError) as e:
            log.warning("mint_authority_fetch_failed", mint=mint, error=str(e))
            return None, None

    async def get_largest_token_holders(self, mint: str) -> Optional[list[tuple[str, int]]]:
        try:
            r = await self._call("getTokenLargestAccounts",
                                 [mint, {"commitment": self._commitment}])
            return [(a["address"], int(a["amount"])) for a in r["value"]]
        except (RpcError, httpx.HTTPError, KeyError) as e:
            log.warning("largest_holders_fetch_failed", mint=mint, error=str(e))
            return None

    async def get_token_supply(self, mint: str) -> Optional[tuple[int, int]]:
        """(raw supply, decimals); None on failure."""
        try:
            r = await self._call("getTokenSupply", [mint, {"commitment": self._commitment}])
            return int(r["value"]["amount"]), int(r["value"]["decimals"])
        except (RpcError, httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("token_supply_fetch_failed", mint=mint, error=str(e))
            return None

    async def get_priority_fee_estimate(self, tx_b64: Optional[str] = None) -> int:
        """Micro-lamports per CU. Helius-only method; sane default elsewhere."""
        try:
            params: list[Any] = [{"options": {"priorityLevel": "High"}}]
            if tx_b64:
                params[0]["transaction"] = tx_b64
            r = await self._call("getPriorityFeeEstimate", params)
            return int(r["priorityFeeEstimate"])
        except (RpcError, httpx.HTTPError, KeyError, ValueError):
            return 50_000  # conservative default, micro-lamports/CU

    # -- writes ----------------------------------------------------------

    async def simulate(self, tx_b64: str) -> Optional[str]:
        """Returns None if simulation succeeds, else the error string."""
        r = await self._call("simulateTransaction", [
            tx_b64, {"encoding": "base64", "commitment": self._commitment,
                     "replaceRecentBlockhash": True}])
        err = r["value"].get("err")
        return None if err is None else json.dumps(err)

    async def send_raw(self, tx_b64: str, skip_preflight: bool = False) -> str:
        return await self._call("sendTransaction", [
            tx_b64, {"encoding": "base64", "skipPreflight": skip_preflight,
                     "maxRetries": 0}])

    async def await_terminal(self, sig: str, last_valid_block_height: int,
                             poll_s: float = 2.0) -> SendOutcome:
        """Poll an already-submitted signature until a DEFINITE outcome:
        confirmed, failed, or blockhash expired. Used to resolve a
        confirm_timeout — an order is never left in unknown state."""
        while True:
            statuses = await self._call("getSignatureStatuses",
                                        [[sig], {"searchTransactionHistory": True}])
            st = statuses["value"][0]
            if st is not None:
                if st.get("err") is not None:
                    return SendOutcome(sig, False, json.dumps(st["err"]), st.get("slot"))
                if st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return SendOutcome(sig, True, None, st.get("slot"))
            height = await self.get_block_height()
            if height > last_valid_block_height and st is None:
                return SendOutcome(sig, False, "blockhash_expired")
            await asyncio.sleep(poll_s)

    async def send_and_confirm(
        self, signed_tx: bytes, last_valid_block_height: int,
        timeout_s: float = 60.0, rebroadcast_every_s: float = 2.0,
    ) -> SendOutcome:
        """Submit, then poll status until confirmed or the blockhash expires.
        Rebroadcasts the same signed bytes (idempotent — same signature) so a
        dropped packet can't strand the order in unknown state. The terminal
        answer is always definite: confirmed, failed with error, or expired."""
        tx_b64 = base64.b64encode(signed_tx).decode()
        sig = await self.send_raw(tx_b64)
        deadline = asyncio.get_event_loop().time() + timeout_s
        while True:
            statuses = await self._call("getSignatureStatuses", [[sig]])
            st = statuses["value"][0]
            if st is not None:
                if st.get("err") is not None:
                    return SendOutcome(sig, False, json.dumps(st["err"]), st.get("slot"))
                if st.get("confirmationStatus") in ("confirmed", "finalized"):
                    return SendOutcome(sig, True, None, st.get("slot"))
            height = await self.get_block_height()
            if height > last_valid_block_height:
                # Blockhash expired: the tx can never land. Definite failure.
                return SendOutcome(sig, False, "blockhash_expired")
            if asyncio.get_event_loop().time() > deadline:
                return SendOutcome(sig, False, "confirm_timeout")
            try:
                await self.send_raw(tx_b64, skip_preflight=True)   # rebroadcast
            except (RpcError, httpx.HTTPError):
                pass                                # node already has it — fine
            await asyncio.sleep(rebroadcast_every_s)
