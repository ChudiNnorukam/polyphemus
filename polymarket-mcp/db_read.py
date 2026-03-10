"""Read-only interface to the performance.db SQLite database."""

import os
import sqlite3
from datetime import date, datetime, timezone
from typing import Dict, List, Optional


class DBReader:
    """Read-only performance database queries."""

    def __init__(self):
        self._db_path: Optional[str] = None

    def _get_path(self) -> str:
        if self._db_path is None:
            self._db_path = os.environ.get("PERFORMANCE_DB_PATH", "")
        return self._db_path

    def _get_conn(self) -> sqlite3.Connection:
        path = self._get_path()
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"Performance DB not found: {path}")
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _detect_pnl_col(self, conn: sqlite3.Connection) -> str:
        cursor = conn.execute("PRAGMA table_info(trades)")
        columns = {row[1] for row in cursor.fetchall()}
        return "pnl" if "pnl" in columns else "profit_loss"

    def get_open_trades(self) -> List[Dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM trades WHERE exit_time IS NULL"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_recent_trades(self, limit: int = 20) -> List[Dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM trades WHERE exit_time IS NOT NULL "
                "ORDER BY entry_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_stats(self) -> Dict:
        conn = self._get_conn()
        try:
            pnl = self._detect_pnl_col(conn)

            row = conn.execute(
                f"""SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN {pnl} > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN {pnl} < 0 THEN 1 ELSE 0 END) AS losses,
                    COALESCE(SUM({pnl}), 0.0) AS total_pnl,
                    COALESCE(AVG({pnl}), 0.0) AS avg_pnl
                FROM trades WHERE exit_time IS NOT NULL"""
            ).fetchone()

            total = row["total"] or 0
            wins = row["wins"] or 0
            losses = row["losses"] or 0

            res_row = conn.execute(
                f"""SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN {pnl} > 0 THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN {pnl} < 0 THEN 1 ELSE 0 END) AS losses
                FROM trades
                WHERE exit_reason = 'market_resolved' AND exit_time IS NOT NULL"""
            ).fetchone()

            res_total = res_row["total"] or 0
            res_wins = res_row["wins"] or 0
            res_losses = res_row["losses"] or 0

            return {
                "total_trades": total,
                "winning_trades": wins,
                "losing_trades": losses,
                "total_pnl": round(row["total_pnl"], 2),
                "avg_pnl": round(row["avg_pnl"], 2),
                "win_rate": round(wins / total, 4) if total > 0 else 0.0,
                "resolution_wins": res_wins,
                "resolution_losses": res_losses,
                "resolution_wr": round(res_wins / res_total, 4) if res_total > 0 else 0.0,
            }
        finally:
            conn.close()

    def get_daily_pnl(self, utc_date: date) -> float:
        conn = self._get_conn()
        try:
            pnl = self._detect_pnl_col(conn)
            start = int(datetime.combine(utc_date, datetime.min.time(), tzinfo=timezone.utc).timestamp())
            end = start + 86400
            row = conn.execute(
                f"SELECT COALESCE(SUM({pnl}), 0.0) AS day_pnl "
                f"FROM trades WHERE exit_time >= ? AND exit_time < ?",
                (start, end),
            ).fetchone()
            return round(row["day_pnl"], 2)
        finally:
            conn.close()

    def get_wr_for_bucket(self, asset: str, bucket: float) -> Dict:
        conn = self._get_conn()
        try:
            pnl = self._detect_pnl_col(conn)
            slug_prefix = f"{asset.lower()}-%"
            row = conn.execute(
                f"""SELECT COUNT(*) AS n,
                    COALESCE(SUM(CASE WHEN {pnl} > 0 THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0), 0) AS wr
                FROM trades
                WHERE slug LIKE ?
                    AND exit_reason = 'market_resolved'
                    AND ROUND(entry_price * 10.0) / 10.0 = ?
                    AND {pnl} IS NOT NULL""",
                (slug_prefix, round(bucket, 1)),
            ).fetchone()
            return {"win_rate": round(float(row["wr"]), 4), "n": int(row["n"])}
        finally:
            conn.close()

    def get_all_buckets(self, asset: str) -> List[Dict]:
        conn = self._get_conn()
        try:
            pnl = self._detect_pnl_col(conn)
            slug_prefix = f"{asset.lower()}-%"
            rows = conn.execute(
                f"""SELECT
                    ROUND(entry_price * 10.0) / 10.0 AS bucket,
                    COUNT(*) AS n,
                    COALESCE(SUM(CASE WHEN {pnl} > 0 THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0), 0) AS wr,
                    COALESCE(SUM({pnl}), 0) AS bucket_pnl
                FROM trades
                WHERE slug LIKE ?
                    AND exit_reason = 'market_resolved'
                    AND {pnl} IS NOT NULL
                GROUP BY bucket
                ORDER BY bucket""",
                (slug_prefix,),
            ).fetchall()
            return [
                {
                    "bucket": f"{float(r['bucket']):.1f}-{float(r['bucket'])+0.1:.1f}",
                    "n": int(r["n"]),
                    "win_rate": round(float(r["wr"]), 4),
                    "pnl": round(float(r["bucket_pnl"]), 2),
                }
                for r in rows
            ]
        finally:
            conn.close()
