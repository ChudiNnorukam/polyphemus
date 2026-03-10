#!/usr/bin/env python3
"""
Redeem Lagbot's winning CTF positions.
Lagbot uses sig_type=0 (EOA) so we can sign directly.
"""
import os, json, time, requests
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3

load_dotenv(Path("/opt/lagbot/lagbot/.env"))

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET = os.getenv("WALLET_ADDRESS")
RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")

CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

CTF_ABI = [
    {"name": "redeemPositions", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "collateralToken", "type": "address"},
         {"name": "parentCollectionId", "type": "bytes32"},
         {"name": "conditionId", "type": "bytes32"},
         {"name": "indexSets", "type": "uint256[]"},
     ], "outputs": []},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "id", "type": "uint256"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "payoutDenominator", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "conditionId", "type": "bytes32"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

USDC_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print(f"ERROR: Cannot connect to {RPC_URL}")
        return

    wallet = Web3.to_checksum_address(WALLET)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=USDC_ABI)

    print(f"Wallet: {wallet}")
    print(f"Chain: {w3.eth.chain_id}")

    # Check balances before
    usdc_before = usdc.functions.balanceOf(wallet).call()
    pol_before = w3.eth.get_balance(wallet)
    print(f"USDC.e before: ${usdc_before / 1e6:.2f}")
    print(f"POL: {w3.from_wei(pol_before, 'ether'):.4f}")

    # Get winning positions from Gamma API
    print("\nFetching positions from Gamma API...")
    resp = requests.get(
        "https://data-api.polymarket.com/positions",
        params={"user": wallet, "limit": 500},
        timeout=30,
    )
    positions = resp.json()

    winners = []
    for p in positions:
        size = float(p.get("size", 0) or 0)
        cur_price = float(p.get("curPrice", 0) or 0)
        redeemable = p.get("redeemable", False)
        condition_id = p.get("conditionId", "")

        if size > 0.1 and cur_price >= 0.95 and redeemable and condition_id:
            winners.append({
                "condition_id": condition_id,
                "size": size,
                "title": p.get("title", "")[:60],
                "token_id": p.get("asset", ""),
            })

    print(f"Found {len(winners)} winning positions to redeem:")
    for w in winners:
        print(f"  {w['size']:8.1f} shares | {w['title']}")

    if not winners:
        print("Nothing to redeem!")
        return

    parent_collection_id = bytes(32)
    nonce = w3.eth.get_transaction_count(wallet)
    total_redeemed = 0

    for i, winner in enumerate(winners):
        cid = winner["condition_id"]
        title = winner["title"]
        expected = winner["size"]

        print(f"\n--- Redeeming #{i+1}/{len(winners)}: {title} ---")
        print(f"  Condition: {cid}")
        print(f"  Expected: ~{expected:.1f} shares = ~${expected:.2f}")

        # Verify resolved on-chain
        try:
            pd = ctf.functions.payoutDenominator(bytes.fromhex(cid[2:])).call()
            if pd == 0:
                print(f"  SKIP: Not resolved on-chain (payoutDenominator=0)")
                continue
            print(f"  Payout denominator: {pd} (RESOLVED)")
        except Exception as e:
            print(f"  WARNING: Could not check payout: {e}")

        # Verify we have token balance
        token_id = int(winner["token_id"])
        try:
            bal = ctf.functions.balanceOf(wallet, token_id).call()
            print(f"  On-chain balance: {bal / 1e6:.2f} shares")
            if bal == 0:
                print(f"  SKIP: No on-chain balance for this token")
                continue
        except Exception as e:
            print(f"  WARNING: Could not check balance: {e}")

        # Build and send redemption tx
        try:
            gas_price = w3.eth.gas_price
            tx = ctf.functions.redeemPositions(
                Web3.to_checksum_address(USDC_E),
                parent_collection_id,
                bytes.fromhex(cid[2:]),
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
            print(f"  TX: {tx_hash.hex()}")

            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
            status = receipt.get("status", 0)
            gas_used = receipt.get("gasUsed", 0)
            gas_cost_pol = w3.from_wei(gas_used * int(gas_price * 1.3), "ether")
            print(f"  Status: {'SUCCESS' if status == 1 else 'FAILED'} | Gas: {gas_used} ({gas_cost_pol:.6f} POL)")

            if status == 1:
                total_redeemed += expected
                nonce += 1
            else:
                print(f"  FAILED — tx reverted!")
                nonce += 1

            time.sleep(2)

        except Exception as e:
            print(f"  ERROR: {e}")
            nonce = w3.eth.get_transaction_count(wallet)

    # Final balance check
    time.sleep(5)
    usdc_after = usdc.functions.balanceOf(wallet).call()
    pol_after = w3.eth.get_balance(wallet)
    gained = (usdc_after - usdc_before) / 1e6
    gas_spent = w3.from_wei(pol_before - pol_after, "ether")

    print(f"\n{'='*50}")
    print(f"=== REDEMPTION COMPLETE ===")
    print(f"USDC.e before: ${usdc_before / 1e6:.2f}")
    print(f"USDC.e after:  ${usdc_after / 1e6:.2f}")
    print(f"GAINED:        ${gained:.2f}")
    print(f"Gas spent:     {gas_spent:.6f} POL")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
