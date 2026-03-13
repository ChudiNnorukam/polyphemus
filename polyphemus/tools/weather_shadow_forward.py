#!/usr/bin/env python3
"""Forward weather shadow collector for live Polymarket weather markets.

This tool is intentionally standalone and stdlib-only. It records live scan
observations for multiple weather profiles, opens simulated shadow positions
when thresholds pass, and later resolves those positions against the final
market winner.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "weather_shadow_forward.db"
DEFAULT_OUTPUT_PREFIX = ROOT.parent / "dario_output" / f"weather_shadow_forward_{datetime.now(timezone.utc).strftime('%Y%m%d')}"


def load_replay_module():
    module_path = Path(__file__).with_name("weather_shadow_replay.py")
    spec = importlib.util.spec_from_file_location("weather_shadow_replay", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load replay helpers from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


REPLAY = load_replay_module()


PROFILES = [
    REPLAY.ReplayProfile(
        name="yes_only_take_profit",
        weather_entry_max_price=0.15,
        weather_exit_min_price=0.45,
        weather_noaa_min_prob=0.70,
        weather_min_edge=0.08,
        weather_allow_complement=False,
        weather_hold_to_resolution=False,
        weather_max_spend=2.0,
        weather_max_open_positions=5,
    ),
    REPLAY.ReplayProfile(
        name="yes_or_no_hold_to_resolution",
        weather_entry_max_price=0.15,
        weather_exit_min_price=0.45,
        weather_noaa_min_prob=0.70,
        weather_min_edge=0.08,
        weather_allow_complement=True,
        weather_hold_to_resolution=True,
        weather_max_spend=2.0,
        weather_max_open_positions=5,
    ),
    REPLAY.ReplayProfile(
        name="coldmath_tail_hold_to_resolution",
        weather_entry_max_price=0.03,
        weather_exit_min_price=0.45,
        weather_noaa_min_prob=0.85,
        weather_min_edge=0.25,
        weather_allow_complement=True,
        weather_hold_to_resolution=True,
        weather_max_spend=20.0,
        weather_max_open_positions=20,
    ),
]


def ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_ts INTEGER NOT NULL,
            scan_iso TEXT NOT NULL,
            forecast_run_iso TEXT NOT NULL,
            profile TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            event_slug TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            question TEXT NOT NULL,
            yes_price REAL,
            no_price REAL,
            yes_prob REAL,
            no_prob REAL,
            selected_outcome TEXT,
            selected_price REAL,
            selected_prob REAL,
            selected_edge REAL,
            hours_left REAL,
            target_high REAL,
            decision INTEGER NOT NULL DEFAULT 0,
            decision_reason TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scan_ts, profile, market_slug)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            event_slug TEXT NOT NULL,
            market_slug TEXT NOT NULL,
            question TEXT NOT NULL,
            outcome TEXT NOT NULL,
            entry_scan_ts INTEGER NOT NULL,
            entry_scan_iso TEXT NOT NULL,
            forecast_run_iso TEXT NOT NULL,
            entry_price REAL NOT NULL,
            model_prob REAL NOT NULL,
            edge REAL NOT NULL,
            spend REAL NOT NULL,
            shares REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            resolve_checked_iso TEXT,
            resolved_winner TEXT,
            exit_price REAL,
            exit_ts INTEGER,
            exit_iso TEXT,
            exit_reason TEXT,
            pnl REAL,
            roi_pct REAL,
            UNIQUE(profile, market_slug)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_positions_status ON shadow_positions(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_positions_profile ON shadow_positions(profile)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_observations_profile_scan_ts ON scan_observations(profile, scan_ts)")
    conn.commit()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Forward weather shadow collector.")
    parser.add_argument("mode", choices=["scan", "resolve", "report", "all"], nargs="?", default="all")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--cities", default="Atlanta,Dallas,London,Paris,Wellington,Lucknow,Ankara")
    parser.add_argument("--future-days", type=int, default=3)
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    return parser.parse_args(argv)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def active_open_count(conn: sqlite3.Connection, profile_name: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM shadow_positions WHERE profile = ? AND status = 'open'",
        (profile_name,),
    ).fetchone()
    return int(row[0] if row else 0)


def has_position(conn: sqlite3.Connection, profile_name: str, market_slug: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM shadow_positions WHERE profile = ? AND market_slug = ? LIMIT 1",
        (profile_name, market_slug),
    ).fetchone()
    return row is not None


def latest_run_dt(now: datetime) -> datetime:
    run_hour = (now.hour // 6) * 6
    run_dt = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
    if run_dt > now:
        run_dt -= timedelta(hours=6)
    return run_dt


def insert_observation(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO scan_observations (
            scan_ts, scan_iso, forecast_run_iso, profile, city, target_date, event_slug,
            market_slug, question, yes_price, no_price, yes_prob, no_prob, selected_outcome,
            selected_price, selected_prob, selected_edge, hours_left, target_high, decision,
            decision_reason
        ) VALUES (
            :scan_ts, :scan_iso, :forecast_run_iso, :profile, :city, :target_date, :event_slug,
            :market_slug, :question, :yes_price, :no_price, :yes_prob, :no_prob, :selected_outcome,
            :selected_price, :selected_prob, :selected_edge, :hours_left, :target_high, :decision,
            :decision_reason
        )
        """,
        row,
    )


