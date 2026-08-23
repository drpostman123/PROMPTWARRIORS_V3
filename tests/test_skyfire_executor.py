"""ExecutionAgent against a fake RPC and a real throwaway keypair:
call sequence, preflight abort, staleness re-quote, terminal resolution."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.transaction import VersionedTransaction

from skyfire_sol.chain.helius import SendOutcome
from skyfire_sol.clients.jupiter import SlippageCapExceeded
from skyfire_sol.config import AppConfig, USDC_MINT
from skyfire_sol.models import IntentKind, JupQuote, Side, SleeveId, TradeIntent, Urgency
from skyfire_sol.safety.gate import ApprovedOrder, _GATE_TOKEN
from skyfire_sol.safety.probation import Probation
from skyfire_sol.execution.executor import ExecutionAgent
from skyfire_sol.wallet import Wallet

NOW = datetime.now(timezone.utc)


def unsigned_tx_b64(payer: Keypair) -> str:
    msg = MessageV0.try_compile(payer.pubkey(), [], [], Hash.default())
    tx = VersionedTransaction.populate(msg, [])
    return base64.b64encode(bytes(tx)).decode()


class FakeRpc:
    def __init__(self):
        self.calls = []
        self.sim_error = None
        self.timeout_first = False
        self.tx_meta = None

    async def get_priority_fee_estimate(self, tx_b64=None):
        self.calls.append("fee")
        return 42

    async def get_latest_blockhash(self):
        self.calls.append("blockhash")
        return "hash", 100

    async def simulate(self, tx_b64):
        self.calls.append("simulate")
        return self.sim_error

    async def send_and_confirm(self, signed, lvbh, timeout_s=60.0):
        self.calls.append("send")
        if self.timeout_first:
            return SendOutcome("SIG1", False, "confirm_timeout")
        return SendOutcome("SIG1", True, None, 1)

    async def await_terminal(self, sig, lvbh, poll_s=2.0):
        self.calls.append("await_terminal")
        return SendOutcome(sig, True, None, 2)

    async def _call(self, method, params):
        self.calls.append(method)
        return self.tx_meta


class FakeJup:
    def __init__(self, payer):
        self.payer = payer
        self.quote_calls = 0
        self.requote_raises = False

    async def quote(self, in_mint, out_mint, amount, cap):
        self.quote_calls += 1
        if self.requote_raises:
            raise SlippageCapExceeded(9.9, cap)
        return JupQuote(in_mint, out_mint, amount, 1_000_000, 0.5,
                        int(cap * 100), "{}", datetime.now(timezone.utc))

    async def swap_transaction(self, quote, pubkey, priority_fee_lamports=None):
        return unsigned_tx_b64(self.payer)

    async def price_usd_per_token(self, mint, decimals, probe_tokens=1.0):
        return 100.0


def build(tmp_path, quote_age_s=0.0):
    cfg = AppConfig()
    kp = Keypair()
    wallet = Wallet(kp)
    rpc = FakeRpc()
    jup = FakeJup(kp)
    probation = Probation(cfg.risk, str(tmp_path))
    published, trades = [], []

    async def publish(topic, msg):
        published.append((topic, msg))

    async def log_trade(rec):
        trades.append(rec)

    ex = ExecutionAgent(cfg, wallet, rpc, jup, probation, publish, log_trade)
    intent = TradeIntent("i1", SleeveId.MEME_ROTATION, IntentKind.ENTRY, Side.BUY,
                         "M", USDC_MINT, 100.0, Urgency.NORMAL, "t", NOW,
                         position_id="pos1")
    quote = JupQuote(USDC_MINT, "M", 100_000_000, 1_000_000, 0.5, 300, "{}",
                     datetime.now(timezone.utc) - timedelta(seconds=quote_age_s))
    approved = ApprovedOrder(intent=intent, quote=quote, size_usd=100.0,
                             amount_raw=100_000_000, gate_trace="[]",
                             approved_ts=NOW, _token=_GATE_TOKEN)
    return ex, rpc, jup, probation, published, trades, approved


async def test_happy_path_sequence_and_fill(tmp_path):
    ex, rpc, jup, probation, published, trades, approved = build(tmp_path)
    fill = await ex.execute(approved)
    assert fill is not None and fill.tx_sig == "SIG1"
    # simulate must precede send; blockhash before both
    assert rpc.calls.index("simulate") < rpc.calls.index("send")
    assert rpc.calls.index("blockhash") < rpc.calls.index("simulate")
    topics = [t for t, _ in published]
    assert "fills" in topics
    assert trades[0]["kind"] == "fill"
    assert probation.clean_fills == 1               # 0% realized slip vs quote


async def test_preflight_failure_never_sends(tmp_path):
    ex, rpc, jup, probation, published, trades, approved = build(tmp_path)
    rpc.sim_error = '{"InstructionError": [0, "InsufficientFunds"]}'
    fill = await ex.execute(approved)
    assert fill is None
    assert "send" not in rpc.calls
    assert trades[0]["kind"] == "execution_failed"
    assert "preflight" in trades[0]["detail"]
    assert probation.clean_fills == 0


async def test_confirm_timeout_resolves_to_terminal(tmp_path):
    ex, rpc, jup, probation, published, trades, approved = build(tmp_path)
    rpc.timeout_first = True
    fill = await ex.execute(approved)
    assert "await_terminal" in rpc.calls            # never left unknown
    assert fill is not None and fill.tx_sig == "SIG1"


async def test_stale_quote_triggers_requote(tmp_path):
    ex, rpc, jup, probation, published, trades, approved = build(
        tmp_path, quote_age_s=60.0)
    await ex.execute(approved)
    assert jup.quote_calls == 1                     # re-quoted once


async def test_requote_over_cap_aborts(tmp_path):
    ex, rpc, jup, probation, published, trades, approved = build(
        tmp_path, quote_age_s=60.0)
    jup.requote_raises = True
    fill = await ex.execute(approved)
    assert fill is None and "send" not in rpc.calls
    assert "requote_failed" in trades[0]["detail"]


async def test_fresh_quote_skips_requote(tmp_path):
    ex, rpc, jup, probation, published, trades, approved = build(
        tmp_path, quote_age_s=1.0)
    await ex.execute(approved)
    assert jup.quote_calls == 0
