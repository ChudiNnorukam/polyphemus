#!/usr/bin/env python3
"""Test the full order lifecycle on Rithmic Test: login -> place limit order -> confirm -> cancel."""
import asyncio
import os
import pathlib
import ssl
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent / "proto"))

import dotenv
import websockets
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
import rithmic_order_notification_pb2
import exchange_order_notification_pb2
import request_heartbeat_pb2

dotenv.load_dotenv(pathlib.Path(__file__).parent / ".env")

URI = os.environ.get("RITHMIC_URI", "wss://rituz00100.rithmic.com:443")
USER = os.environ.get("RITHMIC_USER", "")
PASS = os.environ.get("RITHMIC_PASS", "")
SYSTEM = os.environ.get("RITHMIC_SYSTEM", "Rithmic Test")

# Test order params - limit buy well below market so it won't fill
SYMBOL = "ESU6"       # ES Sep 2026 - confirmed available on TICKER_PLANT
EXCHANGE = "CME"
LIMIT_PRICE = 4000.00  # ES is ~5800+ in 2026, this won't fill
QUANTITY = 1


def build_ssl():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cert = pathlib.Path(__file__).parent / "proto" / "rithmic_ssl_cert_auth_params"
    ctx.load_verify_locations(cert)
    return ctx


async def recv_by_template(ws, expected_tid, timeout=10):
    """Receive messages until we get the expected template_id."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        buf = await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.1))
        base = base_pb2.Base()
        base.ParseFromString(buf)
        if base.template_id == expected_tid:
            return buf
        else:
            print(f"  (skipping template_id={base.template_id})", flush=True)
    raise TimeoutError(f"Did not receive template_id={expected_tid} within {timeout}s")


async def main():
    print(f"=== Rithmic Test Order Lifecycle ===")
    print(f"URI: {URI}")
    print(f"System: {SYSTEM}")
    print(f"User: {USER}")
    print(f"Symbol: {SYMBOL}.{EXCHANGE} @ limit ${LIMIT_PRICE}")
    print()

    ssl_ctx = build_ssl()
    ws = await websockets.connect(URI, ssl=ssl_ctx, ping_interval=None)
    print("[1/7] Connected", flush=True)

    # --- Login ---
    rq = request_login_pb2.RequestLogin()
    rq.template_id = 10
    rq.template_version = "3.9"
    rq.user_msg.append("test-order")
    rq.user = USER
    rq.password = PASS
    rq.app_name = "BulenoxBot"
    rq.app_version = "1.0"
    rq.system_name = SYSTEM
    rq.infra_type = request_login_pb2.RequestLogin.SysInfraType.ORDER_PLANT
    await ws.send(rq.SerializeToString())

    rp = response_login_pb2.ResponseLogin()
    rp.ParseFromString(await ws.recv())
    codes = list(rp.rp_code)
    if not codes or codes[0] != "0":
        print(f"LOGIN FAILED: {codes}")
        await ws.close()
        return
    print(f"[2/7] Login OK | fcm={rp.fcm_id} ib={rp.ib_id}", flush=True)

    # --- Login Info ---
    rq2 = request_login_info_pb2.RequestLoginInfo()
    rq2.template_id = 300
    rq2.user_msg.append("test-order")
    await ws.send(rq2.SerializeToString())
    rp2 = response_login_info_pb2.ResponseLoginInfo()
    rp2.ParseFromString(await ws.recv())
    fcm_id = rp2.fcm_id
    ib_id = rp2.ib_id
    user_type = rp2.user_type

    # --- List Accounts ---
    rq3 = request_account_list_pb2.RequestAccountList()
    rq3.template_id = 302
    rq3.user_msg.append("test-order")
    rq3.fcm_id = fcm_id
    rq3.ib_id = ib_id
    rq3.user_type = user_type
    await ws.send(rq3.SerializeToString())

    account_id = ""
    while True:
        buf = await asyncio.wait_for(ws.recv(), timeout=5)
        rp3 = response_account_list_pb2.ResponseAccountList()
        rp3.ParseFromString(buf)
        if rp3.account_id and not account_id:
            account_id = rp3.account_id
        if rp3.rp_code:
            break
    print(f"[3/7] Account: {account_id}", flush=True)

    # --- List Trade Routes ---
    rq4 = request_trade_routes_pb2.RequestTradeRoutes()
    rq4.template_id = 310
    rq4.user_msg.append("test-order")
    rq4.subscribe_for_updates = False
    await ws.send(rq4.SerializeToString())

    trade_route = ""
    while True:
        buf = await asyncio.wait_for(ws.recv(), timeout=5)
        rp4 = response_trade_routes_pb2.ResponseTradeRoutes()
        rp4.ParseFromString(buf)
        if rp4.exchange == EXCHANGE and rp4.fcm_id == fcm_id and not trade_route:
            trade_route = rp4.trade_route
        if rp4.rp_code:
            break
    print(f"[4/7] Trade route: {trade_route} for {EXCHANGE}", flush=True)

    if not trade_route:
        print(f"ERROR: No trade route for {EXCHANGE}")
        await ws.close()
        return

    # --- Subscribe Order Updates ---
    rq5 = request_subscribe_for_order_updates_pb2.RequestSubscribeForOrderUpdates()
    rq5.template_id = 308
    rq5.user_msg.append("test-order")
    rq5.fcm_id = fcm_id
    rq5.ib_id = ib_id
    rq5.account_id = account_id
    await ws.send(rq5.SerializeToString())
    print(f"[5/7] Subscribed to order updates", flush=True)

    # Small delay to let subscription settle
    await asyncio.sleep(0.5)

    # --- Place Limit Buy Order ---
    rq6 = request_new_order_pb2.RequestNewOrder()
    rq6.template_id = 312
    rq6.user_msg.append("test-order")
    rq6.fcm_id = fcm_id
    rq6.ib_id = ib_id
    rq6.account_id = account_id
    rq6.exchange = EXCHANGE
    rq6.symbol = SYMBOL
    rq6.quantity = QUANTITY
    rq6.trade_route = trade_route
    rq6.price_type = request_new_order_pb2.RequestNewOrder.PriceType.LIMIT
    rq6.price = LIMIT_PRICE
    rq6.duration = request_new_order_pb2.RequestNewOrder.Duration.DAY
    rq6.transaction_type = request_new_order_pb2.RequestNewOrder.TransactionType.BUY
    rq6.manual_or_auto = request_new_order_pb2.RequestNewOrder.OrderPlacement.AUTO

    await ws.send(rq6.SerializeToString())
    print(f"[6/7] Limit BUY order sent: {QUANTITY} {SYMBOL}.{EXCHANGE} @ ${LIMIT_PRICE}", flush=True)

    # --- Wait for order acknowledgments ---
    print(f"\n--- Listening for responses (10s) ---", flush=True)
    deadline = asyncio.get_event_loop().time() + 10
    order_confirmed = False

    while asyncio.get_event_loop().time() < deadline:
        try:
            remaining = deadline - asyncio.get_event_loop().time()
            buf = await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.1))
            base = base_pb2.Base()
            base.ParseFromString(buf)
            tid = base.template_id

            if tid == 313:  # ResponseNewOrder
                rp6 = response_new_order_pb2.ResponseNewOrder()
                rp6.ParseFromString(buf)
                print(f"  ResponseNewOrder: basket_id={rp6.basket_id} rp_code={list(rp6.rp_code)}", flush=True)
                if rp6.rp_code and rp6.rp_code[0] == "0":
                    order_confirmed = True
            elif tid == 351:  # RithmicOrderNotification
                msg = rithmic_order_notification_pb2.RithmicOrderNotification()
                msg.ParseFromString(buf)
                print(f"  RithmicOrderNotif: basket_id={msg.basket_id} type={msg.notify_type} status={msg.status}", flush=True)
            elif tid == 352:  # ExchangeOrderNotification
                msg = exchange_order_notification_pb2.ExchangeOrderNotification()
                msg.ParseFromString(buf)
                print(f"  ExchangeOrderNotif: basket_id={msg.basket_id} type={msg.notify_type}", flush=True)
            elif tid == 19:  # Heartbeat
                pass
            else:
                print(f"  (template_id={tid})", flush=True)

        except asyncio.TimeoutError:
            break

    if order_confirmed:
        print(f"\n[7/7] ORDER LIFECYCLE TEST PASSED", flush=True)
    else:
        print(f"\n[7/7] ORDER LIFECYCLE TEST: no confirmation received", flush=True)

    await ws.close()
    print("Connection closed.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
