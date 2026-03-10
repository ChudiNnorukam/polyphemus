#!/usr/bin/env python3
"""OpenClaw LLM Optimizer — Agentic RAG system for bot-wide self-improvement.

Reads ALL bot data sources (trades, signals, MM observations, regime context,
config history), builds a structured context, and uses Claude API to:
1. Find non-obvious patterns across strategies
2. Recommend config parameter changes with evidence
3. Detect regime shifts that require strategy adjustment
4. Identify missed opportunities and false positives

Data sources (RAG retrieval layer):
- performance.db: trade outcomes per instance
- signals.db: all signals with features and guard results
- mm_observations.db: every pair-cost scan from market maker
- lagbot_context.json: current market regime (F&G, OI, volatility)
- strategy_changelog.md: history of config changes and their outcomes
- .env files: current live config per instance

Usage:
    python3 llm_optimizer.py analyze          # Full analysis + recommendations
    python3 llm_optimizer.py analyze --hours 168  # 7-day lookback
    python3 llm_optimizer.py mm               # Market maker specific analysis
    python3 llm_optimizer.py snipe            # Snipe strategy analysis
    python3 llm_optimizer.py regime           # Regime shift detection
    python3 llm_optimizer.py drift            # Config drift detection

Outputs recommendations to /opt/openclaw/data/evolution/llm_recommendations_YYYY-MM-DD.json
and posts summary to Slack.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- Config ---

DATA_DIR = os.environ.get("OPENCLAW_DATA_DIR", "/opt/openclaw/data")
EVOLUTION_DIR = os.path.join(DATA_DIR, "evolution")
INSTANCES_DIR = os.environ.get("LAGBOT_INSTANCES_DIR", "/opt/lagbot/instances")
LEARNINGS_DIR = os.path.join(DATA_DIR, "learnings")
CHANGELOG_PATH = os.path.join(EVOLUTION_DIR, "strategy_changelog.md")

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

INSTANCES = ["emmanuel", "polyphemus"]
LOOKBACK_HOURS = 24


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr)


def _r8_label(n: int) -> str:
    if n < 30:
        return f"ANECDOTAL n={n}"
    elif n < 107:
        return f"LOW n={n}"
    elif n < 385:
        return f"MODERATE n={n}"
    return f"SIGNIFICANT n={n}"


# ========================================================================
# RAG RETRIEVAL LAYER — collect all data sources into structured context
# ========================================================================

def _connect(db_path: str):
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def retrieve_trades(instance: str, hours: int) -> list:
    since = time.time() - (hours * 3600)
    db = os.path.join(INSTANCES_DIR, instance, "data", "performance.db")
    conn = _connect(db)
    if not conn:
        return []
    try:
        rows = conn.execute("""
            SELECT slug, entry_price, exit_price, pnl, entry_time, exit_time,
                   entry_size, exit_reason, outcome, strategy, metadata
            FROM trades WHERE exit_time IS NOT NULL AND entry_time >= ?
            ORDER BY entry_time
        """, (since,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def retrieve_signals(instance: str, hours: int) -> list:
    since = time.time() - (hours * 3600)
    db = os.path.join(INSTANCES_DIR, instance, "data", "signals.db")
    conn = _connect(db)
    if not conn:
        return []
    try:
        rows = conn.execute("""
            SELECT asset, source, direction, momentum_pct, midpoint,
                   time_remaining_secs, hour_utc, guard_passed, guard_reasons,
                   outcome, pnl, is_win, dry_run, pair_cost, slug, epoch
            FROM signals WHERE epoch >= ?
            ORDER BY epoch
        """, (since,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def retrieve_mm_observations(instance: str, hours: int) -> list:
    db = os.path.join(INSTANCES_DIR, instance, "data", "mm_observations.db")
    conn = _connect(db)
    if not conn:
        return []
    since = time.time() - (hours * 3600)
    try:
        rows = conn.execute("""
            SELECT asset, window_secs, secs_remaining, ask_up, ask_down,
                   pair_cost, profit_per_share, liq_up, liq_down,
                   fear_greed, volatility_1h, market_regime,
                   threshold_used, is_opportunity, hour_utc, timestamp
            FROM observations WHERE epoch > ?
            ORDER BY epoch
        """, (since,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def retrieve_config(instance: str) -> dict:
    env_path = os.path.join(INSTANCES_DIR, instance, ".env")
    if not os.path.exists(env_path):
        return {}
    config = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    return config


def retrieve_market_context() -> dict:
    ctx_path = os.path.join(DATA_DIR, "lagbot_context.json")
    try:
        with open(ctx_path) as f:
            return json.load(f)
    except Exception:
        return {}


def retrieve_changelog() -> str:
    if os.path.exists(CHANGELOG_PATH):
        with open(CHANGELOG_PATH) as f:
            return f.read()[-3000:]  # Last 3K chars
    return ""


def retrieve_past_learnings() -> list:
    """Load past LLM recommendations for cross-session RAG."""
    learnings = []
    pattern = os.path.join(EVOLUTION_DIR, "llm_recommendations_*.json")
    import glob
    for f in sorted(glob.glob(pattern))[-5:]:  # Last 5 sessions
        try:
            with open(f) as fh:
                data = json.load(fh)
                learnings.append({
                    "date": os.path.basename(f).replace("llm_recommendations_", "").replace(".json", ""),
                    "recommendations": data.get("recommendations", []),
                    "applied": data.get("applied", []),
                })
        except Exception:
            pass
    return learnings


# ========================================================================
# LLM ANALYSIS LAYER — structured prompts for each analysis type
# ========================================================================

def _call_claude(system: str, prompt: str, max_tokens: int = 4000) -> str:
    """Call Claude API. Returns response text or error string."""
    if not ANTHROPIC_API_KEY:
        return "[ERROR: ANTHROPIC_API_KEY not set]"

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
            return data["content"][0]["text"]
    except urllib.error.HTTPError as e:
        body = e.read().decode() if hasattr(e, 'read') else ""
        return f"[API ERROR {e.code}]: {body[:500]}"
    except Exception as e:
        return f"[ERROR]: {e}"


SYSTEM_PROMPT = """You are an expert quantitative trading analyst reviewing a Polymarket binary options bot.