def insert_position(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO shadow_positions (
            profile, city, target_date, event_slug, market_slug, question, outcome,
            entry_scan_ts, entry_scan_iso, forecast_run_iso, entry_price, model_prob, edge,
            spend, shares, status
        ) VALUES (
            :profile, :city, :target_date, :event_slug, :market_slug, :question, :outcome,
            :entry_scan_ts, :entry_scan_iso, :forecast_run_iso, :entry_price, :model_prob, :edge,
            :spend, :shares, 'open'
        )
        """,
        row,
    )


def scan_markets(conn: sqlite3.Connection, cities: Sequence[str], future_days: int) -> dict:
    now = utc_now()
    run_dt = latest_run_dt(now)
    forecast_cache: dict = {}
    scan_ts = int(now.timestamp())
    scan_iso = now.isoformat()
    summary = {
        "scan_ts": scan_ts,
        "scan_iso": scan_iso,
        "forecast_run_iso": run_dt.isoformat(),
        "markets_seen": 0,
        "observations_written": 0,
        "positions_opened": 0,
        "profiles": {profile.name: {"observations": 0, "decisions": 0, "opened": 0} for profile in PROFILES},
    }

    for days_ahead in range(future_days):
        target_day = (now + timedelta(days=days_ahead)).date()
        for city in cities:
            event = REPLAY.fetch_event(city, target_day)
            if not event:
                continue
            event_end = REPLAY.parse_iso_dt(event["endDate"])
            if event_end <= now:
                continue
            target_high = REPLAY.extract_daily_max_from_run(city, run_dt, target_day, forecast_cache)
            if target_high is None:
                continue
            hours_left = max(1.0, (event_end - now).total_seconds() / 3600.0)
            for market in event.get("markets", []):
                cond = REPLAY.parse_temp_condition(market.get("question", ""), market.get("groupItemTitle", ""))
                if not cond:
                    continue
                summary["markets_seen"] += 1
                yes_prob = REPLAY.compute_prob(target_high, cond, hours_left)
                no_prob = REPLAY.round6(1.0 - yes_prob)
                try:
                    outcome_prices = [float(x) for x in json.loads(market.get("outcomePrices", "[]"))]
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                yes_price = outcome_prices[0] if len(outcome_prices) > 0 else None
                no_price = outcome_prices[1] if len(outcome_prices) > 1 else None
                for profile in PROFILES:
                    chosen = REPLAY.evaluate_market_candidate(profile, yes_price, no_price, yes_prob)
                    decision = 0
                    decision_reason = "below_threshold"
                    selected_outcome = None
                    selected_price = None
                    selected_prob = None
                    selected_edge = None
                    if chosen:
                        selected_outcome, selected_price, selected_prob = chosen
                        selected_edge = REPLAY.round6(selected_prob - selected_price)
                        decision_reason = "candidate"
                        if has_position(conn, profile.name, market.get("slug", "")):
                            decision_reason = "existing_position"
                        elif active_open_count(conn, profile.name) >= profile.weather_max_open_positions:
                            decision_reason = "max_open_positions"
                        else:
                            decision = 1
                            insert_position(
                                conn,
                                {
                                    "profile": profile.name,
                                    "city": city,
                                    "target_date": target_day.isoformat(),
                                    "event_slug": event.get("slug", ""),
                                    "market_slug": market.get("slug", ""),
                                    "question": market.get("question", ""),
                                    "outcome": selected_outcome,
                                    "entry_scan_ts": scan_ts,
                                    "entry_scan_iso": scan_iso,
                                    "forecast_run_iso": run_dt.isoformat(),
                                    "entry_price": selected_price,
                                    "model_prob": selected_prob,
                                    "edge": selected_edge,
                                    "spend": profile.weather_max_spend,
                                    "shares": profile.weather_max_spend / selected_price,
                                },
                            )
                            summary["positions_opened"] += 1
                            summary["profiles"][profile.name]["opened"] += 1
                    insert_observation(
                        conn,
                        {
                            "scan_ts": scan_ts,
                            "scan_iso": scan_iso,
                            "forecast_run_iso": run_dt.isoformat(),
                            "profile": profile.name,
                            "city": city,
                            "target_date": target_day.isoformat(),
                            "event_slug": event.get("slug", ""),
                            "market_slug": market.get("slug", ""),
                            "question": market.get("question", ""),
                            "yes_price": yes_price,
                            "no_price": no_price,
                            "yes_prob": yes_prob,
                            "no_prob": no_prob,
                            "selected_outcome": selected_outcome,
                            "selected_price": selected_price,
                            "selected_prob": selected_prob,
                            "selected_edge": selected_edge,
                            "hours_left": REPLAY.round6(hours_left),
                            "target_high": target_high,
                            "decision": decision,
                            "decision_reason": decision_reason,
                        },
                    )
                    summary["observations_written"] += 1
                    summary["profiles"][profile.name]["observations"] += 1
                    if chosen:
                        summary["profiles"][profile.name]["decisions"] += 1
    conn.commit()
    return summary


def resolve_positions(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT id, profile, city, target_date, event_slug, market_slug, question, outcome,
               entry_price, spend, shares
        FROM shadow_positions
        WHERE status = 'open'
        ORDER BY entry_scan_ts ASC
        """
    ).fetchall()
    summary = {"open_checked": len(rows), "resolved": 0, "still_open": 0}
    now_iso = utc_now().isoformat()
    for row in rows:
        target_day = date.fromisoformat(row["target_date"])
        event = REPLAY.fetch_event(row["city"], target_day)
        if not event or not event.get("closed", False):
            conn.execute(
                "UPDATE shadow_positions SET resolve_checked_iso = ? WHERE id = ?",
                (now_iso, row["id"]),
            )
            summary["still_open"] += 1
            continue
        market = next((m for m in event.get("markets", []) if m.get("slug") == row["market_slug"]), None)
        if not market:
            conn.execute(
                "UPDATE shadow_positions SET resolve_checked_iso = ? WHERE id = ?",
                (now_iso, row["id"]),
            )
            summary["still_open"] += 1
            continue
        winner = REPLAY.winner_for_market(market)
        if winner not in {"Yes", "No"}:
            conn.execute(
                "UPDATE shadow_positions SET resolve_checked_iso = ? WHERE id = ?",
                (now_iso, row["id"]),
            )
            summary["still_open"] += 1
            continue
        win = winner == row["outcome"]
        exit_price = 1.0 if win else 0.0
        pnl = REPLAY.round6(float(row["shares"]) * (exit_price - float(row["entry_price"])))
        roi_pct = REPLAY.round6((pnl / float(row["spend"])) * 100.0 if row["spend"] else 0.0)
        exit_iso = None
        exit_ts = None
        for key in ("closedTime", "umaEndDate", "endDate"):
            raw = market.get(key)
            if not raw:
                continue
            try:
                exit_dt = REPLAY.parse_iso_dt(str(raw).replace(" ", "T"))
                exit_iso = exit_dt.isoformat()
                exit_ts = int(exit_dt.timestamp())
                break
            except ValueError:
                continue
        conn.execute(
            """
            UPDATE shadow_positions
            SET status = 'resolved',
                resolve_checked_iso = ?,
                resolved_winner = ?,
                exit_price = ?,
                exit_ts = ?,
                exit_iso = ?,
                exit_reason = 'resolution',
                pnl = ?,
                roi_pct = ?
            WHERE id = ?
            """,
            (now_iso, winner, exit_price, exit_ts, exit_iso, pnl, roi_pct, row["id"]),
        )
        summary["resolved"] += 1
    conn.commit()
    summary["still_open"] = summary["open_checked"] - summary["resolved"]
    return summary


