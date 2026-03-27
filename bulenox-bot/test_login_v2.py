#!/usr/bin/env python3
"""Minimal Rithmic login test using dev kit v0.89 protos."""
import asyncio
import os
import pathlib
import ssl
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent / "rprotocol_devkit" / "0.89.0.0" / "samples" / "samples.py"))

import dotenv
import websockets
import request_login_pb2
import response_login_pb2
import request_rithmic_system_info_pb2
import response_rithmic_system_info_pb2

dotenv.load_dotenv(pathlib.Path(__file__).parent / ".env")

URI = os.environ.get("RITHMIC_URI", "wss://rprotocol.rithmic.com:443")
USER = os.environ.get("RITHMIC_USER", "")
PASS = os.environ.get("RITHMIC_PASS", "")
SYSTEM = os.environ.get("RITHMIC_SYSTEM", "Rithmic Paper Trading")

def build_ssl():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cert = pathlib.Path(__file__).parent / "rprotocol_devkit" / "0.89.0.0" / "etc" / "rithmic_ssl_cert_auth_params"
    ctx.load_verify_locations(cert)
    return ctx


async def main():
    print(f"User: {USER}", flush=True)
    print(f"URI: {URI}", flush=True)
    print(f"System: {SYSTEM}", flush=True)
    print(flush=True)

    ssl_ctx = build_ssl()
    ws = await websockets.connect(URI, ssl=ssl_ctx, ping_interval=3)
    print("Connected.", flush=True)

    # --- Login ---
    rq = request_login_pb2.RequestLogin()
    rq.template_id = 10
    rq.template_version = "3.9"
    rq.user_msg.append("hello")
    rq.user = USER
    rq.password = PASS
    rq.app_name = "BulenoxBot"
    rq.app_version = "1.0"
    rq.system_name = SYSTEM
    rq.infra_type = request_login_pb2.RequestLogin.SysInfraType.ORDER_PLANT

    buf = rq.SerializeToString()
    print(f"Sending login ({len(buf)} bytes)...", flush=True)
    await ws.send(buf)

    try:
        rp_buf = await asyncio.wait_for(ws.recv(), timeout=10)
    except asyncio.TimeoutError:
        print("TIMEOUT waiting for login response", flush=True)
        await ws.close()
        return
    except websockets.ConnectionClosed as e:
        print(f"Connection closed: {e}", flush=True)
        return

    rp = response_login_pb2.ResponseLogin()
    rp.ParseFromString(rp_buf)

    rp_codes = list(rp.rp_code)
    print(f"\n--- Login Response ---", flush=True)
    print(f"  rp_code: {rp_codes}", flush=True)
    print(f"  user_msg: {list(rp.user_msg)}", flush=True)
    print(f"  fcm_id: {rp.fcm_id}", flush=True)
    print(f"  ib_id: {rp.ib_id}", flush=True)
    print(f"  unique_user_id: {rp.unique_user_id}", flush=True)
    print(f"  heartbeat_interval: {rp.heartbeat_interval}", flush=True)

    if rp_codes and rp_codes[0] == "0":
        print("\n*** LOGIN SUCCESS ***", flush=True)
    else:
        print(f"\n*** LOGIN FAILED ***", flush=True)

    await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
