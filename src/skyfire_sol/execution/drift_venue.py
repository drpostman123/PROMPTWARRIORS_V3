"""Drift Protocol venue wrapper — SOL-PERP only, dedicated subaccount.

driftpy is an optional extra (pip install .[perps]) with a heavy
anchorpy dependency chain; every driftpy import lives inside this module
and is guarded, so the rest of the system runs without it. The dedicated
subaccount IS the isolation: a liquidation cannot touch the spot wallet
or other sleeves.

NOTE: the driftpy surface below matches driftpy 0.8.x and must be
re-verified against the installed version on first live run (Phase 4 is
flagged perps.enabled=false until then).
"""

from __future__ import annotations

from typing import Optional

from tradecore.logging import get_logger

log = get_logger("drift_venue")

SOL_PERP_MARKET_INDEX = 0


class DriftUnavailable(RuntimeError):
    pass


class DriftVenue:
    def __init__(self, rpc_url: str, keypair, subaccount_id: int) -> None:
        self._rpc_url = rpc_url
        self._keypair = keypair
        self._subaccount_id = subaccount_id
        self._client = None

    async def connect(self) -> None:
        try:
            from anchorpy import Wallet as AnchorWallet
            from driftpy.drift_client import DriftClient
            from solana.rpc.async_api import AsyncClient
        except ImportError as e:
            raise DriftUnavailable(
                "driftpy not installed — pip install '.[perps]'") from e
        conn = AsyncClient(self._rpc_url)
        self._client = DriftClient(
            conn, AnchorWallet(self._keypair),
            active_sub_account_id=self._subaccount_id)
        await self._client.subscribe()
        log.info("drift_connected", subaccount=self._subaccount_id)

    async def funding_rate_pct_hr(self) -> Optional[float]:
        if self._client is None:
            return None
        try:
            market = self._client.get_perp_market_account(SOL_PERP_MARKET_INDEX)
            # amm.last_funding_rate is per funding period (1h on Drift),
            # scaled 1e9; express as % of price per hour.
            rate = market.amm.last_funding_rate / 1e9
            twap = market.amm.historical_oracle_data.last_oracle_price_twap / 1e6
            return rate / twap * 100.0 if twap else None
        except Exception as e:                       # noqa: BLE001 — feed is advisory
            log.warning("funding_rate_read_failed", error=str(e))
            return None

    async def position_notional_usd(self) -> float:
        if self._client is None:
            return 0.0
        try:
            pos = self._client.get_perp_position(SOL_PERP_MARKET_INDEX)
            if pos is None or pos.base_asset_amount == 0:
                return 0.0
            oracle = self._client.get_oracle_price_data_for_perp_market(
                SOL_PERP_MARKET_INDEX)
            return abs(pos.base_asset_amount / 1e9 * oracle.price / 1e6)
        except Exception as e:                       # noqa: BLE001
            log.warning("drift_position_read_failed", error=str(e))
            return 0.0

    async def place_delta_usd(self, delta_usd: float, sol_price_usd: float) -> Optional[str]:
        """Adjust SOL-PERP exposure by delta_usd (signed; + = add long).
        Returns the tx signature, or None on failure."""
        if self._client is None or sol_price_usd <= 0:
            return None
        try:
            from driftpy.types import OrderParams, OrderType, PositionDirection
            base = abs(delta_usd) / sol_price_usd
            params = OrderParams(
                order_type=OrderType.Market(),
                market_index=SOL_PERP_MARKET_INDEX,
                base_asset_amount=int(base * 1e9),
                direction=(PositionDirection.Long() if delta_usd > 0
                           else PositionDirection.Short()),
            )
            sig = await self._client.place_perp_order(params)
            log.info("drift_order", delta_usd=round(delta_usd, 2), sig=str(sig))
            return str(sig)
        except Exception as e:                       # noqa: BLE001 — surfaced to caller
            log.error("drift_order_failed", error=str(e))
            return None