The bot trades crypto 5m/15m Up/Down markets via latency arbitrage (Binance momentum -> Polymarket).
It also has a market maker module that does risk-free pair-cost arbitrage (buy BOTH sides when pair_cost < $1.00).

Key constraints:
- At 0.95 entry price: win=$2.63, loss=$50.00 (19:1 ratio). Need 95%+ WR to break even.
- Maker orders have 0% fee. Taker FOK orders have dynamic fees.
- Bot runs on 2 instances: emmanuel (~$1,373 balance) and polyphemus (~$288 balance).

Your job: analyze the data and produce SPECIFIC, ACTIONABLE recommendations.
Each recommendation must include:
1. Parameter name and current vs recommended value
2. Evidence from the data (cite specific numbers)
3. Expected impact ($ or % improvement)
4. Confidence level (HIGH/MEDIUM/LOW)

Do NOT recommend changes with < 30 trades of evidence (R8 rule).
Do NOT recommend removing safety guards.
Output valid JSON with a "recommendations" array."""


def build_full_analysis_prompt(hours: int) -> str:
    """Build the comprehensive analysis prompt with all data sources."""
    sections = []

    for inst in INSTANCES:
        trades = retrieve_trades(inst, hours)
        signals = retrieve_signals(inst, hours)
        mm_obs = retrieve_mm_observations(inst, hours)
        config = retrieve_config(inst)

        # Summarize trades
        if trades:
            wins = [t for t in trades if (t.get("pnl") or 0) > 0]
            losses = [t for t in trades if (t.get("pnl") or 0) < 0]
            total_pnl = sum(t.get("pnl") or 0 for t in trades)
            wr = len(wins) / len(trades) * 100 if trades else 0

            # Bucket by entry price
            price_buckets = {}
            for t in trades:
                ep = t.get("entry_price", 0)
                bucket = f"{int(ep*100)/100:.2f}" if ep else "?"
                price_buckets.setdefault(bucket, {"n": 0, "wins": 0, "pnl": 0})
                price_buckets[bucket]["n"] += 1
                if (t.get("pnl") or 0) > 0:
                    price_buckets[bucket]["wins"] += 1
                price_buckets[bucket]["pnl"] += t.get("pnl") or 0

            # Bucket by hour
            hour_buckets = {}
            for t in trades:
                et = t.get("entry_time")
                if et:
                    h = int(datetime.fromtimestamp(et, tz=timezone.utc).strftime("%H"))
                    hour_buckets.setdefault(h, {"n": 0, "wins": 0, "pnl": 0})
                    hour_buckets[h]["n"] += 1
                    if (t.get("pnl") or 0) > 0:
                        hour_buckets[h]["wins"] += 1
                    hour_buckets[h]["pnl"] += t.get("pnl") or 0

            sections.append(f"""
