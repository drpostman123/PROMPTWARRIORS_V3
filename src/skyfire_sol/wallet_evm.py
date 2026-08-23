"""Hyperliquid hot wallet (EVM secp256k1 key).

Same discipline as the Solana wallet: the 32-byte private key lives
age-encrypted at rest (secrets/wallet_hl.age), decrypted into memory at
startup with SKYFIRE_KEY_PASSPHRASE. Plaintext never touches disk. The
address is funded manually (USDC to Hyperliquid via the Arbitrum
bridge). NEVER a main wallet.
"""

from __future__ import annotations

from pathlib import Path

from tradecore.logging import get_logger

log = get_logger("wallet_evm")


class EvmWallet:
    def __init__(self, account) -> None:                 # eth_account LocalAccount
        self._acct = account

    @classmethod
    def generate(cls) -> "EvmWallet":
        from eth_account import Account
        return cls(Account.create())

    @classmethod
    def load(cls, age_path: str, passphrase: str) -> "EvmWallet":
        from eth_account import Account

        from skyfire_sol.wallet import _decrypt_age
        raw = _decrypt_age(Path(age_path).read_bytes(), passphrase)
        if len(raw) != 32:
            raise RuntimeError(f"decrypted EVM key has length {len(raw)} (want 32)")
        w = cls(Account.from_key(raw))
        log.info("hl_wallet_loaded", address=w.address)
        return w

    def encrypted(self, passphrase: str) -> bytes:
        import pyrage
        return pyrage.passphrase.encrypt(bytes(self._acct.key), passphrase)

    @property
    def address(self) -> str:
        return self._acct.address

    @property
    def account(self):
        return self._acct
