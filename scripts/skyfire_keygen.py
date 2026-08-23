#!/usr/bin/env python3
"""Generate the SKYFIRE_SOL hot wallet.

Creates a fresh Solana keypair, encrypts the 64-byte secret with an age
passphrase (pyrage), writes it to secrets/wallet.age (mode 0600), and
prints the public key so you can fund it manually from Phantom.

The plaintext key never touches disk. The passphrase is read from the
SKYFIRE_KEY_PASSPHRASE environment variable, or prompted interactively
(with confirmation) when unset.

Usage:
    python scripts/skyfire_keygen.py [--out secrets/wallet.age]

The same passphrase must be present as SKYFIRE_KEY_PASSPHRASE in the
runtime environment (systemd EnvironmentFile) for the bot to boot.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from solders.keypair import Keypair

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from skyfire_sol.wallet import encrypt_keypair  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None)
    ap.add_argument("--evm", action="store_true",
                    help="generate the Hyperliquid (EVM) wallet instead of the "
                         "Solana one; fund it with USDC via the Arbitrum bridge")
    args = ap.parse_args()

    out = Path(args.out or ("secrets/wallet_hl.age" if args.evm
                            else "secrets/wallet.age"))
    if out.exists():
        print(f"REFUSING to overwrite existing {out} — move it aside first "
              "(it may hold the only key to funded assets).", file=sys.stderr)
        return 1

    passphrase = os.environ.get("SKYFIRE_KEY_PASSPHRASE") or ""
    if not passphrase:
        passphrase = getpass.getpass("New wallet passphrase: ")
        if passphrase != getpass.getpass("Confirm passphrase: "):
            print("passphrases do not match", file=sys.stderr)
            return 1
    if len(passphrase) < 12:
        print("passphrase must be at least 12 characters", file=sys.stderr)
        return 1

    if args.evm:
        from skyfire_sol.wallet_evm import EvmWallet
        w = EvmWallet.generate()
        blob = w.encrypted(passphrase)
        pubkey_line = (f"ADDRESS (fund with USDC on Hyperliquid via the "
                       f"Arbitrum bridge): {w.address}")
    else:
        kp = Keypair()
        blob = encrypt_keypair(kp, passphrase)
        pubkey_line = f"PUBKEY (fund this from Phantom): {kp.pubkey()}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.touch(mode=0o600)
    out.write_bytes(blob)
    out.chmod(0o600)

    print(f"wrote {out} ({len(blob)} bytes, mode 0600)")
    print(pubkey_line)
    print("Keep the passphrase safe — without it the funds are unrecoverable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
