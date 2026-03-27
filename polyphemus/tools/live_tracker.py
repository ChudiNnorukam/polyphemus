"""Live Copy Trader Tracker — Visual dashboard showing ugag + our bot side by side.

Serves a web dashboard at http://localhost:3333 that:
1. Polls ugag's trades via Polymarket Data API
2. Polls our bot's trades from performance.db
3. Shows real-time decisions with glowing indicators

Usage:
    python3 polyphemus/tools/live_tracker.py --db /path/to/performance.db
    # Then open http://localhost:3333
"""

import argparse
import json
import sqlite3
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.request import urlopen, Request
from urllib.parse import parse_qs, urlparse


UGAG_ADDR = "0x8ab40cf994e65624ebc890bba4023d74f30ead1e"
POLL_INTERVAL = 10  # seconds
DB_PATH = None

# Cached data
ugag_trades = []
our_trades = []
last_ugag_poll = 0
last_our_poll = 0


def fetch_ugag_trades():
    """Fetch ugag's recent trades from Polymarket Data API."""
    global ugag_trades, last_ugag_poll
    try:
        url = f"https://data-api.polymarket.com/activity?user={UGAG_ADDR}&limit=30"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 Polyphemus/1.0"})
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read())
        trades = []
        for t in data:
            if t.get("type") != "TRADE":
                continue
            trades.append({
                "trader": "ugag",
                "side": t.get("side", ""),
                "outcome": t.get("outcome", ""),
                "price": float(t.get("price", 0)),
                "size": float(t.get("size", 0)),
                "title": t.get("title", "")[:60],
                "timestamp": t.get("timestamp", 0),
            })
        ugag_trades = trades[:20]
        last_ugag_poll = time.time()
    except Exception as e:
        print(f"ugag poll error: {e}")


def compute_stats(trades_list, trader_name):
    """Compute WR, P&L, strategy breakdown from a trade list."""
    if not trades_list:
        return {"wr": 0, "total_pnl": 0, "avg_pnl": 0, "wins": 0, "losses": 0, "n": 0, "by_exit": {}, "by_direction": {}}

    completed = [t for t in trades_list if not t.get("is_open", False)]
    if not completed:
        return {"wr": 0, "total_pnl": 0, "avg_pnl": 0, "wins": 0, "losses": 0, "n": 0, "by_exit": {}, "by_direction": {}}

    wins = sum(1 for t in completed if (t.get("pnl") or 0) > 0)
    losses = len(completed) - wins
    total_pnl = sum(t.get("pnl") or 0 for t in completed)

    by_exit = {}
    for t in completed:
        reason = t.get("exit_reason", "unknown")
        if reason not in by_exit:
            by_exit[reason] = {"n": 0, "pnl": 0, "wins": 0}
        by_exit[reason]["n"] += 1
        by_exit[reason]["pnl"] += t.get("pnl") or 0
        if (t.get("pnl") or 0) > 0:
            by_exit[reason]["wins"] += 1

    by_dir = {"Up": {"n": 0, "pnl": 0, "wins": 0}, "Down": {"n": 0, "pnl": 0, "wins": 0}}
    for t in completed:
        d = t.get("outcome", "Up")
        if d not in by_dir:
            by_dir[d] = {"n": 0, "pnl": 0, "wins": 0}
        by_dir[d]["n"] += 1
        by_dir[d]["pnl"] += t.get("pnl") or 0
        if (t.get("pnl") or 0) > 0:
            by_dir[d]["wins"] += 1

    return {
        "wr": round(100 * wins / len(completed), 1) if completed else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(completed), 2) if completed else 0,
        "wins": wins,
        "losses": losses,
        "n": len(completed),
        "by_exit": {k: {"n": v["n"], "pnl": round(v["pnl"], 2), "wr": round(100*v["wins"]/v["n"], 1) if v["n"] else 0} for k, v in by_exit.items()},
        "by_direction": {k: {"n": v["n"], "pnl": round(v["pnl"], 2), "wr": round(100*v["wins"]/v["n"], 1) if v["n"] else 0} for k, v in by_dir.items()},
    }


