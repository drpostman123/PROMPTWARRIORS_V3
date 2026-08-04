# GodMode0DTE — Ubuntu deployment runbook

Live-mode 0DTE debit-vertical runtime for Tastytrade. One systemd service runs
the trading loop, one runs the read-only dashboard, and a timer performs the
**mandatory** pre-market restart (daily rollover is restart-based by design —
see DESIGN_SPEC deferred items and the comments in `godmode-restart.timer`).

Everything below assumes the install prefix `/opt/godmode` and the service
user `godmode`. If you change the prefix, change it in **all four** unit/conf
files — the code resolves `state/`, `logs/`, and `config/` **relative to the
process CWD**, so `WorkingDirectory=` is a safety control, not a convenience
(details in "Lockout & baseline" below).

## 1. Install

```bash
# 1. Non-login service user and directory layout
sudo useradd --system --home-dir /opt/godmode --shell /usr/sbin/nologin godmode
sudo mkdir -p /opt/godmode
sudo git clone <repo-url> /opt/godmode          # or rsync a release
cd /opt/godmode

# 2. Virtualenv + package (installs the `godmode` console script)
sudo python3 -m venv /opt/godmode/venv
sudo /opt/godmode/venv/bin/pip install --upgrade pip
sudo /opt/godmode/venv/bin/pip install /opt/godmode
sudo /opt/godmode/venv/bin/pip install streamlit plotly pandas   # dashboard deps

# 3. Writable runtime dirs, locked-down ownership
sudo mkdir -p /opt/godmode/state /opt/godmode/logs /opt/godmode/.dashboard-home
sudo chown -R godmode:godmode /opt/godmode
sudo chmod 750 /opt/godmode/state /opt/godmode/logs

# 4. Secrets (see format below)
sudo mkdir -p /etc/godmode
sudo touch /etc/godmode/env
sudo chown root:root /etc/godmode/env && sudo chmod 600 /etc/godmode/env
sudoedit /etc/godmode/env

# 5. Units + logrotate
sudo cp deploy/godmode.service deploy/godmode-dashboard.service \
        deploy/godmode-restart.service deploy/godmode-restart.timer \
        /etc/systemd/system/
sudo cp deploy/logrotate.conf /etc/logrotate.d/godmode
sudo systemctl daemon-reload
sudo systemctl enable --now godmode.service godmode-dashboard.service godmode-restart.timer

# 6. Verify
systemctl status godmode godmode-dashboard
systemctl list-timers godmode-restart.timer
systemd-analyze calendar 'Mon..Fri 08:30 America/New_York'   # next ET restart
journalctl -u godmode -f
```

Dashboard: bound to `127.0.0.1:8501` with no auth. Reach it via
`ssh -L 8501:127.0.0.1:8501 <host>` → http://localhost:8501. Never expose it.

## 2. `/etc/godmode/env` format

Plain `KEY=value` lines (systemd `EnvironmentFile`, not shell — no quotes
needed, no `export`, `#` comments allowed). Root-owned, mode 600; systemd
reads it as root, the process runs as `godmode` and never sees the file.

```
TASTYTRADE_USERNAME=your_username
TASTYTRADE_PASSWORD=your_password
# Live-arming interlock. The config has paper_mode: false; pydantic REFUSES to
# boot live unless this is exactly YES. Remove the line to force paper-only.
GODMODE_CONFIRM_LIVE=YES
```

After editing: `sudo systemctl restart godmode` (systemd re-reads the file on
each start only).

## 3. Lockout & baseline behavior across restarts

Three small files under `/opt/godmode/state/` carry safety state across
restarts. All paths are **CWD-relative** in code — this is why
`WorkingDirectory=/opt/godmode` is mandatory and why you must never run the
service binary by hand from another directory "just to test": from a fresh
CWD the runtime sees no lockout file and will happily re-arm on a locked day.

| File | Written when | Effect on restart |
|---|---|---|
| `state/lockout.json` | Breaker trips (-6% day loss, dead equity feed with open positions, fatal crash with open positions) | If its `date` == today, boot goes straight to `LOCKED_OUT` — no entries for the rest of the session. **Unreadable/corrupt file fails SAFE: treated as locked today.** Yesterday's file is ignored (date mismatch), not deleted — no cleanup needed. |
| `state/baseline.json` | First successful equity fetch of the session day | If `date` == today, starting equity is restored, so a mid-day restart keeps the -6% limit anchored to the **morning** equity (without this, a restart after a drawdown would re-anchor at depleted equity and allow ~-11% cumulative). Stale-dated file is ignored and overwritten on the next session's first equity fetch. |
| `state/volume_profile.json` | End-of-day baseline update | 20-day per-slot relative-volume profile; loss of this file only degrades the rel-volume gate until it re-accumulates. |

Manual overrides:

