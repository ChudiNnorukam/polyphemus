import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from polyphemus.signal_bot import SignalBot
from polyphemus.signal_logger import SignalLogger
from polyphemus.signal_pipeline import normalize_signal
from polyphemus.types import FilterResult


def test_normalize_signal_derives_canonical_fields_and_noise_flags():
    epoch = int(time.time()) - 20
    normalized = normalize_signal(
        {
            "slug": f"btc-updown-5m-{epoch}",
            "midpoint": 0.73,
            "outcome": "up",
            "source": "binance_momentum",
        }
    )

    signal = normalized.signal
    assert signal["asset"] == "BTC"
    assert signal["outcome"] == "Up"
    assert signal["direction"] == "BUY"
    assert signal["price"] == pytest.approx(0.73)
    assert signal["market_window_secs"] == 300
    assert 0 <= signal["time_remaining_secs"] <= 300
    assert set(normalized.noise_flags) >= {
        "derived_asset",
        "derived_market_window",
        "derived_time_remaining",
    }


def test_signal_logger_accepts_overrides_and_filters_unknown_fields(tmp_path: Path):
    logger = SignalLogger(str(tmp_path / "signals.db"))
    signal_id = logger.log_signal(
        {
            "slug": "btc-updown-5m-1775000000",
            "asset": "BTC",
            "direction": "Up",
            "midpoint": 0.71,
        },
        guard_passed=True,
        pipeline_stage="guard",
        pipeline_status="passed",
        noise_flags=["derived_asset", "derived_time_remaining"],
        unknown_field="ignored",
    )

    row = logger._conn.execute(
        """
        SELECT guard_passed, pipeline_stage, pipeline_status, noise_flags
        FROM signals
        WHERE id = ?
        """,
        (signal_id,),
    ).fetchone()
    logger.close()

    assert row == (1, "guard", "passed", "derived_asset,derived_time_remaining")


@pytest.mark.asyncio
async def test_signal_bot_logs_pipeline_stage_for_guard_rejection(tmp_path: Path):
    logger = SignalLogger(str(tmp_path / "signals.db"))
    bot = SignalBot.__new__(SignalBot)
    bot._config = SimpleNamespace(
        market_context_path=str(tmp_path / "missing_context.json"),
    )
    bot._logger = MagicMock()
    bot._regime_detector = None
    bot._signal_logger = logger
    bot._btc5m_evidence = None
    bot._guard = MagicMock()
    bot._guard.check.return_value = FilterResult(
        passed=False,
        reasons=["price_out_of_range"],
        context={"price": 0.81},
    )
    bot._health = MagicMock()

    epoch = int(time.time()) - 15
    await SignalBot._on_signal(
        bot,
        {
            "slug": f"btc-updown-5m-{epoch}",
            "midpoint": 0.81,
            "outcome": "up",
            "source": "binance_momentum",
        },
    )

    row = logger._conn.execute(
        """
        SELECT asset, midpoint, market_window_secs, outcome, pipeline_stage, pipeline_status, noise_flags
        FROM signals
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    logger.close()

    assert row is not None
    assert row[0] == "BTC"
    assert row[1] == pytest.approx(0.81)
    assert row[2] == 300
    assert row[3] == "filtered"
    assert row[4] == "guard"
    assert row[5] == "filtered"
    assert "derived_asset" in row[6]
    assert "derived_market_window" in row[6]
