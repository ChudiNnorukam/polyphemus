#!/usr/bin/env python3
"""
Redeem winning CTF positions on Polymarket.
Calls ConditionalTokens.redeemPositions() for each resolved winning condition.
"""
import os, json, time
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3

load_dotenv(Path("/opt/polyphemus/polyphemus/.env"))

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET = os.getenv("WALLET_ADDRESS")
RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")

# Contract addresses (from py_clob_client)
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Minimal ABI for redeemPositions
CTF_ABI = [
    {
        "name": "redeemPositions",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "outputs": [],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "id", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "payoutDenominator",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "conditionId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

USDC_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

# Winning positions to redeem (from redeem_prep.py)
WINNERS = [
    {
        "title": "Solana Up or Down - Feb 13 6:45PM-7:00PM",
        "condition_id": "0xc9a461f529d20bd083fd6b2a7c9a9a01932fce3d22002f7f74318ccba667c5f0",
        "size": 48.6,
    },
    {
        "title": "Bitcoin Up or Down - Feb 13 6:40PM-6:45PM",
        "condition_id": "0x966ba1e548f2f52501132f01e38b3ce88f4078c8c203acd6fc0bb26f2cefcca4",
        "size": 40.0,
    },
    {
        "title": "Bitcoin Up or Down - Feb 13 8:55PM-9:00PM",
        "condition_id": "0xa1ad6c15df03df5fa90002fd5c99f92ed9eace027c960b4c38aeb3ec68609688",
        "size": 26.9,
    },
    {
        "title": "Bitcoin Up or Down - Feb 13 9:00PM-9:15PM",
        "condition_id": "0xb25fd7fae48f2d0083f4c40e59499d8ef993955078cd4b1036d73210743c92bf",
        "size": 12.2,
    },
]

def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print(f"ERROR: Cannot connect to {RPC_URL}")
        return

    chain_id = w3.eth.chain_id
    print(f"Connected to chain {chain_id} via {RPC_URL}")

    wallet = Web3.to_checksum_address(WALLET)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_ADDRESS), abi=CTF_ABI)
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=USDC_ABI)

    # Check USDC.e balance before
    usdc_before = usdc.functions.balanceOf(wallet).call()
    print(f"\nUSDC.e balance before: ${usdc_before / 1e6:.2f}")

    # Check POL balance for gas
    pol_balance = w3.eth.get_balance(wallet)
    print(f"POL balance (for gas): {w3.from_wei(pol_balance, 'ether'):.4f} POL")

    if pol_balance < w3.to_wei(0.01, "ether"):
        print("WARNING: Very low POL for gas. Transactions may fail.")

    parent_collection_id = bytes(32)  # bytes32(0) for root

    total_redeemed = 0
    nonce = w3.eth.get_transaction_count(wallet)

    for i, winner in enumerate(WINNERS):
        cid = winner["condition_id"]
        title = winner["title"]
        expected = winner["size"]

        print(f"\n--- Redeeming #{i+1}: {title} ---")
        print(f"  Condition: {cid}")
        print(f"  Expected: ~{expected:.1f} shares @ $1.00 = ~${expected:.2f}")

        # Check if condition is resolved (payoutDenominator > 0)
        try:
            payout_denom = ctf.functions.payoutDenominator(cid).call()
            print(f"  Payout denominator: {payout_denom} ({'RESOLVED' if payout_denom > 0 else 'NOT RESOLVED'})")
            if payout_denom == 0:
                print(f"  SKIP: Market not resolved on-chain yet")
                continue
        except Exception as e:
            print(f"  WARNING: Could not check payout: {e}")

        # Build transaction: redeemPositions(USDC.e, 0x0, conditionId, [1, 2])
        try:
            gas_price = w3.eth.gas_price
            tx = ctf.functions.redeemPositions(
                Web3.to_checksum_address(USDC_E),
                parent_collection_id,
                bytes.fromhex(cid[2:]),  # Remove 0x prefix
                [1, 2],  # Both outcome index sets
            ).build_transaction({
                "from": wallet,
                "nonce": nonce,
                "gas": 200000,
                "gasPrice": int(gas_price * 1.2),
                "chainId": chain_id,
            })

            # Sign and send
            signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"  TX sent: {tx_hash.hex()}")

            # Wait for receipt
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            status = receipt.get("status", 0)
            gas_used = receipt.get("gasUsed", 0)
            print(f"  Status: {'SUCCESS' if status == 1 else 'FAILED'} | Gas used: {gas_used}")

            if status == 1:
                total_redeemed += expected
                nonce += 1
            else:
                print(f"  FAILED — transaction reverted")
                nonce += 1  # Still increment nonce

            time.sleep(2)  # Brief pause between txns

        except Exception as e:
            print(f"  ERROR: {e}")
            # Try incrementing nonce anyway in case tx was sent
            nonce = w3.eth.get_transaction_count(wallet)

    # Check USDC.e balance after
    time.sleep(5)
    usdc_after = usdc.functions.balanceOf(wallet).call()
    gained = (usdc_after - usdc_before) / 1e6
    print(f"\n=== RESULTS ===")
    print(f"USDC.e before: ${usdc_before / 1e6:.2f}")
    print(f"USDC.e after:  ${usdc_after / 1e6:.2f}")
    print(f"Gained:        ${gained:.2f}")
    print(f"Expected:      ~${total_redeemed:.2f}")


if __name__ == "__main__":
    main()
