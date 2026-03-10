"""
Probe multiple potential Rithmic gateway URIs to find which one hosts
"Rithmic Paper Trading" or "Bulenox" systems.
"""
import asyncio
import pathlib
import ssl
import sys
import socket

sys.path.insert(0, str(pathlib.Path(__file__).parent / "proto"))

import websockets
import request_rithmic_system_info_pb2
import response_rithmic_system_info_pb2
import base_pb2

# Known and guessed Rithmic gateway patterns
GATEWAYS = [
    "wss://rituz00100.rithmic.com:443",      # Test (confirmed)
    "wss://rituz00100.rithmic.com:65000",     # Test alt port
    "wss://rprotocol-api.rithmic.com:443",    # Possible API gateway
    "wss://rprotocol-api.rithmic.com:65000",
    "wss://rituz.rithmic.com:443",
    "wss://rituz.rithmic.com:65000",
    "wss://paper.rithmic.com:443",
    "wss://paper.rithmic.com:65000",
    "wss://rituz00101.rithmic.com:443",
    "wss://rituz00101.rithmic.com:65000",
    "wss://rituz00200.rithmic.com:443",
    "wss://rituz00200.rithmic.com:65000",
]


def build_ssl():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cert = pathlib.Path(__file__).parent / "proto" / "rithmic_ssl_cert_auth_params"
    ctx.load_verify_locations(cert)
    return ctx


async def probe_one(uri: str, ssl_ctx):
    """Probe a single gateway for available systems."""
    # First check DNS resolves
    host = uri.split("//")[1].split(":")[0]
    try:
        socket.getaddrinfo(host, None, socket.AF_INET)
    except socket.gaierror:
        return uri, None, "DNS_FAIL"

    try:
        ws = await asyncio.wait_for(
            websockets.connect(uri, ssl=ssl_ctx, ping_interval=None),
            timeout=5.0
        )
    except Exception as e:
        return uri, None, f"CONNECT_FAIL: {type(e).__name__}"

    try:
        rq = request_rithmic_system_info_pb2.RequestRithmicSystemInfo()
        rq.template_id = 16
        rq.user_msg.append("probe")
        await ws.send(rq.SerializeToString())

        systems = []
        for _ in range(20):
            try:
                buf = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                break

            rp = response_rithmic_system_info_pb2.ResponseRithmicSystemInfo()
            rp.ParseFromString(buf)

            if rp.system_name:
                systems.extend(rp.system_name)

            if not rp.system_name and rp.rp_code:
                break

        await ws.close()
        return uri, systems, "OK"
    except Exception as e:
        return uri, None, f"PROBE_FAIL: {type(e).__name__}"


async def main():
    ssl_ctx = build_ssl()
    print("Probing Rithmic gateways...\n")

    tasks = [probe_one(uri, ssl_ctx) for uri in GATEWAYS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    found_target = False
    for result in results:
        if isinstance(result, Exception):
            continue
        uri, systems, status = result
        if status == "DNS_FAIL":
            print(f"  {uri:50s} -> DNS_FAIL")
        elif status.startswith("CONNECT_FAIL"):
            print(f"  {uri:50s} -> {status}")
        elif systems:
            marker = ""
            for s in systems:
                if "paper" in s.lower() or "bulenox" in s.lower():
                    marker = " *** TARGET ***"
                    found_target = True
            print(f"  {uri:50s} -> {systems}{marker}")
        else:
            print(f"  {uri:50s} -> {status} (no systems)")

    if not found_target:
        print("\nNo gateway with 'Paper Trading' or 'Bulenox' found.")
        print("The URI must be obtained from Bulenox support or Rithmic dev kit.")


asyncio.run(main())
