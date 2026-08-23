"""Entry point.

  skyfire --config config/skyfire.yaml            run live
  skyfire --config config/skyfire.yaml --selftest connectivity + plumbing check
  skyfire --dashboard                             attach the Rich dashboard
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from skyfire_sol.config import AppConfig, Credentials, load_config, resolve_rpc_url


def main() -> None:
    ap = argparse.ArgumentParser(prog="skyfire", description=__doc__)
    ap.add_argument("--config", default="config/skyfire.yaml")
    ap.add_argument("--selftest", action="store_true",
                    help="check config, wallet decrypt, RPC, Jupiter, "
                         "DexScreener, RugCheck, DB, heartbeat; exit")
    ap.add_argument("--dashboard", action="store_true",
                    help="render the Rich dashboard in this terminal")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.selftest:
        sys.exit(asyncio.run(selftest(cfg)))

    creds = Credentials.from_env(require_wallet=True)
    from skyfire_sol.app import SkyfireApp
    app = SkyfireApp(cfg, creds)

    async def _run() -> None:
        if args.dashboard:
            asyncio.create_task(_dashboard_loop(app))
        await app.start()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("interrupted — shutting down")


async def _dashboard_loop(app) -> None:
    from rich.live import Live

    from skyfire_sol.monitoring.dashboard import render
    with Live(render(app.bb), refresh_per_second=1) as live:
        while True:
            await asyncio.sleep(1.0)
            live.update(render(app.bb))


async def selftest(cfg: AppConfig) -> int:
    import os

    import httpx

    from skyfire_sol.chain.helius import HeliusRpc
    from skyfire_sol.clients.dexscreener import DexScreenerClient
    from skyfire_sol.clients.jupiter import JupiterClient
    from skyfire_sol.clients.rugcheck import RugCheckClient
    from skyfire_sol.config import USDC_MINT, WSOL_MINT
    from skyfire_sol.monitoring.heartbeat import write_heartbeat
    from skyfire_sol.blackboard import Blackboard
    from skyfire_sol.persistence.db import Database

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    print("skyfire selftest")
    creds = Credentials.from_env(require_wallet=False)
    check("config", True, "loaded")

    if creds.key_passphrase and os.path.exists(cfg.wallet.age_key_path):
        try:
            from skyfire_sol.wallet import Wallet
            w = Wallet.load(cfg.wallet.age_key_path, creds.key_passphrase)
            check("wallet decrypt", True, str(w.pubkey))
        except Exception as e:                       # noqa: BLE001 — reported below
            check("wallet decrypt", False, str(e))
    else:
        print("  [skip] wallet decrypt — no key file or SKYFIRE_KEY_PASSPHRASE")

    async with httpx.AsyncClient(timeout=20.0) as http:
        rpc = HeliusRpc(resolve_rpc_url(cfg.rpc, creds), http)
        try:
            blockhash, lvbh = await rpc.get_latest_blockhash()
            check("rpc getLatestBlockhash", True, f"height {lvbh}")
        except Exception as e:                       # noqa: BLE001
            check("rpc getLatestBlockhash", False, str(e))

        jup = JupiterClient(http, cfg.jupiter.base_url)
        try:
            q = await jup.quote(WSOL_MINT, USDC_MINT, 10**9, max_price_impact_pct=1.0)
            check("jupiter quote SOL->USDC", True,
                  f"1 SOL = {q.out_amount / 1e6:.2f} USDC "
                  f"(impact {q.price_impact_pct:.4f}%)")
        except Exception as e:                       # noqa: BLE001
            check("jupiter quote SOL->USDC", False, str(e))

        dex = DexScreenerClient(http)
        profiles = await dex.latest_solana_profiles()
        check("dexscreener profiles", len(profiles) > 0, f"{len(profiles)} tokens")

        rug = RugCheckClient(http)
        report = await rug.report(USDC_MINT)
        check("rugcheck report", report is not None,
              f"score {report.get('score_normalised') if report else '-'}")

    db = Database(cfg.data.db_path)
    try:
        await db.open()
        await db.close()
        check("sqlite open+schema", True, cfg.data.db_path)
    except Exception as e:                           # noqa: BLE001
        check("sqlite open+schema", False, str(e))

    try:
        write_heartbeat(cfg.data.heartbeat_path, Blackboard(), "selftest")
        check("heartbeat write", True, cfg.data.heartbeat_path)
    except Exception as e:                           # noqa: BLE001
        check("heartbeat write", False, str(e))

    print("selftest:", "PASS" if not failures else f"FAIL ({', '.join(failures)})")
    return 0 if not failures else 1


if __name__ == "__main__":
    main()
