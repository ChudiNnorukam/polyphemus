"""Bulk redeem all settled CTF positions for the Lagbot EOA wallet."""

import json
import os
import sys
import time
import requests
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

# ── Config ──
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
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

# ── Setup ──
w3 = Web3(Web3.HTTPProvider(RPC_URL))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E_ADDRESS), abi=ERC20_ABI)
wallet = Web3.to_checksum_address(WALLET)

print(f"Wallet: {wallet}")
print(f"RPC: {RPC_URL}")

# Check starting USDC.e balance
usdc_before = usdc_e.functions.balanceOf(wallet).call() / 1e6
pol_balance = w3.from_wei(w3.eth.get_balance(wallet), 'ether')
print(f"USDC.e balance before: ${usdc_before:.2f}")
print(f"POL balance: {pol_balance:.4f}")
print()

# ── Step 1: Discover all updown markets from Gamma API ──
print("=" * 60)
print("STEP 1: Discovering settled updown markets...")
print("=" * 60)

# Query for recently closed crypto updown markets
all_condition_ids = []
for asset in ["BTC", "ETH", "SOL", "XRP"]:
    for window in ["5m", "15m"]:
        slug_pattern = f"{asset.lower()}-updown-{window}"
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={"slug_contains": slug_pattern, "closed": "true", "limit": 50},
                timeout=10,
            )
            if resp.status_code == 200:
                markets = resp.json()
                for m in markets:
                    cid = m.get("conditionId", "")
                    slug = m.get("slug", "")
                    if cid:
                        all_condition_ids.append({
                            "condition_id": cid,
                            "slug": slug,
                            "question": m.get("question", ""),
                        })
        except Exception as e:
            print(f"  Warning: Failed to query {slug_pattern}: {e}")

print(f"Found {len(all_condition_ids)} settled markets to check")
print()

# ── Step 2: Check which ones have redeemable tokens ──
print("=" * 60)
print("STEP 2: Checking on-chain balances and resolution status...")
print("=" * 60)

redeemable = []
for market in all_condition_ids:
    cid = market["condition_id"]
    cid_bytes = bytes.fromhex(cid[2:]) if cid.startswith("0x") else bytes.fromhex(cid)

    # Check if resolved on-chain
    try:
        pd = ctf.functions.payoutDenominator(cid_bytes).call()
    except Exception:
        continue

    if pd == 0:
        continue  # Not resolved yet

    # Check token balances (index sets 1 and 2)
    # Token IDs are computed from conditionId + indexSet
    # But we can just try to redeem — if no balance, it's a no-op
    redeemable.append(market)

print(f"Found {len(redeemable)} resolved markets (may have tokens)")
print()

if not redeemable:
    print("Nothing to redeem!")
    sys.exit(0)

# ── Step 3: Redeem all ──
print("=" * 60)
print(f"STEP 3: Redeeming {len(redeemable)} markets...")
print("=" * 60)

total_gas = 0.0
successes = 0
failures = 0

for i, market in enumerate(redeemable):
    cid = market["condition_id"]
    slug = market["slug"]
    cid_bytes = bytes.fromhex(cid[2:]) if cid.startswith("0x") else bytes.fromhex(cid)

    print(f"\n[{i+1}/{len(redeemable)}] {slug}")
    print(f"  conditionId: {cid[:20]}...")

    try:
        gas_price = w3.eth.gas_price
        nonce = w3.eth.get_transaction_count(wallet)

        tx = ctf.functions.redeemPositions(
            Web3.to_checksum_address(USDC_E_ADDRESS),
            bytes(32),  # parentCollectionId = 0
            cid_bytes,
            [1, 2],     # both UP and DOWN index sets
        ).build_transaction({
            "from": wallet,
            "nonce": nonce,
            "gas": 200000,
            "gasPrice": int(gas_price * 1.3),
            "chainId": 137,
        })

        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"  TX sent: {tx_hash.hex()[:20]}...")

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
        status = receipt.get("status", 0)
        gas_used = receipt.get("gasUsed", 0)
        gas_cost = float(w3.from_wei(gas_used * int(gas_price * 1.3), "ether"))
        total_gas += gas_cost

        if status == 1:
            successes += 1
            print(f"  SUCCESS | gas={gas_cost:.6f} POL")
        else:
            failures += 1
            print(f"  FAILED (reverted) | gas={gas_cost:.6f} POL")

        time.sleep(1)  # Rate limit between txns

    except Exception as e:
        err_str = str(e)
        if "execution reverted" in err_str.lower() or "revert" in err_str.lower():
            print(f"  SKIP (no tokens or already redeemed)")
        else:
            failures += 1
            print(f"  ERROR: {e}")
        time.sleep(0.5)

# ── Summary ──
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

usdc_after = usdc_e.functions.balanceOf(wallet).call() / 1e6
recovered = usdc_after - usdc_before

print(f"Markets checked:  {len(redeemable)}")
print(f"Successful txns:  {successes}")
print(f"Failed txns:      {failures}")
print(f"Total gas:        {total_gas:.6f} POL")
print(f"USDC.e before:    ${usdc_before:.2f}")
print(f"USDC.e after:     ${usdc_after:.2f}")
print(f"RECOVERED:        ${recovered:.2f}")
