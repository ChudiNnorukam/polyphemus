import asyncio
import logging
import pathlib
import ssl
import sys
import time
from typing import Callable, Awaitable, Optional

import websockets

sys.path.insert(0, str(pathlib.Path(__file__).parent / "proto"))

import base_pb2
import request_login_pb2
import response_login_pb2
import request_market_data_update_pb2
import response_market_data_update_pb2
import last_trade_pb2
import best_bid_offer_pb2
import request_heartbeat_pb2
import request_logout_pb2

from config import BulenoxConfig

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30


class TickerPlant:
    """Rithmic TICKER_PLANT connection for real-time MBT market data.

    Separate WS connection from ORDER_PLANT. Provides last_trade and
    best_bid_offer updates for TP/SL price checks.
    """

    def __init__(
        self,
        cfg: BulenoxConfig,
        on_last_trade: Optional[Callable[[float, int], Awaitable[None]]] = None,
        on_bbo: Optional[Callable[[float, int, float, int], Awaitable[None]]] = None,
    ):
        self._cfg = cfg
        self._on_last_trade = on_last_trade  # (price, size)
        self._on_bbo = on_bbo  # (bid_price, bid_size, ask_price, ask_size)
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ssl_context: Optional[ssl.SSLContext] = None
        self._ready: asyncio.Event = asyncio.Event()

        # Public price state - read by position monitor
        self.last_trade_price: float = 0.0
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.bid_size: int = 0
        self.ask_size: int = 0
        self.last_update_ts: float = 0.0

    @property
    def mid_price(self) -> float:
        if self.best_bid > 0 and self.best_ask > 0:
            return (self.best_bid + self.best_ask) / 2
        return self.last_trade_price

    @property
    def is_stale(self) -> bool:
        if self.last_update_ts == 0:
            return True
        return (time.monotonic() - self.last_update_ts) > 45

    def _build_ssl(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cert = pathlib.Path(__file__).parent / "proto" / "rithmic_ssl_cert_auth_params"
        ctx.load_verify_locations(cert)
        return ctx

    async def connect(self) -> None:
        if self._cfg.dry_run:
            logger.info("DRY RUN - TICKER_PLANT connection skipped")
            self._ready.set()
            while True:
                await asyncio.sleep(3600)

        self._ssl_context = self._build_ssl()
        uri = self._cfg.rithmic_uri
        backoff = 5
        consecutive_failures = 0
        MAX_CONSECUTIVE_FAILURES = 5

        while True:
            try:
                logger.info(f"TICKER_PLANT connecting to: {uri}")
                self._ws = await websockets.connect(
                    uri, ssl=self._ssl_context, ping_interval=None
                )
                logger.info("TICKER_PLANT WS connected")
                backoff = 5

                self._ready.clear()
                await self._login()
                await self._subscribe_market_data()
                consecutive_failures = 0  # only reset after subscribe succeeds
                self._ready.set()
                logger.info(
                    f"TICKER_PLANT ready | symbol={self._cfg.symbol} exchange={self._cfg.exchange}"
                )

                await asyncio.gather(
                    self._heartbeat_loop(),
                    self._listen_loop(),
                )
            except RuntimeError as e:
                self._ready.clear()
                self._ws = None
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.warning(
                        f"TICKER_PLANT disabled after {MAX_CONSECUTIVE_FAILURES} consecutive failures: {e}. "
                        f"Bot will use Binance/Coinbase feed for price data."
                    )
                    self._ready.set()  # unblock anything waiting
                    while True:
                        await asyncio.sleep(3600)
                backoff = min(backoff * 2, 120)
                logger.warning(f"TICKER_PLANT disconnected: {e} - reconnecting in {backoff}s (attempt {consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
                await asyncio.sleep(backoff)
            except Exception as e:
                self._ready.clear()
                self._ws = None
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.warning(
                        f"TICKER_PLANT disabled after {MAX_CONSECUTIVE_FAILURES} consecutive failures: {e}. "
                        f"Bot will use Binance/Coinbase feed for price data."
                    )
                    self._ready.set()
                    while True:
                        await asyncio.sleep(3600)
                logger.warning(f"TICKER_PLANT disconnected: {e} - reconnecting in {backoff}s (attempt {consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
                await asyncio.sleep(backoff)

    async def wait_ready(self) -> None:
        await self._ready.wait()

    async def _login(self) -> None:
        rq = request_login_pb2.RequestLogin()
        rq.template_id = 10
        rq.template_version = "3.9"
        rq.user_msg.append("hello")
        rq.user = self._cfg.rithmic_user
        rq.password = self._cfg.rithmic_pass
        rq.app_name = self._cfg.rithmic_app_name
        rq.app_version = self._cfg.rithmic_app_version
        rq.system_name = self._cfg.rithmic_system
        rq.infra_type = request_login_pb2.RequestLogin.SysInfraType.TICKER_PLANT

        await self._ws.send(rq.SerializeToString())
        buf = await self._ws.recv()

        rp = response_login_pb2.ResponseLogin()
        rp.ParseFromString(buf)

        if not rp.rp_code or rp.rp_code[0] != "0":
            raise RuntimeError(f"TICKER_PLANT login failed: {list(rp.rp_code)}")
        logger.info("TICKER_PLANT login OK")

    async def _subscribe_market_data(self) -> None:
        rq = request_market_data_update_pb2.RequestMarketDataUpdate()
        rq.template_id = 100
        rq.user_msg.append("hello")
        rq.symbol = self._cfg.symbol
        rq.exchange = self._cfg.exchange
        rq.request = request_market_data_update_pb2.RequestMarketDataUpdate.Request.SUBSCRIBE
        rq.update_bits = (
            request_market_data_update_pb2.RequestMarketDataUpdate.UpdateBits.LAST_TRADE
            | request_market_data_update_pb2.RequestMarketDataUpdate.UpdateBits.BBO
        )

        await self._ws.send(rq.SerializeToString())

        # Wait for subscription confirmation (template 101)
        buf = await asyncio.wait_for(self._ws.recv(), timeout=10)
        rp = response_market_data_update_pb2.ResponseMarketDataUpdate()
        rp.ParseFromString(buf)
        if rp.rp_code and rp.rp_code[0] != "0":
            raise RuntimeError(f"Market data subscribe failed: {list(rp.rp_code)}")
        logger.info(f"TICKER_PLANT subscribed: {self._cfg.symbol}@{self._cfg.exchange}")

    async def _listen_loop(self) -> None:
        self._last_recv_ts = asyncio.get_event_loop().time()
        while True:
            try:
                buf = await asyncio.wait_for(self._ws.recv(), timeout=90)
                self._last_recv_ts = asyncio.get_event_loop().time()
                await self._dispatch(buf)
            except asyncio.TimeoutError:
                elapsed = asyncio.get_event_loop().time() - self._last_recv_ts
                raise RuntimeError(f"TICKER_PLANT heartbeat timeout: no message in {elapsed:.0f}s")
            except websockets.ConnectionClosed:
                raise RuntimeError("TICKER_PLANT WebSocket connection closed")
            except Exception as e:
                logger.error(f"TICKER_PLANT dispatch error: {e}")

    async def _dispatch(self, buf: bytes) -> None:
        base = base_pb2.Base()
        base.ParseFromString(buf)
        tid = base.template_id

        if tid == 19:
            pass  # heartbeat response
        elif tid == 150:
            await self._handle_last_trade(buf)
        elif tid == 151:
            await self._handle_bbo(buf)
        elif tid == 101:
            pass  # late subscription response, already handled
        else:
            logger.debug(f"TICKER_PLANT unhandled template_id={tid}")

    async def _handle_last_trade(self, buf: bytes) -> None:
        msg = last_trade_pb2.LastTrade()
        msg.ParseFromString(buf)

        if msg.presence_bits & last_trade_pb2.LastTrade.PresenceBits.LAST_TRADE:
            self.last_trade_price = msg.trade_price
            self.last_update_ts = time.monotonic()
            logger.debug(
                f"TICKER last_trade: {msg.trade_price} size={msg.trade_size} "
                f"aggressor={'BUY' if msg.aggressor == last_trade_pb2.LastTrade.TransactionType.BUY else 'SELL'}"
            )
            if self._on_last_trade:
                await self._on_last_trade(msg.trade_price, msg.trade_size)

    async def _handle_bbo(self, buf: bytes) -> None:
        msg = best_bid_offer_pb2.BestBidOffer()
        msg.ParseFromString(buf)

        updated = False
        if msg.presence_bits & best_bid_offer_pb2.BestBidOffer.PresenceBits.BID:
            self.best_bid = msg.bid_price
            self.bid_size = msg.bid_size
            updated = True
        if msg.presence_bits & best_bid_offer_pb2.BestBidOffer.PresenceBits.ASK:
            self.best_ask = msg.ask_price
            self.ask_size = msg.ask_size
            updated = True

        if updated:
            self.last_update_ts = time.monotonic()
            logger.debug(
                f"TICKER BBO: bid={self.best_bid}x{self.bid_size} "
                f"ask={self.best_ask}x{self.ask_size}"
            )
            if self._on_bbo:
                await self._on_bbo(self.best_bid, self.bid_size, self.best_ask, self.ask_size)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if self._ws is None:
                break
            try:
                rq = request_heartbeat_pb2.RequestHeartbeat()
                rq.template_id = 18
                await self._ws.send(rq.SerializeToString())
            except Exception as e:
                logger.warning(f"TICKER_PLANT heartbeat failed: {e}")
                break

    async def logout(self) -> None:
        if self._ws and self._ws.open:
            # Unsubscribe first
            try:
                rq = request_market_data_update_pb2.RequestMarketDataUpdate()
                rq.template_id = 100
                rq.user_msg.append("bye")
                rq.symbol = self._cfg.symbol
                rq.exchange = self._cfg.exchange
                rq.request = request_market_data_update_pb2.RequestMarketDataUpdate.Request.UNSUBSCRIBE
                rq.update_bits = (
                    request_market_data_update_pb2.RequestMarketDataUpdate.UpdateBits.LAST_TRADE
                    | request_market_data_update_pb2.RequestMarketDataUpdate.UpdateBits.BBO
                )
                await self._ws.send(rq.SerializeToString())
            except Exception:
                pass

            rq = request_logout_pb2.RequestLogout()
            rq.template_id = 12
            rq.user_msg.append("bye")
            await self._ws.send(rq.SerializeToString())
            await self._ws.close(1000, "done")
            logger.info("TICKER_PLANT logged out")
