import sqlite3
import time
from typing import Optional


class TradeStore:
    def __init__(self, db_path: str):
        self._db_path = db_path

    def setup(self) -> None:
        con = sqlite3.connect(self._db_path)
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
                exit_reason TEXT
            )
        """)
        con.commit()
        con.close()

    def record_entry(
        self,
        basket_id: str,
        symbol: str,
        direction: str,
        side: str,
        signal_pct: float,
        entry_price: float,
        entry_ts: Optional[float] = None,
    ) -> None:
        if entry_ts is None:
            entry_ts = time.time()
        con = sqlite3.connect(self._db_path)
        con.execute(
            """
            INSERT INTO trades (basket_id, symbol, direction, side, signal_pct, entry_price, entry_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (basket_id, symbol, direction, side, signal_pct, entry_price, entry_ts),
        )
        con.commit()
        con.close()

    def record_exit(
        self,
        basket_id: str,
        exit_price: Optional[float],
        exit_reason: str,
        exit_ts: Optional[float] = None,
    ) -> None:
        if exit_ts is None:
            exit_ts = time.time()
        con = sqlite3.connect(self._db_path)
        row = con.execute(
            "SELECT entry_price, direction FROM trades WHERE basket_id=? AND exit_ts IS NULL",
            (basket_id,),
        ).fetchone()
        if row is None:
            con.close()
            return
        entry_price, direction = row
        pnl_pts: Optional[float] = None
        if entry_price is not None and exit_price is not None:
            sign = 1 if direction == "UP" else -1
            pnl_pts = (exit_price - entry_price) * sign
        con.execute(
            """
            UPDATE trades
            SET exit_price=?, exit_reason=?, exit_ts=?, pnl_pts=?
            WHERE basket_id=? AND exit_ts IS NULL
            """,
            (exit_price, exit_reason, exit_ts, pnl_pts, basket_id),
        )
        con.commit()
        con.close()

    def get_daily_pnl(self, date_str: str) -> float:
        con = sqlite3.connect(self._db_path)
        row = con.execute(
            "SELECT COALESCE(SUM(pnl_pts), 0) FROM trades "
            "WHERE date(exit_ts, 'unixepoch') = ? AND exit_ts IS NOT NULL",
            (date_str,),
        ).fetchone()
        con.close()
        return float(row[0]) if row else 0.0

    def get_total_pnl(self) -> float:
        con = sqlite3.connect(self._db_path)
        row = con.execute(
            "SELECT COALESCE(SUM(pnl_pts), 0) FROM trades WHERE exit_ts IS NOT NULL"
        ).fetchone()
        con.close()
        return float(row[0]) if row else 0.0

    def get_open_trades(self) -> list:
        con = sqlite3.connect(self._db_path)
        rows = con.execute(
            "SELECT basket_id, direction, entry_price, entry_ts FROM trades WHERE exit_ts IS NULL"
        ).fetchall()
        con.close()
        return rows
