"""Remote web dashboard — a separate PROCESS, never inside the trading
loop (same posture as the MCP monitor, and it reuses that module's
Monitor as its only data layer).

Read surface: heartbeat/snapshot/journals/DB, all read-only.
Control surface: exactly the operator buttons — kill switch, probation
full-size, policy freeze — which are file writes into the state dir.
No wallet, no RPC, no executor: a fully compromised dashboard can
observe and halt, nothing else.

Auth: every /api route requires the SKYFIRE_DASH_TOKEN bearer token.
The process REFUSES to start without one — this page is designed to be
tunneled (ngrok/tailscale) and must never be open.

Run:  skyfire-dash --state-dir state/skyfire --port 8787
Then: ngrok http 8787        (or serve over Tailscale and skip ngrok)
"""

from __future__ import annotations

import argparse
import hmac
import os
import sys

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from skyfire_sol.monitoring.mcp import Monitor


def build_app(state_dir: str, token: str) -> Starlette:
    monitor = Monitor(state_dir)

    def authed(request: Request) -> bool:
        supplied = request.headers.get("authorization", "")
        if supplied.lower().startswith("bearer "):
            supplied = supplied[7:]
        return bool(token) and hmac.compare_digest(supplied, token)

    def guarded(fn, *, method="GET"):
        async def endpoint(request: Request):
            if not authed(request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            kwargs = {}
            if method == "POST":
                try:
                    body = await request.json()
                except Exception:                    # noqa: BLE001 — empty body ok
                    body = {}
                if isinstance(body, dict):
                    kwargs = body
            try:
                return JSONResponse(fn(**kwargs))
            except TypeError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
        return endpoint

    async def index(_request: Request):
        return HTMLResponse(PAGE)

    routes = [
        Route("/", index),
        Route("/api/status", guarded(monitor.status)),
        Route("/api/positions", guarded(monitor.positions)),
        Route("/api/policy", guarded(monitor.policy_status)),
        Route("/api/phantom", guarded(monitor.phantom_stats)),
        Route("/api/journal", guarded(monitor.journal_tail)),
        Route("/api/trades", guarded(monitor.trades_tail)),
        Route("/api/kill", guarded(monitor.kill, method="POST"), methods=["POST"]),
        Route("/api/go_full_size", guarded(monitor.go_full_size, method="POST"),
              methods=["POST"]),
        Route("/api/back_to_probation",
              guarded(monitor.back_to_probation, method="POST"), methods=["POST"]),
        Route("/api/freeze_policy", guarded(monitor.freeze_policy, method="POST"),
              methods=["POST"]),
        Route("/api/unfreeze_policy",
              guarded(monitor.unfreeze_policy, method="POST"), methods=["POST"]),
    ]
    return Starlette(routes=routes)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir", default="state/skyfire")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()

    token = os.environ.get("SKYFIRE_DASH_TOKEN", "")
    if len(token) < 16:
        print("SKYFIRE_DASH_TOKEN must be set (>=16 chars) — this page is "
              "meant to be tunneled and must never run open.", file=sys.stderr)
        raise SystemExit(1)

    import uvicorn
    uvicorn.run(build_app(args.state_dir, token),
                host=args.host, port=args.port, log_level="warning")


PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SKYFIRE</title>
<style>
  body { background:#0e1116; color:#d7dde6; font:14px/1.5 ui-monospace,Menlo,monospace;
         margin:0; padding:1.2rem; }
  h1 { font-size:1.05rem; letter-spacing:.14em; color:#8ab4ff; margin:0 0 1rem; }
  h2 { font-size:.8rem; letter-spacing:.12em; text-transform:uppercase;
       color:#7b8494; margin:1.6rem 0 .5rem; }
  table { border-collapse:collapse; width:100%; }
  th, td { text-align:left; padding:.25rem .7rem .25rem 0; border-bottom:1px solid #1d232e;
           font-variant-numeric:tabular-nums; }
  th { color:#7b8494; font-weight:normal; }
  .ok { color:#5fd38a; } .warn { color:#f2c14e; } .bad { color:#ff6b6b; }
  #badges span { display:inline-block; margin-right:.6rem; padding:.1rem .5rem;
                 border:1px solid #333c4d; border-radius:4px; }
  button { background:#182030; color:#d7dde6; border:1px solid #33405c; border-radius:5px;
           padding:.35rem .8rem; margin:.2rem .4rem .2rem 0; cursor:pointer; font:inherit; }
  button.danger { border-color:#7a2e2e; color:#ff9b9b; }
  #login { margin-bottom:1rem; }
  input { background:#182030; color:#d7dde6; border:1px solid #33405c; border-radius:5px;
          padding:.35rem .5rem; font:inherit; width:20rem; }
  pre { white-space:pre-wrap; color:#9aa4b2; }
</style></head><body>
<h1>SKYFIRE</h1>
<div id="login">token <input id="tok" type="password" placeholder="SKYFIRE_DASH_TOKEN">
  <button onclick="saveTok()">connect</button></div>
<div id="main" style="display:none">
  <div id="badges"></div>
  <h2>Sleeves</h2><table id="sleeves"></table>
  <h2>Positions</h2><table id="positions"></table>
  <h2>Policy</h2><div id="policy"></div>
  <h2>Phantom log</h2><div id="phantom"></div>
  <h2>Controls</h2>
  <div>
    <button onclick="post('go_full_size')">go full size</button>
    <button onclick="post('back_to_probation')">back to probation</button>
    <button onclick="post('freeze_policy')">freeze policy</button>
    <button onclick="post('unfreeze_policy')">unfreeze policy</button>
    <button class="danger" onclick="kill()">KILL + FLATTEN</button>
  </div>
  <h2>Recent decisions</h2><pre id="journal"></pre>
</div>
<script>
let tok = localStorage.getItem('skyfire_tok') || '';
function saveTok() {
  tok = document.getElementById('tok').value.trim();
  try { localStorage.setItem('skyfire_tok', tok); } catch (e) {}
  refresh();
}
async function api(path, opts) {
  const r = await fetch('/api/' + path, Object.assign({
    headers: {'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'}
  }, opts || {}));
  if (r.status === 401) { show(false); throw new Error('unauthorized'); }
  return r.json();
}
async function post(path, body) {
  if (!confirm(path.replace(/_/g, ' ') + ' — are you sure?')) return;
  await api(path, {method: 'POST', body: JSON.stringify(body || {})});
  refresh();
}
async function kill() {
  const reason = prompt('KILL reason (this halts and flattens everything):');
  if (!reason) return;
  await api('kill', {method: 'POST', body: JSON.stringify({reason})});
  refresh();
}
function show(ok) {
  document.getElementById('main').style.display = ok ? '' : 'none';
  document.getElementById('login').style.display = ok ? 'none' : '';
}
function row(cells, tag) {
  return '<tr>' + cells.map(c => '<' + (tag||'td') + '>' + c + '</' + (tag||'td') + '>').join('') + '</tr>';
}
async function refresh() {
  if (!tok) return;
  let s;
  try { s = await api('status'); } catch (e) { return; }
  show(true);
  const hb = s.heartbeat || {};
  const badges = [];
  badges.push('<span class="' + (s.stale ? 'bad' : 'ok') + '">' +
              (s.stale ? 'STALE' : 'LIVE') + '</span>');
  badges.push('<span>NAV $' + (s.nav_usd == null ? '?' : (+s.nav_usd).toLocaleString()) + '</span>');
  const sf = s.safety || {};
  badges.push('<span class="' + (sf.breaker === 'armed' ? 'ok' : 'bad') + '">breaker ' + (sf.breaker || '?') + '</span>');
  if (s.kill_engaged) badges.push('<span class="bad">KILL</span>');
  if (sf.probation) badges.push('<span class="warn">PROBATION ' + (sf.clean_fills || 0) + ' clean</span>');
  if (sf.soft_tier_active) badges.push('<span class="warn">SOFT TIER</span>');
  badges.push('<span>dd ' + (sf.drawdown_pct == null ? '?' : (+sf.drawdown_pct).toFixed(1)) + '%</span>');
  document.getElementById('badges').innerHTML = badges.join('');

  const alloc = s.allocations_target || {};
  document.getElementById('sleeves').innerHTML = row(['sleeve','target %'],'th') +
    Object.keys(alloc).map(k => row([k, (+alloc[k]).toFixed(1)])).join('');

  const pos = await api('positions');
  document.getElementById('positions').innerHTML =
    row(['sleeve','symbol','qty','entry $','mark $','pnl %'],'th') +
    (pos.length ? pos.map(p => {
      const qty = p.qty_raw / Math.pow(10, p.decimals);
      const mark = p.last_mark_usd || p.entry_price_usd || 0;
      const pnl = p.entry_price_usd ? ((mark / p.entry_price_usd - 1) * 100).toFixed(1) : '?';
      return row([p.sleeve, p.symbol, qty.toLocaleString(),
                  (+p.entry_price_usd).toPrecision(4), (+mark).toPrecision(4),
                  '<span class="' + (pnl >= 0 ? 'ok' : 'bad') + '">' + pnl + '</span>']);
    }).join('') : row(['(flat)','','','','','']));

  const pol = await api('policy');
  const p = pol.policy || {};
  document.getElementById('policy').innerHTML =
    'v' + (p.version ?? '?') + ' (' + (p.origin || '?') + ')' +
    (pol.frozen ? ' <span class="warn">FROZEN</span>' : ' <span class="ok">learning</span>') +
    '<br>min_vol_accel ' + p.min_vol_accel + ' · weights ' + p.w_accel + '/' + p.w_growth +
    '/' + p.w_liq + ' · press ' + p.winner_press_gain +
    '<br>' + (pol.recent_conclusions || []).slice(0, 3).map(c => c.reasoning).join('<br>');

  const ph = await api('phantom');
  document.getElementById('phantom').innerHTML =
    (ph.counts || []).slice(0, 8).map(c => c.stage + '/' + c.reason + ': ' + c.n).join(' · ') +
    '<br>' + (ph.forward_returns || []).map(f =>
      f.stage + ' avg fwd 6h ' + (f.avg_fwd_6h == null ? '?' : (+f.avg_fwd_6h).toFixed(1) + '%')
    ).join(' · ');

  const j = await api('journal');
  document.getElementById('journal').textContent =
    j.slice(-12).reverse().map(d => JSON.stringify(d)).join('\\n');
}
setInterval(refresh, 5000);
refresh();
</script></body></html>
"""


if __name__ == "__main__":
    main()