def profile_summary(conn: sqlite3.Connection) -> List[dict]:
    rows = conn.execute(
        """
        SELECT
            profile,
            COUNT(*) AS total_positions,
            SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_positions,
            SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) AS resolved_positions,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN pnl <= 0 AND pnl IS NOT NULL THEN 1 ELSE 0 END) AS losses,
            COALESCE(SUM(pnl), 0) AS total_pnl,
            COALESCE(AVG(entry_price), 0) AS avg_entry_price,
            COALESCE(AVG(roi_pct), 0) AS avg_roi_pct
        FROM shadow_positions
        GROUP BY profile
        ORDER BY profile
        """
    ).fetchall()
    data = []
    for row in rows:
        resolved = int(row["resolved_positions"] or 0)
        wins = int(row["wins"] or 0)
        data.append(
            {
                "profile": row["profile"],
                "total_positions": int(row["total_positions"] or 0),
                "open_positions": int(row["open_positions"] or 0),
                "resolved_positions": resolved,
                "wins": wins,
                "losses": int(row["losses"] or 0),
                "win_rate": REPLAY.round6((wins / resolved) if resolved else 0.0),
                "total_pnl": REPLAY.round6(row["total_pnl"] or 0.0),
                "avg_entry_price": REPLAY.round6(row["avg_entry_price"] or 0.0),
                "avg_roi_pct": REPLAY.round6(row["avg_roi_pct"] or 0.0),
            }
        )
    return data