## {inst.upper()} TRADES ({len(trades)} trades, {hours}h)
WR: {wr:.1f}% | PnL: ${total_pnl:+.2f} | Wins: {len(wins)} | Losses: {len(losses)} [{_r8_label(len(trades))}]
By entry price: {json.dumps(price_buckets)}
By hour UTC: {json.dumps(hour_buckets)}""")

        # Summarize signals
        if signals:
            passed = sum(1 for s in signals if s.get("guard_passed") == 1)
            filtered = sum(1 for s in signals if s.get("guard_passed") == 0)
            dry = sum(1 for s in signals if s.get("dry_run") == 1)
            reasons = {}
            for s in signals:
                for r in (s.get("guard_reasons") or "").split(","):
                    r = r.strip()
                    if r:
                        reasons[r] = reasons.get(r, 0) + 1

            # Snipe signals specifically
            snipe_sigs = [s for s in signals if "snipe" in (s.get("source") or "")]
            snipe_wins = sum(1 for s in snipe_sigs if s.get("is_win") == 1)
            snipe_losses = sum(1 for s in snipe_sigs if s.get("is_win") == 0 and s.get("pnl") is not None)

            sections.append(f"""
## {inst.upper()} SIGNALS ({len(signals)} total)
Passed: {passed} | Filtered: {filtered} | Dry run: {dry}
Top filter reasons: {json.dumps(dict(sorted(reasons.items(), key=lambda x: -x[1])[:5]))}
Snipe signals: {len(snipe_sigs)} (wins={snipe_wins}, losses={snipe_losses})""")

        # Summarize MM observations
        if mm_obs:
            opps = sum(1 for o in mm_obs if o.get("is_opportunity") == 1)
            costs = [o["pair_cost"] for o in mm_obs if o.get("pair_cost")]
            min_cost = min(costs) if costs else 0
            avg_cost = sum(costs) / len(costs) if costs else 0
            below_995 = sum(1 for c in costs if c < 0.995)
            below_990 = sum(1 for c in costs if c < 0.990)

            sections.append(f"""
