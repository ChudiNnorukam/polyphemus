#!/usr/bin/env python3
"""Transfer USDC.e from Lagbot EOA to kingsleahh Safe wallet."""
import os, time
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3

load_dotenv(Path("/opt/lagbot/lagbot/.env"))

PRIVATE_KEY = os.getenv("PRIVATE_KEY")
LAGBOT_EOA = "0x1C0523D33b0D1c7Df8Ec450C5318cFcFc32Ce80A"
KINGSLEAHH = "0x7E69be59E92a396EcCBba344CAe383927fcAD9Ad"
RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]

def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print(f"ERROR: Cannot connect to {RPC_URL}")
        return

    lagbot = Web3.to_checksum_address(LAGBOT_EOA)
    dest = Web3.to_checksum_address(KINGSLEAHH)
    usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)

    # Check balance
    balance = usdc_e.functions.balanceOf(lagbot).call()
    pol = w3.eth.get_balance(lagbot)
    print(f"Lagbot EOA: {lagbot}")
    print(f"Destination (kingsleahh): {dest}")
    print(f"USDC.e balance: ${balance / 1e6:.2f}")
    print(f"POL: {w3.from_wei(pol, 'ether'):.4f}")

    if balance == 0:
        print("Nothing to transfer!")
        return

    # Transfer full USDC.e balance
    print(f"\nTransferring ${balance / 1e6:.2f} USDC.e → {dest}")

    gas_price = w3.eth.gas_price
    nonce = w3.eth.get_transaction_count(lagbot)

    tx = usdc_e.functions.transfer(dest, balance).build_transaction({
        "from": lagbot,
        "nonce": nonce,
        "gas": 80000,
        "gasPrice": int(gas_price * 1.3),
        "chainId": 137,
    })

    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"TX: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
    status = receipt.get("status", 0)
    gas_used = receipt.get("gasUsed", 0)
    gas_cost = w3.from_wei(gas_used * int(gas_price * 1.3), "ether")

    print(f"Status: {'SUCCESS' if status == 1 else 'FAILED'}")
    print(f"Gas: {gas_used} ({gas_cost:.6f} POL)")

    # Verify destination balance
    time.sleep(3)
    dest_bal = usdc_e.functions.balanceOf(dest).call()
    lagbot_bal = usdc_e.functions.balanceOf(lagbot).call()
    print(f"\n=== TRANSFER COMPLETE ===")
    print(f"Lagbot USDC.e: ${lagbot_bal / 1e6:.2f}")
    print(f"Kingsleahh USDC.e: ${dest_bal / 1e6:.2f}")

if __name__ == "__main__":
    main()
