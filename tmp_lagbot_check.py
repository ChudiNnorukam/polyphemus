#!/usr/bin/env python3
"""Check lagbot wallet balances."""
from web3 import Web3

rpc = "https://polygon-bor-rpc.publicnode.com"
w3 = Web3(Web3.HTTPProvider(rpc))

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

ERC20_ABI = [{"name":"balanceOf","type":"function","stateMutability":"view",
    "inputs":[{"name":"account","type":"address"}],
    "outputs":[{"name":"","type":"uint256"}]}]

usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_NATIVE), abi=ERC20_ABI)

addrs = {
    "Lagbot EOA": "0x1C0523D33b0D1c7Df8Ec450C5318cFcFc32Ce80A",
    "Lagbot Safe": "0x7E69be59E92a396EcCBba344CAe383927fcAD9Ad",
}

for label, addr in addrs.items():
    addr_cs = Web3.to_checksum_address(addr)
    pol = w3.eth.get_balance(addr_cs)
    ue = usdc_e.functions.balanceOf(addr_cs).call()
    un = usdc.functions.balanceOf(addr_cs).call()
    print(f"{label} ({addr_cs}):")
    print(f"  POL: {w3.from_wei(pol, 'ether'):.6f}")
    print(f"  USDC.e: ${ue / 1e6:.2f}")
    print(f"  USDC (native): ${un / 1e6:.2f}")

# Also check CLOB exchange balance for lagbot
import os, sys
sys.path.insert(0, "/opt/lagbot")
os.environ["PRIVATE_KEY"] = "a70c4b4c38ae9a25a92457b6feb36ffbd36efac484a156ca85ea310af592605b"
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

c = ClobClient("https://clob.polymarket.com", key=os.environ["PRIVATE_KEY"], chain_id=137, signature_type=0)
c.set_api_creds(c.create_or_derive_api_creds())
ba = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
balance = float(ba.get("balance", "0")) / 1e6
print(f"\nLagbot CLOB exchange balance: ${balance:.2f}")

# Check lagbot positions on Gamma API
import requests
resp = requests.get("https://data-api.polymarket.com/positions",
    params={"user": "0x1C0523D33b0D1c7Df8Ec450C5318cFcFc32Ce80A", "limit": 500}, timeout=30)
positions = resp.json()
winners = [p for p in positions if float(p.get("curPrice", 0) or 0) >= 0.95 and float(p.get("size", 0) or 0) > 0.1]
total = sum(float(p.get("size", 0)) for p in winners)
print(f"Lagbot positions: {len(positions)} total, {len(winners)} winners, ${total:.2f} redeemable")
