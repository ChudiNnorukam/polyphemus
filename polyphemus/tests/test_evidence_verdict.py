import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from polyphemus.evidence_verdict import BTC5MEvidenceEngine
from polyphemus.signal_bot import SignalBot
from polyphemus.signal_logger import SignalLogger
from polyphemus.models import FilterResult


def build_history_db(
    path: Path,
    executed_count: int,
    pnl: float,
    price: float = 0.82,
    secs_left: int = 45,
    source: str = "binance_momentum",
    regime: str = "flat",
    fear_greed: float = 15.0,
    oi_trend: str = "falling",
    filtered_count: int = 0,
    total_epochs: int = 0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                epoch REAL NOT NULL,
                asset TEXT NOT NULL,
                slug TEXT NOT NULL,
                market_window_secs INTEGER NOT NULL,
                source TEXT,
                entry_price REAL,
                midpoint REAL,
                time_remaining_secs INTEGER,
                regime TEXT,
                fear_greed REAL,
                oi_trend TEXT,
                pnl REAL,
                is_win INTEGER,
                exit_reason TEXT,
                outcome TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE epoch_coverage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                epoch INTEGER NOT NULL,
                asset TEXT NOT NULL,
                window_secs INTEGER NOT NULL,
                bot_saw_signal INTEGER DEFAULT 0
            )
            """
        )

        executed_rows = [
            (
                1771000000 + (index * 300),
                "BTC",
                f"btc-updown-5m-{1771000000 + (index * 300)}",
                300,
                source,
                price,
                price,
                secs_left,
                regime,
                fear_greed,
                oi_trend,
                pnl,
                1 if pnl > 0 else 0,
                "profit_target" if pnl > 0 else "time_exit",
                "executed",
            )
            for index in range(executed_count)
        ]
        conn.executemany(
            """
            INSERT INTO signals (
                epoch, asset, slug, market_window_secs, source, entry_price, midpoint,
                time_remaining_secs, regime, fear_greed, oi_trend, pnl, is_win,
                exit_reason, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            executed_rows,
        )

        if filtered_count:
            filtered_rows = [
                (
                    1772000000 + (index * 300),
                    "BTC",
                    f"btc-updown-5m-{1772000000 + (index * 300)}",
                    300,
                    source,
                    price,
                    price,
                    secs_left,
                    regime,
                    fear_greed,
                    oi_trend,
                    None,
                    None,
                    "",
                    "filtered",
                )
                for index in range(filtered_count)
            ]
            conn.executemany(
                """
                INSERT INTO signals (
                    epoch, asset, slug, market_window_secs, source, entry_price, midpoint,
                    time_remaining_secs, regime, fear_greed, oi_trend, pnl, is_win,
                    exit_reason, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                filtered_rows,
            )

        if total_epochs:
            coverage_rows = [
                (
                    1773000000 + (index * 300),
                    "BTC",
                    300,
                    1 if index < executed_count + filtered_count else 0,
                )
                for index in range(total_epochs)
            ]
            conn.executemany(
                """
                INSERT INTO epoch_coverage (epoch, asset, window_secs, bot_saw_signal)
                VALUES (?, ?, ?, ?)
                """,
                coverage_rows,
            )
        conn.commit()
    finally:
        conn.close()


def seed_signal_logger_history(
    db_path: Path,
    executed_count: int,
    pnl: float,
    price: float = 0.82,
    secs_left: int = 45,
) -> SignalLogger:
    logger = SignalLogger(str(db_path))
    conn = sqlite3.connect(db_path)
    try:
        rows = [
            (
                f"2026-03-09T00:{index % 60:02d}:00+00:00",
                1771000000 + (index * 300),
                f"btc-updown-5m-{1771000000 + (index * 300)}",
                "BTC",
                "Up",
                price,
                price,
                secs_left,
                "binance_momentum",
                pnl,
                1 if pnl > 0 else 0,
                "profit_target" if pnl > 0 else "time_exit",
                "executed",
            )
            for index in range(executed_count)
        ]
        conn.executemany(
            """
            INSERT INTO signals (
                timestamp, epoch, slug, asset, direction, entry_price, midpoint,
                time_remaining_secs, source, pnl, is_win, exit_reason, outcome,
                market_window_secs, guard_passed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 300, 1)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return logger


def test_btc5m_evidence_engine_blocks_negative_late_bucket(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    build_history_db(
        db_path,
        executed_count=35,
        pnl=-0.03,
        filtered_count=8,
        total_epochs=60,
    )
    engine = BTC5MEvidenceEngine(str(db_path), min_samples=30, cache_ttl_secs=0)

    verdict = engine.evaluate_signal(
        {
            "asset": "BTC",
            "slug": "btc-updown-5m-1775000000",
            "market_window_secs": 300,
            "source": "binance_momentum",
            "price": 0.84,
            "time_remaining_secs": 40,
            "market_regime": "flat",
            "fear_greed": 16,
            "oi_trend": "falling",
        }
    )

    assert verdict is not None
    assert verdict.verdict == "block"
    assert verdict.sample_size == 35
    assert verdict.match_level == "source+price_bucket+time_bucket+regime+fear_greed+oi_trend"
    assert verdict.expected_pnl < 0
    assert "dominant_exit=time_exit" in verdict.reason
    assert "no_signal_epochs=17/60" in verdict.reason


def test_btc5m_evidence_engine_shadows_anecdotal_cohort(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    build_history_db(
        db_path,
        executed_count=6,
        pnl=0.04,
        total_epochs=12,
    )
    engine = BTC5MEvidenceEngine(str(db_path), min_samples=30, cache_ttl_secs=0)

    verdict = engine.evaluate_signal(
        {
            "asset": "BTC",
            "slug": "btc-updown-5m-1775000000",
            "market_window_secs": 300,
            "source": "binance_momentum",
            "price": 0.82,
            "time_remaining_secs": 45,
            "market_regime": "flat",
            "fear_greed": 15,
            "oi_trend": "falling",
        }
    )

    assert verdict is not None
    assert verdict.verdict == "shadow"
    assert verdict.sample_size == 6
    assert verdict.r8_label == "ANECDOTAL n=6"
    assert "insufficient_sample 6<30" in verdict.reason


@pytest.mark.asyncio
async def test_signal_bot_logs_shadow_evidence_verdict(tmp_path: Path) -> None:
    db_path = tmp_path / "signals.db"
    logger = seed_signal_logger_history(db_path, executed_count=35, pnl=-0.03)

    bot = SignalBot.__new__(SignalBot)
    bot._config = SimpleNamespace(
        market_context_path=str(tmp_path / "missing_context.json"),
        enable_accumulator=False,
        min_bet=5.0,
        entry_mode="maker",
    )
    bot._dry_run = True
    bot._logger = MagicMock()
    bot._guard = MagicMock()
    bot._guard.check.return_value = FilterResult(passed=True, reasons=[], context={})
    bot._signal_logger = logger
    bot._btc5m_evidence = BTC5MEvidenceEngine(str(db_path), min_samples=30, cache_ttl_secs=0)
    bot._regime_detector = None
    bot._binance_feed = None
    bot._signal_scorer = None
    bot._trading_halted = False
    bot._circuit_breaker = MagicMock()
    bot._circuit_breaker.is_trading_allowed.return_value = (True, "")
    bot._balance = MagicMock()
    bot._balance.is_safe_to_trade = AsyncMock(return_value=True)
    bot._balance.get_available = AsyncMock(return_value=100.0)
    bot._executor = SimpleNamespace(_calculate_size=lambda *args, **kwargs: 10.0)
    bot._health = MagicMock()

    await SignalBot._on_signal(
        bot,
        {
            "token_id": "token-btc",
            "price": 0.84,
            "slug": "btc-updown-5m-1775000000",
            "market_title": "BTC Up or Down",
            "usdc_size": 20.0,
            "direction": "BUY",
            "outcome": "Up",
            "asset": "BTC",
            "source": "binance_momentum",
            "market_window_secs": 300,
            "time_remaining_secs": 40,
            "momentum_pct": 0.003,
        },
    )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT evidence_verdict, evidence_sample_size, evidence_match_level, evidence_reason
            FROM signals
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()
        logger._conn.close()

    assert row is not None
    assert row[0] == "block"
    assert row[1] == 35
    assert row[2] == "source+price_bucket+time_bucket+regime+fear_greed+oi_trend"
    assert "negative_expectancy" in row[3]