- **Clear a lockout early** (do not do this): the lockout is irreversible for
  the session on purpose. Deleting `state/lockout.json` and restarting
  re-arms the system. That is a deliberate two-step human action; there is no
  supported command for it.
- **Force a lockout**: `echo '{"date":"'$(date +%F)'","reason":"manual","ts":""}' | sudo -u godmode tee /opt/godmode/state/lockout.json`
  then `sudo systemctl restart godmode`.

Restart semantics with open positions: `systemctl stop|restart` sends SIGINT
(graceful; the runtime flattens on the way down if the breaker logic demands
it), and on the next boot `_reconcile` **adopts** any live legs that pair into
a known debit vertical so the exit engine keeps managing them; unpairable legs
engage the kill switch (no new entries, exits still run) and log
`unpairable_legs_at_boot` — that log line means a human must look at the
account **now**.

## 4. The 08:30 ET restart (mandatory)

`godmode-restart.timer` restarts the runtime every weekday at 08:30
**America/New_York** (systemd evaluates the timezone suffix itself; the host
can stay on UTC; DST shifts are automatic). This is not hygiene — it *is* the
daily rollover: session date, chain fetch, breaker re-arm, and stale-lockout
expiry all happen at boot. If the timer ever fails to fire
(`systemctl list-timers` shows a past-due `NEXT`), restart manually **before
09:30 ET**. A runtime left running across a date boundary has yesterday's
session date and no 0DTE chain for today: it will not trade (fails safe) but
it is not "up" in any useful sense.

## 5. When it will not arm — checklist

Symptom: dashboard stuck in `LOCKED_OUT` / `PRE_MARKET`, or the service
crash-loops. Check in this order:

1. **Service actually running?** `systemctl status godmode`. If
   `start-limit-hit`: 5 failed starts in 10 min — read
   `journalctl -u godmode -e` first, fix, then
   `sudo systemctl reset-failed godmode && sudo systemctl start godmode`.
2. **Live interlock**: log shows
   `paper_mode is false but GODMODE_CONFIRM_LIVE=YES is not set` → fix
   `/etc/godmode/env` (this is a boot-time pydantic failure → crash loop).
3. **Credentials**: `Set TASTYTRADE_USERNAME and TASTYTRADE_PASSWORD` →
   env file missing/unreadable, or `EnvironmentFile=` path wrong. Repeated
   auth failures can temporarily block the account — that is exactly what
   `StartLimitBurst=5` protects against; do not raise it.
4. **Restored lockout**: log `breaker_restored_locked` or
   `breaker_restore_failsafe` and dashboard breaker = LOCKED. Working as
   designed if the date matches today. If the file is corrupt (failsafe line),
   inspect `state/lockout.json`; only delete it if you are certain no -6% trip
   happened today.
5. **No 0DTE expiration**: log `no_0dte_expiration` — market holiday, or the
   runtime wasn't restarted today (see §4), or the underlying has no expiry
   today. It will idle all day; nothing to fix except the restart.
6. **Clock**: `timedatectl` — NTP must be synced. The state machine runs off
   ET wall-clock; a skewed clock shifts the entry window and force-flat.
7. **Snapshot stale banner on dashboard**: the runtime writes
   `state/snapshot.json` every 2 s; a `RUNTIME STALE` banner with the service
   "running" means the event loop is wedged — `journalctl -u godmode -e`, look
   for `task_crashed` spam, then restart.
8. **Equity feed**: `equity_fetch_failed` every 2 s in the log; after ~15
   consecutive failures **with open positions** the breaker trips on purpose
   (`equity feed dead ~30s with open positions`). With no positions it will
   keep retrying and refuse entries on stale data.
9. **Still scanning but never trading**: not a fault. Score ≥ 93 plus every
   hard gate (event blackout, book conflict/thinning, spread caps, OI, chase,
   regime) must pass — most days legitimately produce zero trades. Check the
   dashboard score breakdown and `state/decisions.jsonl` reject reasons before
   suspecting the deployment.

## 6. Ops notes

- **Disk**: `state/decisions.jsonl` grows ~350 full score records/day and is
  the only fast-growing file; logrotate handles it weekly. `logs/godmode.jsonl`
  rotates via copytruncate (held-open file handle — see logrotate.conf header
  before changing anything there).
- **Backups**: `state/trades.jsonl` + `state/decisions.jsonl*` are the
  calibration record — back them up off-host daily.
- **Upgrades**: `git -C /opt/godmode pull && /opt/godmode/venv/bin/pip install
  /opt/godmode && sudo systemctl restart godmode` — outside market hours, or
  accept that the restart path (§3) will adopt open positions.
- **Never** run two runtime instances against the same account/state dir;
  systemd's single-instance semantics are your mutex — always start it via
  `systemctl`, never by hand.
