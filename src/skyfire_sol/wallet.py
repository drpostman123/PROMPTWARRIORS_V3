"""Dedicated hot wallet.

The keypair lives age-encrypted at rest (secrets/wallet.age) and is
decrypted into memory at startup with the passphrase from the
environment. Plaintext key bytes never touch disk. This is NEVER the
user's main wallet — it is funded manually from Phantom.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from tradecore.logging import get_logger

log = get_logger("wallet")


def _decrypt_age(blob: bytes, passphrase: str) -> bytes:
    try:
        import pyrage
        return pyrage.passphrase.decrypt(blob, passphrase)
    except ImportError:
        # Fallback: age CLI. Passphrase via stdin prompt is interactive-only,
        # so use the documented AGE_PASSPHRASE-less trick: expect script is
        # overkill — require pyrage in production; CLI path covers dev boxes
        # with age installed and pyrage wheels unavailable.
        proc = subprocess.run(
            ["age", "-d", "-i", "/dev/stdin"], input=blob,
            capture_output=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"age decrypt failed: {proc.stderr.decode()!r}") from None
        return proc.stdout


def encrypt_keypair(kp: Keypair, passphrase: str) -> bytes:
    import pyrage
    return pyrage.passphrase.encrypt(bytes(kp), passphrase)


class Wallet:
    def __init__(self, keypair: Keypair) -> None:
        self._kp = keypair

    @classmethod
    def load(cls, age_path: str, passphrase: str) -> "Wallet":
        blob = Path(age_path).read_bytes()
        raw = _decrypt_age(blob, passphrase)
        if len(raw) != 64:
            raise RuntimeError(f"decrypted key has unexpected length {len(raw)} (want 64)")
        kp = Keypair.from_bytes(raw)
        log.info("wallet_loaded", pubkey=str(kp.pubkey()))
        return cls(kp)

    @property
    def pubkey(self) -> Pubkey:
        return self._kp.pubkey()

    @property
    def keypair(self) -> Keypair:
        return self._kp
