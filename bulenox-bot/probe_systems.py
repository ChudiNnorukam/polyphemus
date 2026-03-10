"""
Probe the Rithmic Test gateway to list all available system names.
No credentials needed — RequestRithmicSystemInfo is a pre-auth request.

Usage: python probe_systems.py

If "Rithmic Paper Trading" appears in the output, we don't need a new URI.
Just change RITHMIC_SYSTEM in .env to "Rithmic Paper Trading".
"""
import asyncio
import pathlib
import ssl
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent / "proto"))

import websockets
import request_rithmic_system_info_pb2
import response_rithmic_system_info_pb2
import base_pb2


URI = "wss://rituz00100.rithmic.com:443"


def build_ssl():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cert = pathlib.Path(__file__).parent / "proto" / "rithmic_ssl_cert_auth_params"
    ctx.load_verify_locations(cert)
    return ctx


async def probe():
    ssl_ctx = build_ssl()
    print(f"Connecting to {URI} ...")
    async with websockets.connect(URI, ssl=ssl_ctx, ping_interval=None) as ws:
        print("Connected. Sending RequestRithmicSystemInfo (template_id=16) ...")

        rq = request_rithmic_system_info_pb2.RequestRithmicSystemInfo()
        rq.template_id = 16
        rq.user_msg.append("probe")
        await ws.send(rq.SerializeToString())

        systems = []
        for _ in range(20):  # read up to 20 responses
            try:
                buf = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                break

            # peek at template_id in base
            base = base_pb2.Base()
            base.ParseFromString(buf)

            rp = response_rithmic_system_info_pb2.ResponseRithmicSystemInfo()
            rp.ParseFromString(buf)

            if rp.system_name:
                for name in rp.system_name:
                    systems.append(name)
                    print(f"  System: {name!r}")

            # rp_code "0" on each item, final message has no system_name
            if not rp.system_name and rp.rp_code:
                print(f"  End of list (rp_code={list(rp.rp_code)})")
                break

        print()
        if systems:
            print("Available systems:")
            for s in systems:
                print(f"  - {s}")
            if "Rithmic Paper Trading" in systems:
                print()
                print("ACTION: 'Rithmic Paper Trading' is on this gateway.")
                print("Just set RITHMIC_SYSTEM=Rithmic Paper Trading in .env — same URI.")
            else:
                print()
                print("'Rithmic Paper Trading' NOT on this gateway.")
                print("Need a different URI from Bulenox support.")
        else:
            print("No system names returned.")


asyncio.run(probe())