def latest_candidates(conn: sqlite3.Connection, limit: int = 15) -> List[dict]:
    row = conn.execute("SELECT MAX(scan_ts) FROM scan_observations").fetchone()
    if not row or row[0] is None:
        return []
    scan_ts = int(row[0])
    rows = conn.execute(
        """
        SELECT profile, city, target_date, question, selected_outcome, selected_price,
               selected_prob, selected_edge, decision, decision_reason
        FROM scan_observations
        WHERE scan_ts = ? AND selected_outcome IS NOT NULL
        ORDER BY selected_edge DESC, selected_prob DESC
        LIMIT ?
        """,
        (scan_ts, limit),
    ).fetchall()
    return [
        {
            "profile": row["profile"],
            "city": row["city"],
            "target_date": row["target_date"],
            "question": row["question"],
            "outcome": row["selected_outcome"],
            "price": REPLAY.round6(row["selected_price"] or 0.0),
            "prob": REPLAY.round6(row["selected_prob"] or 0.0),
            "edge": REPLAY.round6(row["selected_edge"] or 0.0),
            "decision": int(row["decision"] or 0),
            "decision_reason": row["decision_reason"],
        }
        for row in rows
    ]


def open_positions(conn: sqlite3.Connection, limit: int = 15) -> List[dict]:
    rows = conn.execute(
        """
        SELECT profile, city, target_date, question, outcome, entry_price, model_prob, edge,
               spend, entry_scan_iso
        FROM shadow_positions
        WHERE status = 'open'
        ORDER BY entry_scan_ts DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "profile": row["profile"],
            "city": row["city"],
            "target_date": row["target_date"],
            "question": row["question"],
            "outcome": row["outcome"],
            "entry_price": REPLAY.round6(row["entry_price"] or 0.0),
            "model_prob": REPLAY.round6(row["model_prob"] or 0.0),
            "edge": REPLAY.round6(row["edge"] or 0.0),
            "spend": REPLAY.round6(row["spend"] or 0.0),
            "entry_scan_iso": row["entry_scan_iso"],
        }
        for row in rows
    ]


def recent_resolved(conn: sqlite3.Connection, limit: int = 15) -> List[dict]:
    rows = conn.execute(
        """
        SELECT profile, city, target_date, question, outcome, entry_price, exit_price,
               pnl, roi_pct, resolved_winner, exit_iso
        FROM shadow_positions
        WHERE status = 'resolved'
        ORDER BY exit_ts DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "profile": row["profile"],
            "city": row["city"],
            "target_date": row["target_date"],
            "question": row["question"],
            "outcome": row["outcome"],
            "entry_price": REPLAY.round6(row["entry_price"] or 0.0),
            "exit_price": REPLAY.round6(row["exit_price"] or 0.0),
            "pnl": REPLAY.round6(row["pnl"] or 0.0),
            "roi_pct": REPLAY.round6(row["roi_pct"] or 0.0),
            "resolved_winner": row["resolved_winner"],
            "exit_iso": row["exit_iso"],
        }
        for row in rows
    ]


