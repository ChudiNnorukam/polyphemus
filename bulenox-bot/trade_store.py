import sqlite3
import time
from typing import Optional, Union


class TradeStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._con: Optional[sqlite3.Connection] = None  # Python 3.10 compat (no X | None)

    def _get_con(self) -> sqlite3.Connection:
        if self._con is None:
            self._con = sqlite3.connect(self._db_path)
            self._con.execute("PRAGMA journal_mode=WAL")
            self._con.execute("PRAGMA busy_timeout=5000")
        return self._con

    def setup(self) -> None:
        con = self._get_con()
        con.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                basket_id   TEXT NOT NULL,
                symbol      TEXT,
                direction   TEXT,
                side        TEXT,
                signal_pct  REAL,
                entry_price REAL,
                exit_price  REAL,
                pnl_pts     REAL,
                entry_ts    REAL,
                exit_ts     REAL,
                exit_reason TEXT,
                mfe_ticks REAL,
                mae_ticks REAL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_basket_id ON trades(basket_id)")
        # Add columns if table existed before MFE/MAE
        try:
            con.execute("ALTER TABLE trades ADD COLUMN mfe_ticks REAL")
        except sqlite3.OperationalError:
            pass
        try:
            con.execute("ALTER TABLE trades ADD COLUMN mae_ticks REAL")
        except sqlite3.OperationalError:
            pass
        # Signal log table - persists all signals (executed + rejected)
        con.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                raw_direction TEXT NOT NULL,
                fade_direction TEXT NOT NULL,
                signal_pct REAL NOT NULL,
                action TEXT NOT NULL,
                basket_id TEXT,
                reason TEXT
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts)")
        # Shadow trades table - counterfactual tracking for rejected signals
        con.execute("""
            CREATE TABLE IF NOT EXISTS shadow_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_ts REAL NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                rejection_reason TEXT NOT NULL,
                outcome TEXT,
                pnl_pts REAL,
                resolved_ts REAL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_shadow_ts ON shadow_trades(signal_ts)")
        con.commit()

    def record_signal(
        self,
        raw_direction: str,
        fade_direction: str,
        signal_pct: float,
        action: str,
        basket_id: Optional[str] = None,
        reason: Optional[str] = None,
        ts: Optional[float] = None,
    ) -> int:
        """Log every signal: executed, rejected, ignored."""
        if ts is None:
            ts = time.time()
        con = self._get_con()
        cur = con.execute(
            "INSERT INTO signals (ts, raw_direction, fade_direction, signal_pct, action, basket_id, reason) VALUES (?,?,?,?,?,?,?)",
            (ts, raw_direction, fade_direction, signal_pct, action, basket_id, reason),
        )
        con.commit()
        return int(cur.lastrowid)

    def record_entry(
        self,
        basket_id: str,
        symbol: str,
        direction: str,
        side: str,
        signal_pct: float,
        entry_price: float,
        entry_ts: Optional[float] = None,
    ) -> int:
        if entry_ts is None:
            entry_ts = time.time()
        con = self._get_con()
        cur = con.execute(
            """
            INSERT INTO trades (basket_id, symbol, direction, side, signal_pct, entry_price, entry_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (basket_id, symbol, direction, side, signal_pct, entry_price, entry_ts),
        )
        con.commit()
        return int(cur.lastrowid)

    def record_exit(
        self,
        basket_id: str,
        exit_price: Optional[float],
        exit_reason: str,
        exit_ts: Optional[float] = None,
        mfe_ticks: Optional[float] = None,
        mae_ticks: Optional[float] = None,
    ) -> None:
        if exit_ts is None:
            exit_ts = time.time()
        con = self._get_con()
        row = con.execute(
            "SELECT entry_price, direction FROM trades WHERE basket_id=? AND exit_ts IS NULL",
            (basket_id,),
        ).fetchone()
        if row is None:
            return
        entry_price, direction = row
        pnl_pts: Optional[float] = None
        if entry_price is not None and exit_price is not None:
            sign = 1 if direction == "UP" else -1
            pnl_pts = (exit_price - entry_price) * sign
        con.execute(
            """
            UPDATE trades
            SET exit_price=?, exit_reason=?, exit_ts=?, pnl_pts=?, mfe_ticks=?, mae_ticks=?
            WHERE basket_id=? AND exit_ts IS NULL
            """,
            (exit_price, exit_reason, exit_ts, pnl_pts, mfe_ticks, mae_ticks, basket_id),
        )
        con.commit()

    def get_daily_pnl(self, date_str: str) -> float:
        con = self._get_con()
        row = con.execute(
            "SELECT COALESCE(SUM(pnl_pts), 0) FROM trades "
            "WHERE date(exit_ts, 'unixepoch') = ? AND exit_ts IS NOT NULL",
            (date_str,),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def get_total_pnl(self) -> float:
        con = self._get_con()
        row = con.execute(
            "SELECT COALESCE(SUM(pnl_pts), 0) FROM trades WHERE exit_ts IS NOT NULL"
        ).fetchone()
        return float(row[0]) if row else 0.0

    def get_total_trades(self) -> int:
        con = self._get_con()
        row = con.execute("SELECT COUNT(*) FROM trades").fetchone()
        return int(row[0]) if row else 0

    def get_open_trades(self) -> list:
        con = self._get_con()
        rows = con.execute(
            "SELECT basket_id, direction, entry_price, entry_ts FROM trades WHERE exit_ts IS NULL"
        ).fetchall()
        return rows

    def record_shadow_trade(
        self, signal_ts: float, direction: str, entry_price: float, rejection_reason: str,
    ) -> int:
        """Record a rejected signal for counterfactual tracking. Returns row id."""
        con = self._get_con()
        cur = con.execute(
            "INSERT INTO shadow_trades (signal_ts, direction, entry_price, rejection_reason) VALUES (?,?,?,?)",
            (signal_ts, direction, entry_price, rejection_reason),
        )
        con.commit()
        return cur.lastrowid

    def resolve_shadow_trade(self, row_id: int, outcome: str, pnl_pts: float) -> None:
        """Resolve a shadow trade with what WOULD have happened."""
        con = self._get_con()
        con.execute(
            "UPDATE shadow_trades SET outcome=?, pnl_pts=?, resolved_ts=? WHERE id=?",
            (outcome, pnl_pts, time.time(), row_id),
        )
        con.commit()

    def get_directional_wr(self, direction: str, lookback: int = 20) -> tuple[float, int]:
        """Get win rate for a specific direction over last N trades. Returns (wr, n)."""
        con = self._get_con()
        rows = con.execute(
            "SELECT pnl_pts FROM trades WHERE direction=? AND exit_ts IS NOT NULL "
            "ORDER BY exit_ts DESC LIMIT ?",
            (direction, lookback),
        ).fetchall()
        if not rows:
            return 0.0, 0
        wins = sum(1 for r in rows if r[0] is not None and r[0] > 0)
        return wins / len(rows), len(rows)

    def get_shadow_stats(self) -> dict:
        """Get counterfactual stats grouped by rejection reason."""
        con = self._get_con()
        rows = con.execute(
            "SELECT rejection_reason, COUNT(*) as n, "
            "SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins, "
            "AVG(pnl_pts) as avg_pnl "
            "FROM shadow_trades WHERE outcome IS NOT NULL "
            "GROUP BY rejection_reason"
        ).fetchall()
        return {r[0]: {"n": r[1], "wins": r[2], "wr": r[2]/r[1] if r[1] > 0 else 0, "avg_pnl": r[3]} for r in rows}
