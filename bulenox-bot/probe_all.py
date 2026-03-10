"""Probe ALL Rithmic gateways extracted from R Trader Pro for available systems."""
import asyncio
import pathlib
import ssl
import sys
import socket

sys.path.insert(0, str(pathlib.Path(__file__).parent / "proto"))

import websockets
import request_rithmic_system_info_pb2
import response_rithmic_system_info_pb2

HOSTS = [
    "rittz00100.rithmic.com",
    "rituz00100.rithmic.com",
    "rprotocol-au.rithmic.com",
    "rprotocol-beta.rithmic.com",
    "rprotocol-br.rithmic.com",
    "rprotocol-hk.rithmic.com",
    "rprotocol-ie.rithmic.com",
    "rprotocol-in.rithmic.com",
    "rprotocol-jp.rithmic.com",
    "rprotocol-kr.rithmic.com",
    "rprotocol-mobile.rithmic.com",
    "rprotocol-sg.rithmic.com",
]

PORTS = [443, 65000]


def build_ssl():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cert = pathlib.Path(__file__).parent / "proto" / "rithmic_ssl_cert_auth_params"
    ctx.load_verify_locations(cert)
    return ctx


async def probe_one(host, port, ssl_ctx):
    uri = f"wss://{host}:{port}"
    try:
        socket.getaddrinfo(host, None, socket.AF_INET)
    except socket.gaierror:
        return uri, None, "DNS_FAIL"

    try:
        ws = await asyncio.wait_for(
            websockets.connect(uri, ssl=ssl_ctx, ping_interval=None),
            timeout=6.0
        )
    except Exception as e:
        return uri, None, f"CONN_FAIL:{type(e).__name__}"

    try:
        rq = request_rithmic_system_info_pb2.RequestRithmicSystemInfo()
        rq.template_id = 16
        rq.user_msg.append("probe")
        await ws.send(rq.SerializeToString())

        systems = []
        for _ in range(30):
            try:
                buf = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except Exception:
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
        return uri, None, f"PROBE_FAIL:{type(e).__name__}"


async def main():
    ssl_ctx = build_ssl()
    print("Probing ALL Rithmic gateways from R Trader Pro...\n")

    tasks = []
    for host in HOSTS:
        for port in PORTS:
            tasks.append(probe_one(host, port, ssl_ctx))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            continue
        uri, systems, status = result
        if status == "DNS_FAIL":
            continue  # skip DNS failures silently
        elif systems:
            target = any("paper" in s.lower() or "bulenox" in s.lower() for s in systems)
            marker = " *** TARGET ***" if target else ""
            print(f"  {uri:50s} -> {systems}{marker}")
        elif "CONN_FAIL" in status:
            print(f"  {uri:50s} -> {status}")
        else:
            print(f"  {uri:50s} -> {status} (empty)")

    print("\nDone.")


asyncio.run(main())
