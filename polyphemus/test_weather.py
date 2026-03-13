"""Weather strategy regression tests."""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from polyphemus.config import Settings
from polyphemus.position_executor import PositionExecutor
from polyphemus.position_store import PositionStore
from polyphemus.types import ExecutionResult
from polyphemus.weather_feed import WeatherFeed


def make_config(**overrides):
    defaults = dict(
        private_key="0x" + "a" * 64,
        wallet_address="0x914377734689c9e055B8826733F90dF0893817a2",
        clob_api_key="test_key",
        clob_secret="test_secret",
        clob_passphrase="test_pass",
        builder_api_key="test_builder_key",
        builder_secret="test_builder_secret",
        builder_passphrase="test_builder_pass",
        polygon_rpc_url="http://localhost:8545",
        dry_run=True,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_weather_feed_emits_no_signal_for_complement_edge():
    config = make_config(
        signal_mode="noaa_weather",
        weather_dry_run=False,
        weather_allow_complement=True,
        entry_mode="maker",
    )
    clob = MagicMock()
    clob.get_order_book = AsyncMock(return_value={
        "bids": [{"price": 0.001, "size": 1000}],
        "asks": [{"price": 0.003, "size": 1000}],
    })
    on_signal = AsyncMock()

    feed = WeatherFeed(config=config, clob=clob, on_signal=on_signal)
    feed._get_forecast_probability = AsyncMock(return_value=0.01)

    market = {
        "question": "Will the highest temperature in Dallas be 74°F or above on March 13?",
        "groupItemTitle": "74°F or above",
        "slug": "highest-temperature-in-dallas-on-march-13-2026-74forabove",
        "conditionId": "cond-1",
        "endDate": (datetime.now(timezone.utc) + timedelta(hours=18)).isoformat(),
        "clobTokenIds": '["yes-token","no-token"]',
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.998","0.002"]',
        "orderPriceMinTickSize": 0.001,
        "_weather_city": "Dallas",
    }

    await feed._evaluate_market(market, ["Dallas"])

    on_signal.assert_awaited_once()
    signal = on_signal.await_args.args[0]
    assert signal["outcome"] == "No"
    assert signal["token_id"] == "no-token"
    assert signal["price"] == pytest.approx(0.002, abs=1e-9)
    assert signal["noaa_prob"] == pytest.approx(0.99, abs=1e-9)
    assert signal["metadata"]["price_tick_size"] == pytest.approx(0.001, abs=1e-9)
    assert signal["metadata"]["maker_target_price"] == pytest.approx(0.002, abs=1e-9)


@pytest.mark.asyncio
async def test_execute_buy_weather_taker_uses_subcent_tick():
    config = make_config(entry_mode="taker", taker_on_5m=False)
    clob = MagicMock()
    clob.get_midpoint = AsyncMock(return_value=0.006)
    clob.place_order = AsyncMock(return_value=ExecutionResult(success=True, order_id="ord-1"))

    executor = PositionExecutor(clob=clob, store=PositionStore(), config=config)
    executor._poll_for_fill = AsyncMock(return_value=ExecutionResult(
        success=True,
        order_id="ord-1",
        fill_price=0.008,
        fill_size=500.0,
    ))

    signal = {
        "token_id": "weather-token",
        "price": 0.006,
        "slug": "highest-temperature-in-dallas-on-march-13-2026-74forabove",
        "market_title": "Will the highest temperature in Dallas be 74°F or above on March 13?",
        "usdc_size": 2.0,
        "asset": "WEATHER",
        "source": "noaa_weather",
        "kelly_fraction": 0.02,
        "metadata": {
            "is_weather": True,
            "price_tick_size": 0.001,
        },
    }

    result = await executor.execute_buy(signal, available_capital=100.0)

    assert result.success
    clob.place_order.assert_awaited_once()
    assert clob.place_order.await_args.kwargs["price"] == pytest.approx(0.008, abs=1e-9)
