#!/usr/bin/env python3
"""Trading Daily Digest Agent -- Haiku 4.5 cron agent.

Queries Polymarket (Emmanuel, Polyphemus) and Bulenox DBs.
Produces daily performance summary with anomaly detection.
Escalates to human when pattern needs Opus-level analysis.

Schedule: daily at 06:00 UTC (10pm PST)
Cost: ~$0.01/run
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add parent for imports
sys.path.insert(0, str(Path(__file__).parent))
from agent_framework import (
    ask_haiku, query_db, get_journal_logs, write_finding,
    check_pending_intervention, request_human_intervention,
    send_telegram,
)

# DB paths
EMMANUEL_DB = "/opt/lagbot/instances/emmanuel/data/performance.db"
POLYPHEMUS_DB = "/opt/lagbot/instances/polyphemus/data/performance.db"
BULENOX_DB = "/opt/bulenox/bulenox-bot/data/trades.db"

SYSTEM_PROMPT = """You are a trading operations analyst for Polyphemus, a crypto trading agency.
You analyze daily trading data across 3 bots and produce concise operational reports.

Bots:
- Emmanuel: Polymarket binary options, BTC 5m cheap-side strategy. LIVE.
- Polyphemus: Polymarket binary options, BTC 5m cheap-side + multi-asset. LIVE.
- Bulenox: CME Micro Bitcoin Futures (MBT), FADE 0.3% mean-reversion. Rithmic Test (not yet live).

Your job:
1. Summarize daily P&L, WR, and trade count for each bot
2. Flag anomalies: losing streaks (3+), unusual WR drop, service errors, drawdown warnings
3. Identify patterns that need deeper analysis (these get escalated to human + Opus 4.6)

When you identify something needing escalation, format it as:
ESCALATION: [finding summary]
SUGGESTED_PROMPT: /domain-entry-audit:[operation] [specific question]

Keep reports concise. No fluff. Numbers first, interpretation second."""


def get_polymarket_stats(db_path: str, bot_name: str, days: int = 1) -> dict:
    """Get recent Polymarket bot stats."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    pnl_col = "pnl"

    # Check column name
    try:
        cols = [r["name"] for r in query_db(db_path, "PRAGMA table_info(trades)")]
        if "pnl" not in cols and "profit_loss" in cols:
            pnl_col = "profit_loss"
    except Exception:
        return {"bot": bot_name, "error": "DB not accessible"}

    recent = query_db(db_path, f"""
        SELECT COUNT(*) as n,
            SUM(CASE WHEN {pnl_col} > 0 THEN 1 ELSE 0 END) as wins,
            COALESCE(SUM({pnl_col}), 0) as pnl
        FROM trades
        WHERE exit_time IS NOT NULL AND exit_time > ?
    """, (cutoff,))

    alltime = query_db(db_path, f"""
        SELECT COUNT(*) as n,
            SUM(CASE WHEN {pnl_col} > 0 THEN 1 ELSE 0 END) as wins,
            COALESCE(SUM({pnl_col}), 0) as pnl
        FROM trades WHERE exit_time IS NOT NULL
    """)

    # Streak check
    last_10 = query_db(db_path, f"""
        SELECT {pnl_col} as pnl FROM trades
        WHERE exit_time IS NOT NULL
        ORDER BY exit_time DESC LIMIT 10
    """)
    streak = 0
    for t in last_10:
        if (t["pnl"] or 0) <= 0:
            streak += 1
        else:
            break

    r = recent[0] if recent else {"n": 0, "wins": 0, "pnl": 0}
    a = alltime[0] if alltime else {"n": 0, "wins": 0, "pnl": 0}

    return {
        "bot": bot_name,
        "today": {"trades": r["n"] or 0, "wins": r["wins"] or 0, "pnl": round(r["pnl"] or 0, 2)},
        "alltime": {"trades": a["n"] or 0, "wins": a["wins"] or 0, "pnl": round(a["pnl"] or 0, 2)},
        "losing_streak": streak,
    }


