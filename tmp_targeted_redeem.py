"""Targeted redemption of Lagbot accumulator settled positions."""

import os
import sys
import time
import requests
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

PRIVATE_KEY = os.environ["PRIVATE_KEY"]
WALLET = os.environ["WALLET_ADDRESS"]
RPC_URL = os.environ.get("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")

CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
GAMMA_API = "https://gamma-api.polymarket.com"

CTF_ABI = [
    {"name": "redeemPositions", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "collateralToken", "type": "address"},
         {"name": "parentCollectionId", "type": "bytes32"},
         {"name": "conditionId", "type": "bytes32"},
         {"name": "indexSets", "type": "uint256[]"},
     ], "outputs": []},
    {"name": "payoutDenominator", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "conditionId", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

# Exact slugs from Lagbot accumulator settlement logs (batch 2)
HEDGED_SLUGS = [
    "btc-updown-5m-1771042800",
    "btc-updown-5m-1771043100",
    "btc-updown-5m-1771043400",
    "btc-updown-5m-1771043700",
    "btc-updown-5m-1771047600",
    "btc-updown-5m-1771047900",
    "btc-updown-5m-1771048200",
    "btc-updown-5m-1771048500",
]

# Setup
w3 = Web3(Web3.HTTPProvider(RPC_URL))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E_ADDRESS), abi=ERC20_ABI)
wallet = Web3.to_checksum_address(WALLET)

usdc_before = usdc_e.functions.balanceOf(wallet).call() / 1e6
pol_before = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
print(f"Wallet: {wallet}")
print(f"USDC.e before: ${usdc_before:.2f}")
print(f"POL before: {pol_before:.4f}")
print(f"Markets to redeem: {len(HEDGED_SLUGS)}")
print()

# Step 1: Look up condition IDs from Gamma API
print("Looking up condition IDs from Gamma API...")
markets = []
for slug in HEDGED_SLUGS:
    try:
        resp = requests.get(f"{GAMMA_API}/markets", params={"slug": slug}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                m = data[0]
                cid = m.get("conditionId", "")
                if cid:
                    markets.append({"slug": slug, "condition_id": cid})
                    print(f"  {slug} -> {cid[:20]}...")
                else:
                    print(f"  {slug} -> NO conditionId!")
            else:
                print(f"  {slug} -> NOT FOUND in Gamma API")
        else:
            print(f"  {slug} -> HTTP {resp.status_code}")
    except Exception as e:
        print(f"  {slug} -> ERROR: {e}")

print(f"\nFound {len(markets)} condition IDs")
print()

if not markets:
    print("Nothing to redeem!")
    sys.exit(0)

# Step 2: Redeem each
print("=" * 60)
print(f"Redeeming {len(markets)} markets...")
print("=" * 60)

successes = 0
failures = 0

for i, m in enumerate(markets):
    cid = m["condition_id"]
    slug = m["slug"]
    cid_bytes = bytes.fromhex(cid[2:]) if cid.startswith("0x") else bytes.fromhex(cid)

    # Verify resolved
    pd = ctf.functions.payoutDenominator(cid_bytes).call()
    if pd == 0:
        print(f"[{i+1}] {slug} — NOT RESOLVED, skipping")
        continue

    print(f"[{i+1}/{len(markets)}] {slug}")

    try:
        gas_price = w3.eth.gas_price
        nonce = w3.eth.get_transaction_count(wallet)

        tx = ctf.functions.redeemPositions(
            Web3.to_checksum_address(USDC_E_ADDRESS),
            bytes(32),
            cid_bytes,
            [1, 2],
        ).build_transaction({
            "from": wallet,
            "nonce": nonce,
            "gas": 200000,
            "gasPrice": int(gas_price * 1.3),
            "chainId": 137,
        })

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
        status = receipt.get("status", 0)
        gas_used = receipt.get("gasUsed", 0)
        gas_cost = float(w3.from_wei(gas_used * int(gas_price * 1.3), "ether"))

        # Check USDC.e balance change
        usdc_now = usdc_e.functions.balanceOf(wallet).call() / 1e6

        if status == 1:
            successes += 1
            delta = usdc_now - usdc_before
            print(f"  SUCCESS | recovered=${delta:.2f} total | gas={gas_cost:.6f} POL")
        else:
            failures += 1
            print(f"  FAILED (reverted)")

        time.sleep(1)

    except Exception as e:
        err = str(e)
        if "revert" in err.lower():
            print(f"  SKIP (no tokens or already redeemed)")
        else:
            failures += 1
            print(f"  ERROR: {e}")
        time.sleep(0.5)

# Summary
usdc_after = usdc_e.functions.balanceOf(wallet).call() / 1e6
pol_after = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
recovered = usdc_after - usdc_before

print()
print("=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"Successful:      {successes}")
print(f"Failed:          {failures}")
print(f"USDC.e before:   ${usdc_before:.2f}")
print(f"USDC.e after:    ${usdc_after:.2f}")
print(f"RECOVERED:       ${recovered:.2f}")
print(f"POL spent:       {pol_before - pol_after:.4f}")
