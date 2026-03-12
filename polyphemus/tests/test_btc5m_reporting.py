import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKTESTER = ROOT / "tools" / "backtester.py"
REPORTER = ROOT / "tools" / "dario_btc5m_report.py"


def build_signals_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                epoch REAL NOT NULL,
                slug TEXT NOT NULL,
                asset TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL,
                midpoint REAL,
                momentum_pct REAL,
                regime TEXT,
                time_remaining_secs INTEGER,
                guard_passed INTEGER,
                guard_reasons TEXT,
                outcome TEXT,
                signal_score REAL,
                source TEXT,
                market_window_secs INTEGER,
                evidence_cohort TEXT,
                evidence_sample_size INTEGER,
                evidence_r8_label TEXT,
                evidence_expected_pnl REAL,
                evidence_verdict TEXT,
                evidence_reason TEXT,
                evidence_match_level TEXT
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
                bot_saw_signal INTEGER DEFAULT 0,
                bot_signal_source TEXT,
                resolved_outcome TEXT,
                oracle_delta_pct REAL,
                binance_delta_pct REAL,
                timestamp TEXT NOT NULL
            )
            """
        )

        conn.executemany(
            """
            INSERT INTO signals (
                epoch, slug, asset, direction, entry_price, midpoint, momentum_pct,
                regime, time_remaining_secs, guard_passed, guard_reasons, outcome,
                signal_score, source, market_window_secs, evidence_cohort,
                evidence_sample_size, evidence_r8_label, evidence_expected_pnl,
                evidence_verdict, evidence_reason, evidence_match_level
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1771000000,
                    "btc-updown-5m-1771000000",
                    "BTC",
                    "Up",
                    0.68,
                    0.68,
                    0.004,
                    "trending",
                    150,
                    1,
                    "",
                    "executed",
                    75.0,
                    "binance_momentum",
                    300,
                    "source=binance_momentum | price_bucket=0.60-0.79",
                    18,
                    "ANECDOTAL n=18",
                    0.08,
                    "shadow",
                    "insufficient_sample 18<30 | level=source+price_bucket",
                    "source+price_bucket",
                ),
                (
                    1771000300,
                    "btc-updown-5m-1771000300",
                    "BTC",
                    "Down",
                    0.52,
                    0.52,
                    0.002,
                    "flat",
                    40,
                    0,
                    "not_buy_signal,too_late",
                    "filtered",
                    20.0,
                    "binance_momentum",
                    300,
                    "source=binance_momentum | price_bucket=0.40-0.59",
                    41,
                    "LOW n=41",
                    -0.03,
                    "block",
                    "negative_expectancy avg_pnl=-0.0300 | level=source+price_bucket+time_bucket",
                    "source+price_bucket+time_bucket",
                ),
                (
                    1771000600,
                    "eth-updown-15m-1771000600",
                    "ETH",
                    "Up",
                    0.84,
                    0.84,
                    0.005,
                    "volatile",
                    600,
                    1,
                    "",
                    "executed",
                    60.0,
                    "sharp_move",
                    900,
                    "",
                    None,
                    "",
                    None,
                    "",
                    "",
                    "",
                ),
            ],
        )

        conn.executemany(
            """
            INSERT INTO epoch_coverage (
                epoch, asset, window_secs, bot_saw_signal, bot_signal_source,
                resolved_outcome, oracle_delta_pct, binance_delta_pct, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1771000000, "BTC", 300, 1, "binance_momentum", "Up", 0.003, 0.004, "2026-03-10T00:00:00Z"),
                (1771000300, "BTC", 300, 1, "binance_momentum", "Down", -0.002, -0.001, "2026-03-10T00:05:00Z"),
                (1771000600, "BTC", 300, 0, "", "Up", 0.001, 0.001, "2026-03-10T00:10:00Z"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def build_performance_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE trades (
                trade_id TEXT PRIMARY KEY,
                token_id TEXT NOT NULL,
                entry_time INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                entry_size REAL NOT NULL,
                side TEXT NOT NULL,
                entry_amount REAL NOT NULL,
                exit_time INTEGER,
                exit_price REAL,
                exit_amount REAL,
                exit_reason TEXT,
                profit_loss REAL,
                profit_loss_pct REAL,
                hold_seconds INTEGER,
                strategy TEXT NOT NULL,
                slug TEXT,
                outcome TEXT,
                metadata TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO trades (
                trade_id, token_id, entry_time, entry_price, entry_size, side,
                entry_amount, exit_time, exit_price, exit_amount, exit_reason,
                profit_loss, profit_loss_pct, hold_seconds, strategy, slug, outcome, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "trade-btc-1",
                    "token-btc",
                    1771000000,
                    0.68,
                    10.0,
                    "BUY",
                    6.8,
                    1771000100,
                    0.80,
                    8.0,
                    "profit_target",
                    4.0,
                    0.12,
                    100,
                    "signal_bot",
                    "btc-updown-5m-1771000000",
                    "Up",
                    "{}",
                ),
                (
                    "trade-eth-1",
                    "token-eth",
                    1771000600,
                    0.84,
                    10.0,
                    "BUY",
                    8.4,
                    1771001100,
                    0.70,
                    7.0,
                    "time_exit",
                    -2.0,
                    -0.10,
                    500,
                    "signal_bot",
                    "eth-updown-15m-1771000600",
                    "Up",
                    "{}",
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_dario_btc5m_report_generation(tmp_path: Path) -> None:
    signals_db = tmp_path / "signals.db"
    performance_db = tmp_path / "performance.db"
    config_path = tmp_path / ".env"
    market_context_path = tmp_path / "lagbot_context.json"
    dario_dir = tmp_path / "dario_output"
    output_path = tmp_path / "report.md"

    build_signals_db(signals_db)
    build_performance_db(performance_db)
    config_path.write_text(
        "ASSET_FILTER=BTC\nMARKET_WINDOW_SECS=300\nENTRY_MODE=maker\nDRY_RUN=false\n",
        encoding="utf-8",
    )
    market_context_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-03-10T01:00:00Z",
                "fear_greed": 42,
                "BTC": {"oi_trend": "rising"},
            }
        ),
        encoding="utf-8",
    )
    dario_dir.mkdir()
    (dario_dir / "dario_test_20260310.md").write_text(
        "# Test Report\n\n**Verdict:** Hold BTC 5m only.\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            str(REPORTER),
            "--instance",
            "emmanuel",
            "--signals-db",
            str(signals_db),
            "--performance-db",
            str(performance_db),
            "--config-path",
            str(config_path),
            "--market-context-path",
            str(market_context_path),
            "--dario-dir",
            str(dario_dir),
            "--output",
            str(output_path),
            "--report-title",
            "Synthetic BTC 5m Report",
        ],
        check=True,
        cwd=ROOT,
    )

    report = output_path.read_text(encoding="utf-8")
    assert "# Synthetic BTC 5m Report" in report
    assert "Config snapshot hash" in report
    assert "0.60-0.79" in report
    assert "not_buy_signal" in report
    assert "Epochs with no signal" in report
    assert "ANECDOTAL n=1" in report
    assert "Runtime Evidence Verdicts" in report
    assert "negative_expectancy avg_pnl=-0.0300" in report
    assert "block" in report
    assert "dario_test_20260310.md" in report


def test_backtester_filters_and_json_output(tmp_path: Path) -> None:
    signals_db = tmp_path / "signals.db"
    performance_db = tmp_path / "performance.db"
    output_path = tmp_path / "results.json"

    build_signals_db(signals_db)
    build_performance_db(performance_db)

    subprocess.run(
        [
            sys.executable,
            str(BACKTESTER),
            "--instance",
            "emmanuel",
            "--signals-db",
            str(signals_db),
            "--performance-db",
            str(performance_db),
            "--asset",
            "BTC",
            "--window",
            "300",
            "--source",
            "binance_momentum",
            "--output",
            str(output_path),
            "--output-format",
            "json",
            "--report-title",
            "Filtered Replay",
        ],
        check=True,
        cwd=ROOT,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["title"] == "Filtered Replay"
    assert payload["filters"] == {"asset": "BTC", "window": 300, "source": "binance_momentum"}
    assert payload["dataset"]["total_signals"] == 2
    assert payload["dataset"]["sources"] == ["binance_momentum"]
    assert payload["dataset"]["signals_with_evidence"] == 2
    assert payload["dataset"]["evidence_verdicts"] == {"block": 1, "shadow": 1}
    assert payload["results"]