## {inst.upper()} MARKET MAKER ({len(mm_obs)} scans)
Opportunities: {opps} ({opps/len(mm_obs)*100:.1f}% rate)
Pair cost: min={min_cost:.4f}, avg={avg_cost:.4f}
Below 0.995: {below_995} | Below 0.990: {below_990}""")

        # Key config values
        if config:
            key_params = [
                "SNIPE_MIN_ENTRY_PRICE", "SNIPE_MAX_ENTRY_PRICE",
                "SNIPE_MAX_SECS_REMAINING", "SNIPE_MAX_PER_EPOCH",
                "BASE_BET_PCT", "MAX_BET", "MAX_DAILY_LOSS",
                "DANGER_HOURS", "DANGER_HOURS_SIZE_MULT",
                "MM_MAX_PAIR_COST", "MM_DRY_RUN", "MM_MAX_BET",
                "SNIPE_15M_DRY_RUN", "SNIPE_15M_MIN_ENTRY_PRICE",
                "SNIPE_15M_MAX_ENTRY_PRICE",
            ]
            cfg_str = {k: config.get(k, "not set") for k in key_params}
            sections.append(f"\n## {inst.upper()} CONFIG\n{json.dumps(cfg_str, indent=2)}")

    # Market context
    ctx = retrieve_market_context()
    if ctx:
        sections.append(f"\n## MARKET CONTEXT\n{json.dumps(ctx, indent=2)[:1000]}")

    # Past learnings (RAG cross-session)
    past = retrieve_past_learnings()
    if past:
        sections.append(f"\n## PAST LLM RECOMMENDATIONS (last {len(past)} sessions)")
        for p in past:
            recs = p.get("recommendations", [])
            applied = p.get("applied", [])
            sections.append(f"- {p['date']}: {len(recs)} recs, {len(applied)} applied")
            for r in recs[:3]:
                sections.append(f"  - {r.get('param', '?')}: {r.get('summary', '?')}")

    # Changelog
    changelog = retrieve_changelog()
    if changelog:
        sections.append(f"\n## RECENT CONFIG CHANGES\n{changelog[-1500:]}")

    return "\n".join(sections)


def build_mm_prompt(hours: int) -> str:
    """Market maker specific analysis prompt."""
    sections = ["Analyze the market maker (pair-cost arbitrage) data specifically.\n"]
    for inst in INSTANCES:
        mm_obs = retrieve_mm_observations(inst, hours)
        if not mm_obs:
            sections.append(f"{inst}: No MM observations yet.\n")
            continue

        # Time distribution of pair costs
        hour_costs = {}
        for o in mm_obs:
            h = o.get("hour_utc", 0)
            hour_costs.setdefault(h, []).append(o["pair_cost"])

        hour_summary = {}
        for h, costs in sorted(hour_costs.items()):
            n = len(costs)
            hour_summary[h] = {
                "n": n, "min": round(min(costs), 4),
                "avg": round(sum(costs)/n, 4),
                "below_995": sum(1 for c in costs if c < 0.995),
            }

        # Asset distribution
        asset_costs = {}
        for o in mm_obs:
            a = o.get("asset", "?")
            asset_costs.setdefault(a, []).append(o["pair_cost"])

        asset_summary = {}
        for a, costs in asset_costs.items():
            n = len(costs)
            asset_summary[a] = {
                "n": n, "min": round(min(costs), 4),
                "avg": round(sum(costs)/n, 4),
                "below_995": sum(1 for c in costs if c < 0.995),
            }

        # Regime correlation
        regime_costs = {}
        for o in mm_obs:
            r = o.get("market_regime") or "unknown"
            regime_costs.setdefault(r, []).append(o["pair_cost"])

        sections.append(f"""
## {inst.upper()} MM DATA ({len(mm_obs)} observations, {hours}h)
By hour UTC: {json.dumps(hour_summary)}
By asset: {json.dumps(asset_summary)}
By regime: {json.dumps({r: {"n": len(c), "min": round(min(c), 4)} for r, c in regime_costs.items()})}
""")

    sections.append("""
Questions to answer:
1. What is the optimal MM_MAX_PAIR_COST threshold? (currently 0.995)
2. Which hours have the most pair_cost < 1.00 opportunities?
3. Which assets have tightest pair costs?
4. Does market regime (F&G, volatility) predict opportunities?
5. Should we scan wider time windows (increase MM_MAX_SECS_REMAINING)?
6. Is the opportunity rate high enough to justify going live?
""")
    return "\n".join(sections)


def build_snipe_prompt(hours: int) -> str:
    """Snipe strategy specific analysis."""
    sections = ["Analyze the resolution snipe strategy data specifically.\n"]
    for inst in INSTANCES:
        signals = retrieve_signals(inst, hours)
        snipe = [s for s in signals if "snipe" in (s.get("source") or "")]
        if not snipe:
            sections.append(f"{inst}: No snipe signals.\n")
            continue

        # By time remaining
        time_buckets = {}
        for s in snipe:
            tr = s.get("time_remaining_secs") or 0
            bucket = f"{(tr//5)*5}-{(tr//5)*5+5}s"
            time_buckets.setdefault(bucket, {"n": 0, "wins": 0})
            time_buckets[bucket]["n"] += 1
            if s.get("is_win") == 1:
                time_buckets[bucket]["wins"] += 1

        # By asset
        asset_buckets = {}
        for s in snipe:
            a = s.get("asset", "?")
            asset_buckets.setdefault(a, {"n": 0, "wins": 0, "pnl": 0})
            asset_buckets[a]["n"] += 1
            if s.get("is_win") == 1:
                asset_buckets[a]["wins"] += 1
            asset_buckets[a]["pnl"] += s.get("pnl") or 0

        sections.append(f"""