def fetch_our_trades():
    """Fetch our bot's recent trades from performance.db."""
    global our_trades, last_our_poll
    if not DB_PATH:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT slug, entry_price, exit_price, pnl, exit_reason, entry_time, exit_time
            FROM trades
            WHERE trade_id NOT LIKE 'dry_%'
            ORDER BY entry_time DESC
            LIMIT 50
        """).fetchall()
        conn.close()
        trades = []
        for r in rows:
            slug, entry_p, exit_p, pnl, reason, entry_t, exit_t = r
            asset = slug.split("-")[0].upper() if slug else ""
            direction = "Up" if "up" in slug.lower() else "Down"
            trades.append({
                "trader": "us",
                "slug": slug,
                "asset": asset,
                "outcome": direction,
                "price": round(entry_p, 4) if entry_p else 0,
                "exit_price": round(exit_p, 4) if exit_p else 0,
                "pnl": round(pnl, 2) if pnl else 0,
                "exit_reason": reason or "open",
                "timestamp": int(entry_t) if entry_t else 0,
                "is_open": exit_t is None,
            })
        our_trades = trades
        last_our_poll = time.time()
    except Exception as e:
        print(f"our trades poll error: {e}")


def poll_loop():
    """Background thread polling both data sources."""
    while True:
        fetch_ugag_trades()
        fetch_our_trades()
        time.sleep(POLL_INTERVAL)


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Live Copy Trader Tracker</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0a0a1a;
    color: #e0e0ff;
    font-family: 'SF Mono', 'Fira Code', monospace;
    overflow-x: hidden;
  }
  .header {
    text-align: center;
    padding: 20px;
    background: linear-gradient(180deg, #0f0f2e 0%, #0a0a1a 100%);
    border-bottom: 1px solid #1a1a3e;
  }
  .header h1 {
    font-size: 24px;
    background: linear-gradient(90deg, #00f0ff, #8b5cf6, #00f0ff);
    background-size: 200%;
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    animation: shimmer 3s ease-in-out infinite;
  }
  @keyframes shimmer { 0%,100%{background-position:0%} 50%{background-position:100%} }

  .stats-bar {
    display: flex;
    justify-content: center;
    gap: 30px;
    padding: 12px;
    font-size: 13px;
    color: #888;
    flex-wrap: wrap;
  }
  .stats-bar .stat { display: flex; gap: 6px; align-items: center; }
  .stats-bar .val { color: #00f0ff; font-weight: bold; }
  .stats-bar .neg { color: #ff4466; }
  .stats-bar .pos { color: #00ff88; }

  .stats-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
    padding: 10px 14px;
    border-bottom: 1px solid #1a1a3e;
  }
  .stat-card {
    background: #0a0a1a;
    border-radius: 8px;
    padding: 10px;
    text-align: center;
  }
  .stat-card .label { font-size: 10px; color: #666; text-transform: uppercase; letter-spacing: 1px; }
  .stat-card .value { font-size: 20px; font-weight: bold; margin-top: 4px; }
  .stat-card .value.pos { color: #00ff88; }
  .stat-card .value.neg { color: #ff4466; }
  .stat-card .value.neutral { color: #00f0ff; }
  .ugag-panel .stat-card .value.neutral { color: #8b5cf6; }

  .breakdown {
    padding: 8px 14px;
    border-bottom: 1px solid #1a1a3e;
  }
  .breakdown h3 { font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; }
  .breakdown-row {
    display: flex;
    justify-content: space-between;
    padding: 3px 0;
    font-size: 12px;
    border-bottom: 1px solid #111;
  }
  .breakdown-row .name { color: #aaa; }
  .breakdown-row .bar-bg {
    width: 60px;
    height: 6px;
    background: #1a1a2e;
    border-radius: 3px;
    overflow: hidden;
    margin: 0 8px;
    align-self: center;
  }
  .breakdown-row .bar-fill { height: 100%; border-radius: 3px; }
  .ugag-panel .bar-fill { background: #8b5cf6; }
  .us-panel .bar-fill { background: #00f0ff; }

  .container {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    padding: 20px;
    max-width: 1400px;
    margin: 0 auto;
  }

  .panel {
    background: #0d0d24;
    border: 1px solid #1a1a3e;
    border-radius: 12px;
    overflow: hidden;
  }
  .panel-header {
    padding: 14px 18px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid #1a1a3e;
  }
  .panel-header h2 { font-size: 16px; }
  .panel-header .badge {
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: bold;
  }
  .ugag-panel .panel-header { border-bottom-color: #8b5cf644; }
  .ugag-panel .badge { background: #8b5cf622; color: #8b5cf6; border: 1px solid #8b5cf644; }
  .us-panel .panel-header { border-bottom-color: #00f0ff44; }
  .us-panel .badge { background: #00f0ff22; color: #00f0ff; border: 1px solid #00f0ff44; }

  .trade-list { padding: 8px; max-height: 500px; overflow-y: auto; }
  .trade-list::-webkit-scrollbar { width: 4px; }
  .trade-list::-webkit-scrollbar-thumb { background: #333; border-radius: 4px; }

  .trade {
    display: grid;
    grid-template-columns: 50px 1fr 70px 60px;
    align-items: center;
    padding: 8px 12px;
    margin: 4px 0;
    border-radius: 8px;
    background: #111133;
    border-left: 3px solid transparent;
    animation: fadeIn 0.5s ease;
    font-size: 13px;
  }
  @keyframes fadeIn { from { opacity:0; transform:translateY(-8px); } to { opacity:1; transform:translateY(0); } }

  .trade.up { border-left-color: #00ff88; }
  .trade.down { border-left-color: #ff4466; }
  .trade.open { box-shadow: 0 0 12px #00f0ff33; border: 1px solid #00f0ff33; }

  .trade .dir {
    font-weight: bold;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
    text-align: center;
  }
  .trade .dir.up { background: #00ff8822; color: #00ff88; }
  .trade .dir.down { background: #ff446622; color: #ff4466; }

  .trade .title { color: #aaa; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .trade .price { text-align: right; color: #ddd; }
  .trade .pnl { text-align: right; font-weight: bold; }
  .trade .pnl.pos { color: #00ff88; }
  .trade .pnl.neg { color: #ff4466; }

  .wave-container {
    height: 60px;
    position: relative;
    overflow: hidden;
    border-top: 1px solid #1a1a3e;
  }
  .wave {
    position: absolute;
    bottom: 0;
    width: 200%;
    height: 100%;
    animation: wave 4s linear infinite;
  }
  .ugag-panel .wave { background: repeating-linear-gradient(90deg, transparent, #8b5cf611 50px, transparent 100px); }
  .us-panel .wave { background: repeating-linear-gradient(90deg, transparent, #00f0ff11 50px, transparent 100px); }
  @keyframes wave { from{transform:translateX(0)} to{transform:translateX(-50%)} }

  .pulse-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    display: inline-block;
    animation: pulse 2s ease-in-out infinite;
  }
  .pulse-dot.live { background: #00ff88; box-shadow: 0 0 8px #00ff88; }
  .pulse-dot.offline { background: #ff4466; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

  .last-update { font-size: 11px; color: #555; text-align: center; padding: 8px; }

  .canvas-container { padding: 10px; }
  canvas { width: 100%; height: 120px; border-radius: 8px; background: #0a0a1a; }
</style>
</head>
<body>
<div class="header">
  <h1>LIVE COPY TRADER TRACKER</h1>
  <div class="stats-bar">
    <div class="stat">ugag P&L: <span class="val" id="ugag-pnl">loading...</span></div>
    <div class="stat">Our P&L: <span class="val" id="our-pnl">loading...</span></div>
    <div class="stat">ugag trades: <span class="val" id="ugag-count">-</span></div>
    <div class="stat">Our trades: <span class="val" id="our-count">-</span></div>
  </div>
</div>

<div class="container">
  <div class="panel ugag-panel">
    <div class="panel-header">
      <h2><span class="pulse-dot live"></span> ugag</h2>
      <span class="badge">DIRECTIONAL BTC</span>
    </div>
    <div class="stats-grid" id="ugag-stats"></div>
    <div class="canvas-container"><canvas id="ugag-chart"></canvas></div>
    <div class="breakdown" id="ugag-breakdown"></div>
    <div class="trade-list" id="ugag-trades"></div>
    <div class="wave-container"><div class="wave"></div></div>
    <div class="last-update" id="ugag-update">waiting...</div>
  </div>

  <div class="panel us-panel">
    <div class="panel-header">
      <h2><span class="pulse-dot" id="our-pulse"></span> Emmanuel</h2>
      <span class="badge">MAKER + DEFENSIVE</span>
    </div>
    <div class="stats-grid" id="our-stats"></div>
    <div class="canvas-container"><canvas id="our-chart"></canvas></div>
    <div class="breakdown" id="our-breakdown"></div>
    <div class="trade-list" id="our-trades"></div>
    <div class="wave-container"><div class="wave"></div></div>
    <div class="last-update" id="our-update">waiting...</div>
  </div>
</div>

<script>
async function fetchData() {
  try {
    const resp = await fetch('/api/trades');
    const data = await resp.json();
    renderStats('ugag-stats', data.ugag_stats, 'ugag');
    renderStats('our-stats', data.our_stats, 'us');
    renderBreakdown('ugag-breakdown', data.ugag_stats, 'ugag');
    renderBreakdown('our-breakdown', data.our_stats, 'us');
    renderUgag(data.ugag);
    renderOurs(data.ours);
    drawChart('ugag-chart', data.ugag, '#8b5cf6');
    drawChart('our-chart', data.ours, '#00f0ff');
    document.getElementById('ugag-pnl').textContent = '$' + (data.ugag_stats?.total_pnl || 0).toFixed(2);
  } catch(e) { console.error('Poll error:', e); }
}

function renderStats(elId, stats, who) {
  if (!stats) return;
  const el = document.getElementById(elId);
  const pnlCls = stats.total_pnl >= 0 ? 'pos' : 'neg';
  const wrColor = stats.wr >= 60 ? 'pos' : stats.wr >= 50 ? 'neutral' : 'neg';
  el.innerHTML =
    '<div class="stat-card"><div class="label">Win Rate</div><div class="value ' + wrColor + '">' + stats.wr + '%</div></div>' +
    '<div class="stat-card"><div class="label">P&L</div><div class="value ' + pnlCls + '">$' + stats.total_pnl + '</div></div>' +
    '<div class="stat-card"><div class="label">Trades</div><div class="value neutral">' + stats.n + '</div></div>' +
    '<div class="stat-card"><div class="label">$/Trade</div><div class="value ' + (stats.avg_pnl >= 0 ? 'pos' : 'neg') + '">$' + stats.avg_pnl + '</div></div>';
}

function renderBreakdown(elId, stats, who) {
  if (!stats) return;
  const el = document.getElementById(elId);
  let html = '<h3>Exit Strategy</h3>';
  const exits = stats.by_exit || {};
  for (const [name, data] of Object.entries(exits).sort((a,b) => b[1].n - a[1].n)) {
    const pct = stats.n > 0 ? Math.round(100 * data.n / stats.n) : 0;
    const pnlCls = data.pnl >= 0 ? 'pos' : 'neg';
    html += '<div class="breakdown-row">' +
      '<span class="name">' + name + '</span>' +
      '<div class="bar-bg"><div class="bar-fill" style="width:' + pct + '%"></div></div>' +
      '<span>' + data.n + ' (' + data.wr + '%)</span>' +
      '<span class="' + pnlCls + '">$' + data.pnl + '</span>' +
    '</div>';
  }
  html += '<h3 style="margin-top:8px">Direction</h3>';
  const dirs = stats.by_direction || {};
  for (const [name, data] of Object.entries(dirs)) {
    if (data.n === 0) continue;
    const pnlCls = data.pnl >= 0 ? 'pos' : 'neg';
    html += '<div class="breakdown-row">' +
      '<span class="name">' + name + '</span>' +
      '<span>' + data.n + ' trades (' + data.wr + '% WR)</span>' +
      '<span class="' + pnlCls + '">$' + data.pnl + '</span>' +
    '</div>';
  }
  el.innerHTML = html;
}

function renderUgag(trades) {
  const el = document.getElementById('ugag-trades');
  const countEl = document.getElementById('ugag-count');
  const updateEl = document.getElementById('ugag-update');
  countEl.textContent = trades.length;

  el.innerHTML = trades.map(t => {
    const dir = (t.outcome || '').toLowerCase();
    const cls = dir === 'up' ? 'up' : 'down';
    return '<div class="trade ' + cls + '">' +
      '<span class="dir ' + cls + '">' + (t.outcome || '?') + '</span>' +
      '<span class="title">' + (t.title || '') + '</span>' +
      '<span class="price">$' + (t.price || 0).toFixed(2) + '</span>' +
      '<span class="pnl">' + (t.size || 0).toFixed(1) + ' sh</span>' +
    '</div>';
  }).join('');

  if (trades.length > 0) {
    const ago = Math.round((Date.now()/1000 - trades[0].timestamp) / 60);
    updateEl.textContent = 'Last trade: ' + ago + ' min ago';
  }
}

function renderOurs(trades) {
  const el = document.getElementById('our-trades');
  const countEl = document.getElementById('our-count');
  const updateEl = document.getElementById('our-update');
  const pnlEl = document.getElementById('our-pnl');
  const pulseEl = document.getElementById('our-pulse');
  countEl.textContent = trades.length;

  const totalPnl = trades.reduce((s, t) => s + (t.pnl || 0), 0);
  pnlEl.textContent = '$' + totalPnl.toFixed(2);
  pnlEl.className = 'val ' + (totalPnl >= 0 ? 'pos' : 'neg');

  const hasOpen = trades.some(t => t.is_open);
  pulseEl.className = 'pulse-dot ' + (hasOpen ? 'live' : 'offline');

  el.innerHTML = trades.map(t => {
    const dir = (t.outcome || '').toLowerCase();
    const cls = dir === 'up' ? 'up' : 'down';
    const openCls = t.is_open ? ' open' : '';
    const pnlCls = (t.pnl || 0) >= 0 ? 'pos' : 'neg';
    const pnlText = t.is_open ? 'OPEN' : '$' + (t.pnl || 0).toFixed(2);
    return '<div class="trade ' + cls + openCls + '">' +
      '<span class="dir ' + cls + '">' + (t.outcome || '?') + '</span>' +
      '<span class="title">' + (t.slug || '') + '</span>' +
      '<span class="price">$' + (t.price || 0).toFixed(2) + '</span>' +
      '<span class="pnl ' + pnlCls + '">' + pnlText + '</span>' +
    '</div>';
  }).join('');

  if (trades.length > 0) {
    const ago = Math.round((Date.now()/1000 - trades[0].timestamp) / 60);
    updateEl.textContent = 'Last trade: ' + ago + ' min ago';
  }
}

function drawChart(canvasId, trades, color) {
  const canvas = document.getElementById(canvasId);
  const ctx = canvas.getContext('2d');
  canvas.width = canvas.offsetWidth * 2;
  canvas.height = 240;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (trades.length < 2) return;

  const prices = trades.map(t => t.price || 0).reverse();
  const maxP = Math.max(...prices, 0.01);
  const minP = Math.min(...prices);
  const range = maxP - minP || 0.01;
  const stepX = canvas.width / (prices.length - 1);

  // Glow effect
  ctx.shadowColor = color;
  ctx.shadowBlur = 12;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  prices.forEach((p, i) => {
    const x = i * stepX;
    const y = canvas.height - ((p - minP) / range) * (canvas.height - 20) - 10;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Fill gradient
  ctx.shadowBlur = 0;
  const last = prices.length - 1;
  ctx.lineTo(last * stepX, canvas.height);
  ctx.lineTo(0, canvas.height);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, canvas.height);
  grad.addColorStop(0, color + '33');
  grad.addColorStop(1, color + '00');
  ctx.fillStyle = grad;
  ctx.fill();

  // Dots
  ctx.shadowColor = color;
  ctx.shadowBlur = 8;
  prices.forEach((p, i) => {
    const x = i * stepX;
    const y = canvas.height - ((p - minP) / range) * (canvas.height - 20) - 10;
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  });
}

// Poll every 5 seconds
fetchData();
setInterval(fetchData, 5000);
</script>
</body>
</html>"""


