# Getting the master system onto your Linux desktop — step by step

This is the operator's checklist. It assumes Ubuntu (or Mint) on the desktop and that the
final kickoff prompt lives in this repository at `docs/MASTER_KICKOFF_PROMPT.md` (and the
Picador-only one at `docs/PICADOR_KICKOFF_PROMPT.md`).

## Part A — Accounts and access to apply for now (they take days; code takes hours)

| # | Do this | Why | Time |
|---|---|---|---|
| A1 | Webull: open the **Event Contract account** from your existing individual account (app → profile → Events), sign the category agreements | Needed for any live Webull event trade | minutes, approval same day |
| A2 | Webull: apply for **OpenAPI** (developer.webull.com → Individual Application); $100 minimum, approval "1–2 business days", depends on trading history | Production App Key/Secret | 1–2 days |
| A3 | Webull: on the same page click **"Using OpenAPI service in Paper Trading"** to get the **sandbox App Key** (auto-approved) | Paper = sandbox; you build against this first | minutes |
| A4 | Webull: in the Advanced Quotes Center subscribe to the **OpenAPI** market-data package(s) you need (options "OPRA Real-Time Non-display" for the fair-value engine; note stock L1 is also a paid OpenAPI subscription) | App subscriptions do not carry over to OpenAPI | minutes, billed monthly |
| A5 | Kalshi: open an account, complete KYC, create an **API key** (RSA key pair) | Resolution feed (BRTI/ERTI 60-s average) and full order books; optional direct trading | 1 day |
| A6 | If you will ever trade Kalshi contracts through two accounts (e.g., Webull + Kalshi direct): email Kalshi (rule33@kalshi.com) listing every account **before** the second one trades | Rulebook 3.3(b) | 1 email |
| A7 | Optional now, needed for later sleeves: tastytrade Open API (developer.tastytrade.com, cert sandbox), Public Individual Trader API (personal access token), Robinhood Crypto API keys | Hedge/data arms | days |
| A8 | Telegram: create a bot with @BotFather and note your chat id | Alerts | minutes |
| A9 | healthchecks.io (or similar): create one check per arm and one for the head | Dead-man switch | minutes |

Keep every key in a password manager for now; the code will move them into an encrypted credential store.

## Part B — Desktop preparation (about 30 minutes)

```bash
# 1. System packages
sudo apt update && sudo apt install -y git curl build-essential chrony sqlite3 python3-venv

# 2. Time sync matters for expiry-sensitive trading
sudo systemctl enable --now chrony && chronyc tracking

# 3. uv (Python toolchain manager) — the project pins Python 3.12 through it
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env && uv --version

# 4. Node.js (needed by Claude Code) and Claude Code itself
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
npm install -g @anthropic-ai/claude-code
claude --version

# 5. (Docker is NOT needed for version 1: no NATS/Postgres until the octopus phase)

# 6. Project directory and the research bundle
mkdir -p ~/code/master-system && cd ~/code/master-system
git init
git clone --depth 1 --branch claude/prediction-market-arbitrage-dxi93z \
  https://github.com/drpostman123/PROMPTWARRIORS_V3.git /tmp/research
mkdir -p docs/research && cp -r /tmp/research/docs/* docs/research/
git add docs && git commit -m "Import research bundle"
```

If any `curl … | sh` line makes you uneasy, download the script first, read it, then run it; that is the same rule the system applies to every dependency.

## Part C — Starting the fresh Claude Code session

```bash
cd ~/code/master-system
claude
```
1. In the session, type `/init` only after the build starts (it documents the codebase); for now paste the kickoff prompt.
2. Open `docs/research/MASTER_KICKOFF_PROMPT.md` (the master system) in another window — use `PICADOR_KICKOFF_PROMPT.md` only if you want the prediction-market sleeve alone, copy everything below the horizontal rule, paste it into the session and send.
3. The prompt's Step 0 re-reads the research bundle from `docs/research/`, so the session starts with the full evidence base even offline.
4. Expected first outputs: `docs/PLAN.md` (Claude's restatement with disagreements), then deliverable 1 (repo scaffold). Review each stop point before saying "continue".
5. When the session asks for credentials, provide **sandbox** keys only. Production keys enter only through the encrypted credential store the build creates, and only after the paper gates pass.

## Part D — Keeping the desktop as the standby later
- The VPS becomes primary once the first sleeve is live; the desktop keeps the identical release and pulls state via the replication the build sets up (WireGuard + Postgres replica, or Litestream restore).
- Never run the head in live mode on both machines. The leader lease is the guard, but the operator rule is simpler: promote the desktop only after confirming through each broker that no orders from the VPS are resting.
- Monthly: `git pull`, rebuild, run the failover drill in paper.

## Part E — Session hygiene
- One session per deliverable is fine; the repo and `docs/DECISIONS.md` carry the memory, not the chat.
- Commit after every accepted deliverable; push to a private GitHub repo you own.
- If a session proposes installing something not in the plan's stack, ask it to justify and to show the package's source and maintainers first.