def get_bulenox_stats() -> dict:
    """Get Bulenox bot stats."""
    recent = query_db(BULENOX_DB, """
        SELECT COUNT(*) as n,
            SUM(CASE WHEN pnl_pts > 0 THEN 1 ELSE 0 END) as wins,
            COALESCE(SUM(pnl_pts), 0) as pnl_pts
        FROM trades
    """)

    by_dir = query_db(BULENOX_DB, """
        SELECT direction, COUNT(*) as n,
            SUM(CASE WHEN pnl_pts > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(SUM(pnl_pts), 2) as pnl_pts
        FROM trades GROUP BY direction
    """)

    shadow = query_db(BULENOX_DB, """
        SELECT rejection_reason, COUNT(*) as n,
            ROUND(SUM(pnl_pts), 2) as would_pnl
        FROM shadow_trades WHERE outcome IS NOT NULL
        GROUP BY rejection_reason
    """)

    r = recent[0] if recent else {"n": 0, "wins": 0, "pnl_pts": 0}
    return {
        "bot": "bulenox",
        "total": {"trades": r["n"] or 0, "wins": r["wins"] or 0, "pnl_pts": round(r["pnl_pts"] or 0, 2)},
        "by_direction": {d["direction"]: {"n": d["n"], "wins": d["wins"], "pnl": d["pnl_pts"]} for d in by_dir},
        "shadow_savings": {s["rejection_reason"]: {"n": s["n"], "would_pnl": s["would_pnl"]} for s in shadow},
    }


def get_service_health() -> dict:
    """Check all bot services."""
    services = {}
    for svc in ["lagbot@emmanuel", "lagbot@polyphemus", "bulenox"]:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            )
            status = result.stdout.strip()
        except Exception:
            status = "unknown"

        errors = get_journal_logs(svc, since="24 hours ago", grep="error|traceback|exception")
        error_count = len(errors.strip().split("\n")) if errors.strip() else 0
        services[svc] = {"status": status, "errors_24h": error_count}
    return services


import subprocess

def main():
    agent_name = "trading_digest"

    # Check for pending intervention -- don't run if waiting for human
    pending = check_pending_intervention(agent_name)
    if pending:
        print(f"[{agent_name}] Pending intervention: {pending['id']}. Waiting for human response.")
        return

    # Gather all data
    emmanuel = get_polymarket_stats(EMMANUEL_DB, "emmanuel")
    polyphemus = get_polymarket_stats(POLYPHEMUS_DB, "polyphemus")
    bulenox = get_bulenox_stats()
    health = get_service_health()

    data_summary = json.dumps({
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "emmanuel": emmanuel,
        "polyphemus": polyphemus,
        "bulenox": bulenox,
        "services": health,
    }, indent=2)

    # Ask Haiku for analysis
    analysis = ask_haiku(
        system_prompt=SYSTEM_PROMPT,
        user_message=f"Here is today's trading data. Analyze and report.\n\n{data_summary}",
        max_tokens=1500,
    )

    # Check for escalation requests in the analysis
    if "ESCALATION:" in analysis:
        lines = analysis.split("\n")
        for i, line in enumerate(lines):
            if line.strip().startswith("ESCALATION:"):
                finding = line.replace("ESCALATION:", "").strip()
                suggested = ""
                if i + 1 < len(lines) and "SUGGESTED_PROMPT:" in lines[i + 1]:
                    suggested = lines[i + 1].replace("SUGGESTED_PROMPT:", "").strip()
                request_human_intervention(
                    agent_name=agent_name,
                    finding=finding,
                    suggested_prompt=suggested or f"/domain-entry-audit:investigate {finding}",
                    context=data_summary,
                    urgency="normal",
                )
                break

    # Write finding for dashboard
    write_finding(
        agent_name=agent_name,
        category="daily_digest",
        title=f"Trading Digest {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        body=analysis,
        severity="info",
        data={"emmanuel": emmanuel, "polyphemus": polyphemus, "bulenox": bulenox},
    )

    # Send Telegram summary (truncated)
    short = analysis[:500] + "..." if len(analysis) > 500 else analysis
    send_telegram(f"\U0001f4ca *Daily Trading Digest*\n\n{short}")

    print(f"[{agent_name}] Digest complete.")


if __name__ == "__main__":
    main()