class TrackerHandler(SimpleHTTPRequestHandler):
    """HTTP handler for the dashboard."""

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode())

        elif parsed.path == "/api/trades":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            payload = {
                "ugag": ugag_trades,
                "ours": our_trades,
                "ugag_stats": compute_stats(ugag_trades, "ugag"),
                "our_stats": compute_stats(our_trades, "us"),
                "ugag_poll": last_ugag_poll,
                "our_poll": last_our_poll,
            }
            self.wfile.write(json.dumps(payload).encode())

        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass  # Suppress request logs


def main():
    global DB_PATH
    parser = argparse.ArgumentParser(description="Live Copy Trader Tracker")
    parser.add_argument("--db", default="/opt/lagbot/instances/emmanuel/data/performance.db",
                       help="Path to performance.db")
    parser.add_argument("--port", type=int, default=3333)
    args = parser.parse_args()
    DB_PATH = args.db

    # Start background poller
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()

    # Initial fetch
    fetch_ugag_trades()
    fetch_our_trades()

    print(f"Live Tracker running at http://localhost:{args.port}")
    print(f"Tracking: ugag ({UGAG_ADDR[:10]}...) vs Emmanuel ({DB_PATH})")
    server = HTTPServer(("0.0.0.0", args.port), TrackerHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