def build_report_payload(conn: sqlite3.Connection, cities: Sequence[str]) -> dict:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS observations,
            COUNT(DISTINCT scan_ts) AS scan_epochs,
            COUNT(DISTINCT market_slug) AS unique_markets
        FROM scan_observations
        """
    ).fetchone()
    return {
        "generated_at": utc_now().isoformat(),
        "cities": list(cities),
        "profiles": [asdict(profile) for profile in PROFILES],
        "db_totals": {
            "observations": int(row["observations"] or 0) if row else 0,
            "scan_epochs": int(row["scan_epochs"] or 0) if row else 0,
            "unique_markets": int(row["unique_markets"] or 0) if row else 0,
        },
        "profile_summary": profile_summary(conn),
        "latest_candidates": latest_candidates(conn),
        "open_positions": open_positions(conn),
        "recent_resolved": recent_resolved(conn),
    }


def write_report(payload: dict, output_prefix: Path) -> None:
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    output_json = output_prefix.with_suffix(".json")
    output_md = output_prefix.with_suffix(".md")
    output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Weather Shadow Forward",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Cities: `{', '.join(payload['cities'])}`",
        f"- Scan epochs recorded: `{payload['db_totals']['scan_epochs']}`",
        f"- Observations recorded: `{payload['db_totals']['observations']}`",
        f"- Unique markets seen: `{payload['db_totals']['unique_markets']}`",
        "",
        "## Profile Summary",
        "",
    ]
    if payload["profile_summary"]:
        lines.append("| Profile | Total | Open | Resolved | Wins | Losses | Win rate | Total PnL | Avg entry | Avg ROI |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in payload["profile_summary"]:
            lines.append(
                f"| {row['profile']} | {row['total_positions']} | {row['open_positions']} | "
                f"{row['resolved_positions']} | {row['wins']} | {row['losses']} | "
                f"{row['win_rate']:.1%} | {row['total_pnl']:.2f} | {row['avg_entry_price']:.3f} | "
                f"{row['avg_roi_pct']:.1f}% |"
            )
    else:
        lines.append("No shadow positions have been opened yet.")

    lines.extend(["", "## Latest Candidates", ""])
    if payload["latest_candidates"]:
        lines.append("| Edge | Profile | City | Date | Side | Price | Prob | Decision | Market |")
        lines.append("| ---: | --- | --- | --- | --- | ---: | ---: | --- | --- |")
        for row in payload["latest_candidates"]:
            decision_text = "open" if row["decision"] else row["decision_reason"]
            lines.append(
                f"| {row['edge']:.3f} | {row['profile']} | {row['city']} | {row['target_date']} | "
                f"{row['outcome']} | {row['price']:.3f} | {row['prob']:.3f} | {decision_text} | "
                f"{row['question']} |"
            )
    else:
        lines.append("No scan candidates have met any profile threshold yet.")

    lines.extend(["", "## Open Positions", ""])
    if payload["open_positions"]:
        lines.append("| Profile | City | Date | Side | Entry | Prob | Edge | Spend | Market |")
        lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |")
        for row in payload["open_positions"]:
            lines.append(
                f"| {row['profile']} | {row['city']} | {row['target_date']} | {row['outcome']} | "
                f"{row['entry_price']:.3f} | {row['model_prob']:.3f} | {row['edge']:.3f} | "
                f"{row['spend']:.2f} | {row['question']} |"
            )
    else:
        lines.append("No open shadow positions.")

    lines.extend(["", "## Recent Resolutions", ""])
    if payload["recent_resolved"]:
        lines.append("| PnL | ROI | Profile | City | Date | Side | Entry | Exit | Winner | Market |")
        lines.append("| ---: | ---: | --- | --- | --- | --- | ---: | ---: | --- | --- |")
        for row in payload["recent_resolved"]:
            lines.append(
                f"| {row['pnl']:.2f} | {row['roi_pct']:.1f}% | {row['profile']} | {row['city']} | "
                f"{row['target_date']} | {row['outcome']} | {row['entry_price']:.3f} | "
                f"{row['exit_price']:.3f} | {row['resolved_winner']} | {row['question']} |"
            )
    else:
        lines.append("No resolved shadow positions yet.")

    output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    cities = [city.strip() for city in args.cities.split(",") if city.strip()]
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_db(conn)

    scan_summary = None
    resolve_summary = None
    if args.mode in {"scan", "all"}:
        scan_summary = scan_markets(conn, cities, args.future_days)
    if args.mode in {"resolve", "all"}:
        resolve_summary = resolve_positions(conn)
    if args.mode in {"report", "all"}:
        payload = build_report_payload(conn, cities)
        output_prefix = Path(args.output_prefix)
        write_report(payload, output_prefix)
        print(f"report_md={output_prefix.with_suffix('.md')}")
        print(f"report_json={output_prefix.with_suffix('.json')}")

    print(f"db_path={db_path}")
    if scan_summary:
        print(f"scan_summary={json.dumps(scan_summary, separators=(',', ':'))}")
    if resolve_summary:
        print(f"resolve_summary={json.dumps(resolve_summary, separators=(',', ':'))}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
