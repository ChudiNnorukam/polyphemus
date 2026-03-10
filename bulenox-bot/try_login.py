"""Try logging in to Rithmic gateways with Bulenox credentials."""
import asyncio
import pathlib
import ssl
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent / "proto"))

import websockets
import request_login_pb2
import response_login_pb2

URIS = [
    "wss://rprotocol-beta.rithmic.com:443",
    "wss://rprotocol-mobile.rithmic.com:443",
]
USERS = ["BX97517", "BX97517-01"]
PASS = "eOMFdHaXR6"
SYSTEMS = ["Bulenox", "Rithmic Paper Trading"]


def build_ssl():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cert = pathlib.Path(__file__).parent / "proto" / "rithmic_ssl_cert_auth_params"
    ctx.load_verify_locations(cert)
    return ctx


async def try_login(uri: str, user: str, system_name: str):
    ssl_ctx = build_ssl()
    print(f"\n--- user='{user}' system='{system_name}' on {uri} ---")
    try:
        ws = await asyncio.wait_for(
            websockets.connect(uri, ssl=ssl_ctx, ping_interval=None),
            timeout=8.0
        )
    except Exception as e:
        print(f"  CONNECT FAILED: {e}")
        return

    try:
        rq = request_login_pb2.RequestLogin()
        rq.template_id = 10
        rq.template_version = "3.9"
        rq.user_msg.append("hello")
        rq.user = user
        rq.password = PASS
        rq.app_name = "SampleOrder.py"
        rq.app_version = "0.3.0.0"
        rq.system_name = system_name
        rq.infra_type = request_login_pb2.RequestLogin.SysInfraType.ORDER_PLANT

        await ws.send(rq.SerializeToString())
        buf = await asyncio.wait_for(ws.recv(), timeout=8.0)

        rp = response_login_pb2.ResponseLogin()
        rp.ParseFromString(buf)

        rp_codes = list(rp.rp_code)

        if rp_codes and rp_codes[0] == "0":
            print(f"  LOGIN SUCCESS!")
            print(f"  fcm_id={rp.fcm_id}, ib_id={rp.ib_id}")
            print(f"  unique_user_id={rp.unique_user_id}")
            print(f"  heartbeat_interval={rp.heartbeat_interval}")
        else:
            print(f"  LOGIN FAILED: rp_code={rp_codes}")

        await ws.close()
    except Exception as e:
        print(f"  ERROR: {e}")


async def main():
    for uri in URIS:
        for user in USERS:
            for system in SYSTEMS:
                await try_login(uri, user, system)


asyncio.run(main())
