"""Discovery scanner: 60s poll over DexScreener (profiles + boosts),
assembling TokenFacts from DexScreener + Helius (on-chain authorities,
holder concentration) + RugCheck (LP lock, deployer) + Birdeye (holder
counts) + Jupiter (sell-test), then running the rug hard gate.

Outputs:
- rug-clean candidates with entry signals -> bus topic "meme_signals"
- every reject -> phantom log (the training-data flywheel)
- honeypots (sell-test failures) -> persisted blacklist

The scanner holds no wallet and no executor: it can look, not trade.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from skyfire_sol.chain.helius import HeliusRpc
from skyfire_sol.clients.birdeye import BirdeyeClient
from skyfire_sol.clients.dexscreener import DexScreenerClient
from skyfire_sol.clients.jupiter import JupiterClient
from skyfire_sol.clients.rugcheck import RugCheckClient
from skyfire_sol.config import MAJOR_MINTS, AppConfig
from skyfire_sol.discovery import rugfilter
from skyfire_sol.meme import entry as entry_engine
from skyfire_sol.models import PhantomCandidate, RugVerdict, TokenFacts
from skyfire_sol.persistence.phantom import PhantomLog, features_of
from tradecore.logging import get_logger

log = get_logger("scanner")

RESCAN_COOLDOWN = timedelta(hours=6)


class Blacklist:
    """Persisted honeypot blacklist. Once in, never traded again."""

    def __init__(self, state_dir: str) -> None:
        self._file = Path(state_dir) / "blacklist.json"
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._mints: set[str] = set()
        if self._file.exists():
            try:
                self._mints = set(json.loads(self._file.read_text()))
            except (json.JSONDecodeError, OSError, TypeError):
                log.error("blacklist_unreadable_starting_empty")

    def __contains__(self, mint: str) -> bool:
        return mint in self._mints

    def add(self, mint: str) -> None:
        self._mints.add(mint)
        try:
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text(json.dumps(sorted(self._mints)))
            tmp.replace(self._file)
        except OSError as e:
            log.error("blacklist_persist_failed", error=str(e))


class Scanner:
    def __init__(
        self,
        cfg: AppConfig,
        dex: DexScreenerClient,
        rug: RugCheckClient,
        birdeye: BirdeyeClient,
        jup: JupiterClient,
        rpc: HeliusRpc,
        phantom: PhantomLog,
        blacklist: Blacklist,
        publish: Callable[[str, object], Awaitable[None]],
        policy_store=None,                    # learning.policy.PolicyStore
    ) -> None:
        self._cfg = cfg
        self._dex = dex
        self._rug = rug
        self._birdeye = birdeye
        self._jup = jup
        self._rpc = rpc
        self._phantom = phantom
        self._blacklist = blacklist
        self._publish = publish
        self._policy_store = policy_store

        self._seen: dict[str, datetime] = {}
        self._holder_history: dict[str, int] = {}
        self._verdicts: dict[str, RugVerdict] = {}
        self._decimals: dict[str, int] = {}
        self.last_breadth: float = 0.0            # share w/ positive 1h momo
        self.last_agg_volume: float = 0.0

    # Gate + CEO read these.
    def rug_verdict_for(self, mint: str) -> Optional[RugVerdict]:
        return self._verdicts.get(mint)

    def is_blacklisted(self, mint: str) -> bool:
        return mint in self._blacklist

    async def decimals_for(self, mint: str) -> int:
        if mint not in self._decimals:
            info = await self._rpc.get_token_supply(mint)
            self._decimals[mint] = info[1] if info else 9
        return self._decimals[mint]

    async def run(self) -> None:
        while True:
            try:
                await self.scan_once()
            except Exception as e:                  # noqa: BLE001 — scanner never dies
                log.error("scan_cycle_failed", error=str(e))
            await asyncio.sleep(self._cfg.discovery.poll_seconds)

    async def scan_once(self) -> None:
        now = datetime.now(timezone.utc)
        profiles, boosts = await asyncio.gather(
            self._dex.latest_solana_profiles(), self._dex.boosted_solana_tokens())
        mints: list[str] = []
        for m in profiles + boosts:
            if m in mints or m in MAJOR_MINTS or m in self._blacklist:
                continue
            last = self._seen.get(m)
            if last and now - last < RESCAN_COOLDOWN:
                continue
            mints.append(m)
        mints = mints[: self._cfg.discovery.max_candidates_per_cycle]
        log.info("scan_cycle", candidates=len(mints))

        momo_up = 0
        momo_total = 0
        agg_vol = 0.0
        signals = []
        for mint in mints:
            self._seen[mint] = now
            facts = await self._facts(mint)
            if facts is None:
                continue
            if facts.volume_1h_usd is not None:
                agg_vol += facts.volume_1h_usd
            if facts.price_change_1h_pct is not None:
                momo_total += 1
                momo_up += 1 if facts.price_change_1h_pct > 0 else 0

            verdict = rugfilter.evaluate(facts, self._cfg.discovery)
            self._verdicts[mint] = verdict
            if not verdict.passed:
                if verdict.first_failure == "sell_route_exists" \
                        and facts.sell_route_exists is False:
                    self._blacklist.add(mint)
                    log.warning("honeypot_blacklisted", mint=mint)
                await self._phantom.record(PhantomCandidate(
                    mint=mint, symbol=facts.symbol, stage="rug_reject",
                    reject_reason=verdict.first_failure or "unknown",
                    features=features_of(facts), price_usd=facts.price_usd, ts=now))
                continue

            policy = self._policy_store.current if self._policy_store else None
            sig = entry_engine.score(facts, self._cfg.sleeves.meme, policy=policy)
            if sig is None:
                await self._phantom.record(PhantomCandidate(
                    mint=mint, symbol=facts.symbol, stage="entry_reject",
                    reject_reason="momentum_criteria", features=features_of(facts),
                    price_usd=facts.price_usd, ts=now))
                continue
            signals.append((sig, facts))

        if momo_total:
            self.last_breadth = momo_up / momo_total
        self.last_agg_volume = agg_vol

        if signals:
            await self._publish("meme_signals",
                                [(s, features_of(f)) for s, f in signals])

    async def _facts(self, mint: str) -> Optional[TokenFacts]:
        stats = await self._dex.token_stats(mint)
        if stats is None or stats.get("mint") != mint:
            return None

        mint_rev, freeze_rev = await self._rpc.get_mint_authorities(mint)
        report = await self._rug.report(mint)
        rc = self._rug.extract(report) if report else {}

        top10 = await self._top10_ex_lp(mint)

        holders = await self._birdeye.holder_count(mint) if self._birdeye.enabled else None
        prev_holders = self._holder_history.get(mint)
        if holders is not None:
            self._holder_history[mint] = holders

        sell_ok: Optional[bool] = None
        price = stats.get("price_usd")
        if price and price > 0:
            decimals = await self.decimals_for(mint)
            amount_raw = max(1, int(self._cfg.discovery.sell_test_usd / price
                                    * 10 ** decimals))
            sell_ok = await self._jup.sell_test(mint, amount_raw)

        deployer_rugs: Optional[int] = None
        if report is not None:
            deployer_rugs = 1 if rc.get("rugged_flag") else 0
            for risk in (report.get("risks") or []):
                name = (risk.get("name") or "").lower()
                if "creator" in name and ("rug" in name or "dump" in name):
                    deployer_rugs = (deployer_rugs or 0) + 1

        return TokenFacts(
            mint=mint, symbol=stats.get("symbol") or "?",
            pair_address=stats.get("pair_address"),
            price_usd=price,
            liquidity_usd=stats.get("liquidity_usd"),
            volume_1h_usd=stats.get("volume_1h_usd"),
            volume_6h_usd=stats.get("volume_6h_usd"),
            volume_24h_usd=stats.get("volume_24h_usd"),
            price_change_1h_pct=stats.get("price_change_1h_pct"),
            pair_created_at=stats.get("pair_created_at"),
            mint_authority_revoked=mint_rev,
            freeze_authority_revoked=freeze_rev,
            lp_locked_or_burned_days=rc.get("lp_locked_days"),
            top10_holder_pct_ex_lp=top10,
            holder_count=holders,
            holder_count_prev=prev_holders,
            deployer=rc.get("deployer"),
            deployer_prior_rugs=deployer_rugs,
            sell_route_exists=sell_ok,
            fetched_at=datetime.now(timezone.utc))

    async def _top10_ex_lp(self, mint: str) -> Optional[float]:
        """Top-10 holder share excluding the presumed main LP account
        (the single largest token account — pools dominate real memes'
        largest-account lists). Conservative and simple; None fails closed."""
        largest = await self._rpc.get_largest_token_holders(mint)
        supply_info = await self._rpc.get_token_supply(mint)
        if not largest or not supply_info:
            return None
        supply, _decimals = supply_info
        if supply <= 0:
            return None
        ex_lp = sorted((amt for _addr, amt in largest), reverse=True)[1:11]
        return sum(ex_lp) / supply * 100.0
