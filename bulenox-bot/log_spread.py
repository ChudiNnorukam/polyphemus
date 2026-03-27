#!/usr/bin/env python3
"""Log MBT bid/ask spread from Rithmic TICKER_PLANT for 48 hours.

Writes to data/spread_log.db with columns:
  ts, bid_price, bid_size, ask_price, ask_size, spread_ticks, last_trade_price

Run: PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python python3 log_spread.py
Stop: Ctrl+C or kill PID
"""
import asyncio
import logging
import os
import pathlib
import sqlite3
import ssl
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent / "proto"))

import certifi
import dotenv
import websockets

import base_pb2
import request_login_pb2
import response_login_pb2
import request_market_data_update_pb2
import response_market_data_update_pb2
import last_trade_pb2
import best_bid_offer_pb2
import request_heartbeat_pb2

dotenv.load_dotenv(pathlib.Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("spread_logger")

URI = os.environ.get("RITHMIC_URI", "wss://rituz00100.rithmic.com:443")
USER = os.environ.get("RITHMIC_USER", "")
PASS = os.environ.get("RITHMIC_PASS", "")
SYSTEM = os.environ.get("RITHMIC_SYSTEM", "Rithmic Test")
SYMBOL = os.environ.get("SYMBOL", "ESU6")  # Use ESU6 on Rithmic Test, MBT on Paper Trading
EXCHANGE = os.environ.get("EXCHANGE", "CME")
DB_PATH = os.path.join(os.environ.get("DATA_DIR", "data"), "spread_log.db")


class SpreadLogger:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._con = sqlite3.connect(DB_PATH)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("""
            CREATE TABLE IF NOT EXISTS spread_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                symbol TEXT NOT NULL,
                bid_price REAL,
                bid_size INTEGER,
                ask_price REAL,
                ask_size INTEGER,
                spread_ticks REAL,
                last_trade_price REAL
            )
        """)
        self._con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON spread_log(ts)")
        self._con.commit()

        self._bid = 0.0
        self._bid_size = 0
        self._ask = 0.0
        self._ask_size = 0
        self._last_trade = 0.0
        self._tick_count = 0
        self._start_time = time.time()

    def _build_ssl(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cert = pathlib.Path(__file__).parent / "proto" / "rithmic_ssl_cert_auth_params"
        ctx.load_verify_locations(cert)
        return ctx

    def _log_spread(self):
        if self._bid <= 0 or self._ask <= 0:
            return
        spread_ticks = (self._ask - self._bid) / 5.0  # MBT tick = 5 index points
        now = time.time()
        self._con.execute(
            "INSERT INTO spread_log (ts, symbol, bid_price, bid_size, ask_price, ask_size, spread_ticks, last_trade_price) VALUES (?,?,?,?,?,?,?,?)",
            (now, SYMBOL, self._bid, self._bid_size, self._ask, self._ask_size, spread_ticks, self._last_trade),
        )
        self._tick_count += 1
        if self._tick_count % 100 == 0:
            self._con.commit()
            elapsed = (now - self._start_time) / 3600
            logger.info(
                f"[{self._tick_count} ticks, {elapsed:.1f}h] "
                f"bid={self._bid:.2f}x{self._bid_size} ask={self._ask:.2f}x{self._ask_size} "
                f"spread={spread_ticks:.1f} ticks last={self._last_trade:.2f}"
            )

    async def run(self):
        ssl_ctx = self._build_ssl()
        logger.info(f"Connecting to {URI} for {SYMBOL}.{EXCHANGE} spread logging...")

        ws = await websockets.connect(URI, ssl=ssl_ctx, ping_interval=None)

        # Login to TICKER_PLANT
        rq = request_login_pb2.RequestLogin()
        rq.template_id = 10
        rq.template_version = "3.9"
        rq.user_msg.append("spread-logger")
        rq.user = USER
        rq.password = PASS
        rq.app_name = "SpreadLogger"
        rq.app_version = "1.0"
        rq.system_name = SYSTEM
        rq.infra_type = request_login_pb2.RequestLogin.SysInfraType.TICKER_PLANT

        await ws.send(rq.SerializeToString())
        buf = await ws.recv()
        rp = response_login_pb2.ResponseLogin()
        rp.ParseFromString(buf)
        if not rp.rp_code or rp.rp_code[0] != "0":
            logger.error(f"Login failed: {list(rp.rp_code)}")
            return
        logger.info("TICKER_PLANT login OK")

        # Subscribe to market data
        rq2 = request_market_data_update_pb2.RequestMarketDataUpdate()
        rq2.template_id = 100
        rq2.user_msg.append("spread-logger")
        rq2.symbol = SYMBOL
        rq2.exchange = EXCHANGE
        rq2.request = request_market_data_update_pb2.RequestMarketDataUpdate.Request.SUBSCRIBE
        rq2.update_bits = (
            request_market_data_update_pb2.RequestMarketDataUpdate.UpdateBits.LAST_TRADE
            | request_market_data_update_pb2.RequestMarketDataUpdate.UpdateBits.BBO
        )
        await ws.send(rq2.SerializeToString())

        buf = await asyncio.wait_for(ws.recv(), timeout=10)
        rp2 = response_market_data_update_pb2.ResponseMarketDataUpdate()
        rp2.ParseFromString(buf)
        if rp2.rp_code and rp2.rp_code[0] != "0":
            logger.error(f"Subscribe failed: {list(rp2.rp_code)}")
            return
        logger.info(f"Subscribed to {SYMBOL}.{EXCHANGE} - logging spread to {DB_PATH}")
        logger.info("Press Ctrl+C to stop. Target: 48 hours of data.")

        # Heartbeat + listen
        async def heartbeat():
            while True:
                await asyncio.sleep(30)
                rq = request_heartbeat_pb2.RequestHeartbeat()
                rq.template_id = 18
                await ws.send(rq.SerializeToString())

        async def listen():
            last_recv = asyncio.get_event_loop().time()
            while True:
                try:
                    buf = await asyncio.wait_for(ws.recv(), timeout=90)
                    last_recv = asyncio.get_event_loop().time()
                except asyncio.TimeoutError:
                    logger.warning("No data in 90s, reconnecting...")
                    break

                base = base_pb2.Base()
                base.ParseFromString(buf)
                tid = base.template_id

                if tid == 150:  # LastTrade
                    msg = last_trade_pb2.LastTrade()
                    msg.ParseFromString(buf)
                    if msg.presence_bits & last_trade_pb2.LastTrade.PresenceBits.LAST_TRADE:
                        self._last_trade = msg.trade_price
                        self._log_spread()
                elif tid == 151:  # BBO
                    msg = best_bid_offer_pb2.BestBidOffer()
                    msg.ParseFromString(buf)
                    if msg.presence_bits & best_bid_offer_pb2.BestBidOffer.PresenceBits.BID:
                        self._bid = msg.bid_price
                        self._bid_size = msg.bid_size
                    if msg.presence_bits & best_bid_offer_pb2.BestBidOffer.PresenceBits.ASK:
                        self._ask = msg.ask_price
                        self._ask_size = msg.ask_size
                    self._log_spread()
                elif tid == 19:  # Heartbeat
                    pass

        await asyncio.gather(heartbeat(), listen())


async def main():
    logger = SpreadLogger()
    while True:
        try:
            await logger.run()
        except Exception as e:
            logging.error(f"Spread logger error: {e}, reconnecting in 10s...")
            await asyncio.sleep(10)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info(f"Spread logger stopped.")