## {inst.upper()} SNIPE ({len(snipe)} signals, {hours}h)
By time remaining: {json.dumps(time_buckets)}
By asset: {json.dumps(asset_buckets)}
5m vs 15m: 5m={sum(1 for s in snipe if '5m' in (s.get('slug') or ''))} 15m={sum(1 for s in snipe if '15m' in (s.get('slug') or ''))}
Live vs dry: live={sum(1 for s in snipe if not s.get('dry_run'))} dry={sum(1 for s in snipe if s.get('dry_run'))}
""")

    sections.append("""
Questions:
1. What is the optimal SNIPE_MAX_SECS_REMAINING? (currently 10s for 5m)
2. Which assets have best snipe WR?
3. Is 15m snipe showing enough opportunities to go live?
4. Should entry price range be adjusted?
""")
    return "\n".join(sections)


# ========================================================================
# OUTPUT LAYER — save recommendations and post to Slack
# ========================================================================

def save_recommendations(analysis: str, mode: str):
    """Parse LLM output and save structured recommendations."""
    os.makedirs(EVOLUTION_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = os.path.join(EVOLUTION_DIR, f"llm_recommendations_{today}.json")

    # Try to parse JSON from response
    recs = []
    try:
        # Find JSON block in response
        start = analysis.find("[")
        end = analysis.rfind("]") + 1
        if start >= 0 and end > start:
            recs = json.loads(analysis[start:end])
    except json.JSONDecodeError:
        pass

    output = {
        "date": today,
        "mode": mode,
        "raw_analysis": analysis,
        "recommendations": recs,
        "applied": [],
    }

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    _log(f"Saved {len(recs)} recommendations to {out_path}")
    return out_path


def post_to_slack(text: str):
    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        return
    payload = json.dumps({
        "channel": SLACK_CHANNEL_ID,
        "text": text[:3000],
    }).encode()
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        _log(f"Slack post failed: {e}")


# ========================================================================
# MAIN
# ========================================================================

def run_analysis(mode: str, hours: int):
    _log(f"Starting LLM optimizer: mode={mode}, lookback={hours}h")

    if mode == "analyze":
        prompt = build_full_analysis_prompt(hours)
    elif mode == "mm":
        prompt = build_mm_prompt(hours)
    elif mode == "snipe":
        prompt = build_snipe_prompt(hours)
    elif mode == "regime":
        prompt = build_full_analysis_prompt(hours)
        prompt += "\n\nFocus ONLY on regime shifts. Has market behavior changed? Should config adapt?"
    elif mode == "drift":
        prompt = build_full_analysis_prompt(hours)
        prompt += "\n\nFocus ONLY on config drift. Compare current config to recent performance. Any mismatches?"
    else:
        _log(f"Unknown mode: {mode}")
        return

    _log(f"Built prompt: {len(prompt)} chars")
    _log("Calling Claude API...")

    analysis = _call_claude(SYSTEM_PROMPT, prompt)
    _log(f"Got response: {len(analysis)} chars")

    out_path = save_recommendations(analysis, mode)

    # Print summary
    print(f"\n{'='*60}")
    print(f"LLM OPTIMIZER — {mode.upper()} ({hours}h)")
    print(f"{'='*60}")
    print(analysis[:3000])
    if len(analysis) > 3000:
        print(f"\n... ({len(analysis) - 3000} chars truncated, full output: {out_path})")

    # Slack digest
    slack_msg = f"*LLM Optimizer [{mode}]*\n{analysis[:1500]}"
    post_to_slack(slack_msg)


def main():
    parser = argparse.ArgumentParser(description="OpenClaw LLM Optimizer")
    parser.add_argument("mode", choices=["analyze", "mm", "snipe", "regime", "drift"],
                        default="analyze", nargs="?")
    parser.add_argument("--hours", type=int, default=LOOKBACK_HOURS)
    args = parser.parse_args()
    run_analysis(args.mode, args.hours)


if __name__ == "__main__":
    main()
