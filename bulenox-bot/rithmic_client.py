import asyncio
import logging
import pathlib
import ssl
import sys
import uuid
from typing import Callable, Awaitable, Optional

import websockets

# Proto imports from local proto/ package
sys.path.insert(0, str(pathlib.Path(__file__).parent / "proto"))

import base_pb2
import request_login_pb2
import response_login_pb2
import request_login_info_pb2
import response_login_info_pb2
import request_account_list_pb2
import response_account_list_pb2
import request_trade_routes_pb2
import response_trade_routes_pb2
import request_subscribe_for_order_updates_pb2
import response_subscribe_for_order_updates_pb2
import request_new_order_pb2
import response_new_order_pb2
import exchange_order_notification_pb2
import rithmic_order_notification_pb2
import request_heartbeat_pb2
import request_logout_pb2

from config import BulenoxConfig

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30  # seconds


class RithmicClient:
    def __init__(
        self,
        cfg: BulenoxConfig,
        on_fill: Optional[Callable[[dict], Awaitable[None]]] = None,
        on_order_ack: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self._cfg = cfg
        self._on_fill = on_fill
        self._on_order_ack = on_order_ack
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ssl_context: Optional[ssl.SSLContext] = None

        self._fcm_id: str = ""
        self._ib_id: str = ""
        self._account_id: str = ""
        self._trade_route: str = ""
        self._ready: asyncio.Event = asyncio.Event()
        self._pending_orders: dict[str, asyncio.Event] = {}  # basket_id -> fill event
        self._last_order_ack: Optional[str] = None  # last acknowledged basket_id

    def _build_ssl(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cert = pathlib.Path(__file__).parent / "proto" / "rithmic_ssl_cert_auth_params"
        ctx.load_verify_locations(cert)
        return ctx

    async def connect(self) -> None:
        if self._cfg.dry_run:
            logger.info("DRY RUN — Rithmic connection skipped")
            while True:
                await asyncio.sleep(3600)

        self._ssl_context = self._build_ssl()
        uri = self._cfg.rithmic_uri
        backoff = 5

        while True:
            try:
                logger.info(f"Connecting to Rithmic: {uri}")
                self._ws = await websockets.connect(uri, ssl=self._ssl_context, ping_interval=None)
                logger.info("Rithmic WS connected")
                backoff = 5  # reset on successful connection

                self._ready.clear()
                await self._login()
                await self._get_login_info()
                await self._subscribe_order_updates()
                self._ready.set()
                logger.info(f"Rithmic ready | account={self._account_id} | trade_route={self._trade_route}")

                await asyncio.gather(
                    self._heartbeat_loop(),
                    self._listen_loop(),
                )
            except RuntimeError as e:
                # Login failures (wrong gateway, bad creds) are permanent — back off hard
                self._ready.clear()
                self._ws = None
                backoff = min(backoff * 2, 120)
                logger.warning(f"Rithmic disconnected: {e} — reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
            except Exception as e:
                self._ready.clear()
                self._ws = None
                logger.warning(f"Rithmic disconnected: {e} — reconnecting in {backoff}s")
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
        rq.infra_type = request_login_pb2.RequestLogin.SysInfraType.ORDER_PLANT

        await self._ws.send(rq.SerializeToString())
        buf = await self._ws.recv()

        rp = response_login_pb2.ResponseLogin()
        rp.ParseFromString(buf)

        if not rp.rp_code or rp.rp_code[0] != "0":
            raise RuntimeError(f"Rithmic login failed: {list(rp.rp_code)}")
        logger.info(f"Rithmic login OK | fcm_id={rp.fcm_id} ib_id={rp.ib_id}")

    async def _get_login_info(self) -> None:
        rq = request_login_info_pb2.RequestLoginInfo()
        rq.template_id = 300
        rq.user_msg.append("hello")

        await self._ws.send(rq.SerializeToString())
        buf = await self._ws.recv()

        rp = response_login_info_pb2.ResponseLoginInfo()
        rp.ParseFromString(buf)

        if not rp.rp_code or rp.rp_code[0] != "0":
            raise RuntimeError(f"RequestLoginInfo failed: {list(rp.rp_code)}")

        await self._list_accounts(rp.fcm_id, rp.ib_id, rp.user_type)
        await self._list_trade_routes()

    async def _list_accounts(self, fcm_id: str, ib_id: str, user_type) -> None:
        rq = request_account_list_pb2.RequestAccountList()
        rq.template_id = 302
        rq.user_msg.append("hello")
        rq.fcm_id = fcm_id
        rq.ib_id = ib_id
        rq.user_type = user_type

        await self._ws.send(rq.SerializeToString())

        while True:
            buf = await self._ws.recv()
            rp = response_account_list_pb2.ResponseAccountList()
            rp.ParseFromString(buf)

            if (
                rp.rq_handler_rp_code
                and rp.rq_handler_rp_code[0] == "0"
                and rp.fcm_id
                and rp.ib_id
                and rp.account_id
                and not self._account_id
            ):
                self._fcm_id = rp.fcm_id
                self._ib_id = rp.ib_id
                self._account_id = rp.account_id
                logger.info(f"Account: {self._account_id}")

            if rp.rp_code:
                break

    async def _list_trade_routes(self) -> None:
        rq = request_trade_routes_pb2.RequestTradeRoutes()
        rq.template_id = 310
        rq.user_msg.append("hello")
        rq.subscribe_for_updates = False

        await self._ws.send(rq.SerializeToString())

        while True:
            buf = await self._ws.recv()
            rp = response_trade_routes_pb2.ResponseTradeRoutes()
            rp.ParseFromString(buf)

            if (
                rp.rq_handler_rp_code
                and rp.rq_handler_rp_code[0] == "0"
                and rp.fcm_id == self._fcm_id
                and rp.ib_id == self._ib_id
                and rp.exchange == self._cfg.exchange
                and not self._trade_route
            ):
                self._trade_route = rp.trade_route
                logger.info(f"Trade route: {self._trade_route}")

            if rp.rp_code:
                break

        if not self._trade_route:
            raise RuntimeError(f"No trade route found for exchange={self._cfg.exchange}")

    async def _subscribe_order_updates(self) -> None:
        rq = request_subscribe_for_order_updates_pb2.RequestSubscribeForOrderUpdates()
        rq.template_id = 308
        rq.user_msg.append("hello")
        rq.fcm_id = self._fcm_id
        rq.ib_id = self._ib_id
        rq.account_id = self._account_id

        await self._ws.send(rq.SerializeToString())
        logger.info("Subscribed to order updates")

    async def place_order(self, side: str) -> str:
        if self._cfg.dry_run:
            fake_id = f"DRY-{uuid.uuid4().hex[:8]}"
            logger.info(f"[DRY RUN] place_order side={side} basket_id={fake_id}")
            return fake_id

        rq = request_new_order_pb2.RequestNewOrder()
        rq.template_id = 312
        rq.user_msg.append("hello")
        rq.fcm_id = self._fcm_id
        rq.ib_id = self._ib_id
        rq.account_id = self._account_id
        rq.exchange = self._cfg.exchange
        rq.symbol = self._cfg.symbol
        rq.quantity = self._cfg.contracts
        rq.trade_route = self._trade_route
        rq.price_type = request_new_order_pb2.RequestNewOrder.PriceType.MARKET
        rq.duration = request_new_order_pb2.RequestNewOrder.Duration.DAY
        rq.manual_or_auto = request_new_order_pb2.RequestNewOrder.OrderPlacement.MANUAL

        if side == "BUY":
            rq.transaction_type = request_new_order_pb2.RequestNewOrder.TransactionType.BUY
        else:
            rq.transaction_type = request_new_order_pb2.RequestNewOrder.TransactionType.SELL

        rq.trailing_stop = True
        rq.trail_by_ticks = self._cfg.stop_loss_ticks

        await self._ws.send(rq.SerializeToString())
        logger.info(f"Order sent: side={side} symbol={self._cfg.symbol} trailing_stop=True trail_by_ticks={self._cfg.stop_loss_ticks}")

        # Wait for order acknowledgment with timeout
        self._last_order_ack = None
        try:
            ack_event = asyncio.Event()
            self._pending_orders["__next__"] = ack_event
            await asyncio.wait_for(ack_event.wait(), timeout=10.0)
            basket_id = self._last_order_ack or ""
            if basket_id:
                logger.info(f"Order acknowledged: basket_id={basket_id}")
            return basket_id
        except asyncio.TimeoutError:
            logger.error(f"Order acknowledgment timeout (10s) for {side} {self._cfg.symbol}. Order may be orphaned.")
            del self._pending_orders["__next__"]
            return ""

    async def _listen_loop(self) -> None:
        self._last_recv_ts = asyncio.get_event_loop().time()
        while True:
            try:
                buf = await asyncio.wait_for(self._ws.recv(), timeout=90)
                self._last_recv_ts = asyncio.get_event_loop().time()
                await self._dispatch(buf)
            except asyncio.TimeoutError:
                elapsed = asyncio.get_event_loop().time() - self._last_recv_ts
                raise RuntimeError(f"Heartbeat timeout: no message in {elapsed:.0f}s")
            except websockets.ConnectionClosed:
                raise RuntimeError("WebSocket connection closed")
            except Exception as e:
                logger.error(f"Dispatch error: {e}")

    async def _dispatch(self, buf: bytes) -> None:
        base = base_pb2.Base()
        base.ParseFromString(buf)
        tid = base.template_id

        if tid == 19:
            logger.debug("Heartbeat response")
        elif tid == 313:
            rp = response_new_order_pb2.ResponseNewOrder()
            rp.ParseFromString(buf)
            logger.info(f"ResponseNewOrder: basket_id={rp.basket_id} rp_code={list(rp.rp_code)}")
            # Signal the pending order wait
            if rp.basket_id:
                self._last_order_ack = rp.basket_id
                if "__next__" in self._pending_orders:
                    self._pending_orders["__next__"].set()
                    del self._pending_orders["__next__"]
            if rp.basket_id and self._on_order_ack:
                await self._on_order_ack(rp.basket_id)
        elif tid == 351:
            msg = rithmic_order_notification_pb2.RithmicOrderNotification()
            msg.ParseFromString(buf)
            logger.info(f"RithmicOrderNotification: basket_id={msg.basket_id} notify_type={msg.notify_type}")
        elif tid == 352:
            msg = exchange_order_notification_pb2.ExchangeOrderNotification()
            msg.ParseFromString(buf)
            logger.info(
                f"ExchangeOrderNotification: basket_id={msg.basket_id} "
                f"notify_type={msg.notify_type} fill_price={msg.fill_price} fill_size={msg.fill_size}"
            )
            if msg.notify_type == exchange_order_notification_pb2.ExchangeOrderNotification.FILL:
                if self._on_fill:
                    await self._on_fill({
                        "basket_id": msg.basket_id,
                        "fill_price": msg.fill_price,
                        "fill_size": msg.fill_size,
                        "transaction_type": msg.transaction_type,
                    })
        else:
            logger.debug(f"Unhandled template_id={tid}")

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if self._ws is None:
                break
            try:
                rq = request_heartbeat_pb2.RequestHeartbeat()
                rq.template_id = 18
                await self._ws.send(rq.SerializeToString())
                logger.debug("Heartbeat sent")
            except Exception as e:
                logger.warning(f"Heartbeat failed: {e}")
                break

    async def logout(self) -> None:
        if self._ws and self._ws.open:
            rq = request_logout_pb2.RequestLogout()
            rq.template_id = 12
            rq.user_msg.append("bye")
            await self._ws.send(rq.SerializeToString())
            await self._ws.close(1000, "done")
            logger.info("Rithmic logged out")
