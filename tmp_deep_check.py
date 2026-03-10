#!/usr/bin/env python3
"""Deep check: where are the CTF tokens actually held?"""
import os, requests, json
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3

load_dotenv(Path("/opt/polyphemus/polyphemus/.env"))
wallet = os.getenv("WALLET_ADDRESS")
rpc = os.getenv("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")

w3 = Web3(Web3.HTTPProvider(rpc))
wallet_cs = Web3.to_checksum_address(wallet)

CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

CTF_ABI = [
    {"name":"balanceOf","type":"function","stateMutability":"view",
     "inputs":[{"name":"owner","type":"address"},{"name":"id","type":"uint256"}],
     "outputs":[{"name":"","type":"uint256"}]},
    {"name":"payoutDenominator","type":"function","stateMutability":"view",
     "inputs":[{"name":"conditionId","type":"bytes32"}],
     "outputs":[{"name":"","type":"uint256"}]},
]
ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=CTF_ABI)

# Get FULL token IDs from Gamma API
resp = requests.get(
    "https://data-api.polymarket.com/positions",
    params={"user": wallet},
    timeout=30,
)
positions = resp.json()

# Check the 4 winning positions (curPrice >= 0.95)
winners = [p for p in positions if float(p.get("curPrice", 0) or 0) >= 0.95]

print(f"=== CHECKING {len(winners)} WINNING POSITIONS ===\n")

for p in winners:
    token_id_str = p["asset"]
    token_id = int(token_id_str)
    size = float(p.get("size", 0))
    title = p.get("title", "")[:60]
    condition_id = p.get("conditionId", "")
    outcome = p.get("outcome", "")

    print(f"Market: {title}")
    print(f"  Outcome: {outcome}")
    print(f"  Full token_id: {token_id_str[:40]}...")
    print(f"  API size: {size:.1f} shares")
    print(f"  Condition: {condition_id}")

    # Check balance on: wallet, exchange, neg_risk_exchange
    for label, addr in [("Wallet", wallet_cs),
                        ("Exchange", Web3.to_checksum_address(EXCHANGE)),
                        ("NegRisk", Web3.to_checksum_address(NEG_RISK_EXCHANGE))]:
        try:
            bal = ctf.functions.balanceOf(addr, token_id).call()
            if bal > 0:
                print(f"  {label:12s} balance: {bal / 1e6:.2f} shares  *** FOUND ***")
            else:
                print(f"  {label:12s} balance: 0")
        except Exception as e:
            print(f"  {label:12s} error: {e}")

    # Check payout denominator (is condition resolved?)
    if condition_id:
        try:
            pd = ctf.functions.payoutDenominator(bytes.fromhex(condition_id[2:])).call()
            print(f"  Payout denom: {pd} ({'RESOLVED' if pd > 0 else 'NOT resolved'})")
        except Exception as e:
            print(f"  Payout denom error: {e}")

    print()

# Also check a few LOSING positions to see if they have on-chain balance
print("=== CHECKING 3 LOSING POSITIONS (for comparison) ===\n")
losers = [p for p in positions if float(p.get("curPrice", 0) or 0) < 0.05 and float(p.get("size", 0) or 0) > 50][:3]
for p in losers:
    token_id = int(p["asset"])
    size = float(p.get("size", 0))
    title = p.get("title", "")[:60]

    print(f"Market: {title}")
    print(f"  API size: {size:.1f} shares")

    for label, addr in [("Wallet", wallet_cs),
                        ("Exchange", Web3.to_checksum_address(EXCHANGE)),
                        ("NegRisk", Web3.to_checksum_address(NEG_RISK_EXCHANGE))]:
        try:
            bal = ctf.functions.balanceOf(addr, token_id).call()
            if bal > 0:
                print(f"  {label:12s} balance: {bal / 1e6:.2f} shares  *** FOUND ***")
            else:
                print(f"  {label:12s} balance: 0")
        except Exception as e:
            print(f"  {label:12s} error: {e}")
    print()
