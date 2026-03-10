#!/usr/bin/env python3
"""Set USDC.e and CTF allowances for Polymarket CLOB exchange contracts.

Must be run ONCE per EOA wallet before live trading.
Requires POL/MATIC for gas (~0.01 POL total for 6 txns).

Steps:
1. Unstick any pending transactions (replacement with higher gas)
2. Check which allowances are already set
3. Only approve what's missing
"""
import os
import sys
import time
from dotenv import load_dotenv
from web3 import Web3
from web3.constants import MAX_INT

# Auto-detect .env path
for env_path in ["polyphemus/.env", "/opt/lagbot/polyphemus/.env"]:
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"Loaded env from: {env_path}")
        break

priv_key = os.getenv("PRIVATE_KEY")
pub_key = os.getenv("WALLET_ADDRESS")

if not priv_key or not pub_key:
    print("ERROR: PRIVATE_KEY or WALLET_ADDRESS not found in .env")
    sys.exit(1)

pub_key = Web3.to_checksum_address(pub_key)

# Polygon RPC
rpc_url = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
chain_id = 137

# Contract ABIs (minimal — approve + allowance/isApprovedForAll for checking)
erc20_abi = [
    {"constant": False, "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "payable": False, "stateMutability": "nonpayable", "type": "function"},
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "payable": False, "stateMutability": "view", "type": "function"},
]
erc1155_abi = [
    {"inputs": [{"internalType": "address", "name": "operator", "type": "address"}, {"internalType": "bool", "name": "approved", "type": "bool"}], "name": "setApprovalForAll", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"internalType": "address", "name": "account", "type": "address"}, {"internalType": "address", "name": "operator", "type": "address"}], "name": "isApprovedForAll", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
]

# Polymarket contract addresses (Polygon)
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# Exchange contracts requiring approval
TARGETS = {
    "CTF Exchange": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    "Neg Risk CTF Exchange": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
    "Neg Risk Adapter": "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
}

print(f"Wallet: {pub_key}")
print(f"RPC: {rpc_url}")

web3 = Web3(Web3.HTTPProvider(rpc_url))

if not web3.is_connected():
    print("ERROR: Cannot connect to Polygon RPC")
    sys.exit(1)

# Check POL balance
pol_balance = web3.eth.get_balance(pub_key)
pol_eth = web3.from_wei(pol_balance, 'ether')
print(f"POL balance: {pol_eth:.6f} POL")

if pol_eth < 0.005:
    print(f"WARNING: Low POL balance ({pol_eth:.6f}). Need ~0.05 POL for transactions.")
    sys.exit(1)

usdc = web3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=erc20_abi)
ctf = web3.eth.contract(address=Web3.to_checksum_address(CTF), abi=erc1155_abi)


# --- Step 1: Unstick pending transactions ---
def unstick_pending():
    """Send a 0-value self-transfer to replace any stuck pending txns."""
    confirmed = web3.eth.get_transaction_count(pub_key, 'latest')
    pending = web3.eth.get_transaction_count(pub_key, 'pending')
    stuck = pending - confirmed

    if stuck == 0:
        print("\n[OK] No stuck transactions")
        return

    print(f"\n[!] {stuck} stuck transaction(s) detected (confirmed={confirmed}, pending={pending})")
    print(f"    Sending replacement tx at nonce {confirmed} with high gas...")

    for nonce in range(confirmed, pending):
        tx = {
            "chainId": chain_id,
            "from": pub_key,
            "to": pub_key,
            "value": 0,
            "nonce": nonce,
            "gas": 21000,
            "maxFeePerGas": web3.to_wei(150, "gwei"),
            "maxPriorityFeePerGas": web3.to_wei(100, "gwei"),
        }
        signed = web3.eth.account.sign_transaction(tx, private_key=priv_key)
        raw_tx = signed.rawTransaction if hasattr(signed, 'rawTransaction') else signed.raw_transaction
        try:
            tx_hash = web3.eth.send_raw_transaction(raw_tx)
            print(f"    Replacement sent: {tx_hash.hex()[:16]}... (nonce={nonce})")
            receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            print(f"    [OK] Confirmed in block {receipt.blockNumber}")
        except Exception as e:
            err = str(e)
            if "already known" in err.lower() or "nonce too low" in err.lower():
                print(f"    [OK] Nonce {nonce} already cleared")
            else:
                print(f"    [ERROR] {e}")
                return
        time.sleep(3)

    # Verify
    new_confirmed = web3.eth.get_transaction_count(pub_key, 'latest')
    new_pending = web3.eth.get_transaction_count(pub_key, 'pending')
    print(f"    After unstick: confirmed={new_confirmed}, pending={new_pending}")


# --- Step 2: Check existing allowances ---
def check_allowances():
    """Check which allowances are already set. Returns list of (name, type) that need setting."""
    print("\n--- Checking existing allowances ---")
    needed = []

    for name, target in TARGETS.items():
        target = Web3.to_checksum_address(target)

        # Check USDC.e allowance
        try:
            allowance = usdc.functions.allowance(pub_key, target).call()
            if allowance > 10**18:  # More than 1 trillion USDC (effectively unlimited)
                print(f"  [SKIP] USDC.e → {name}: already approved ({allowance})")
            else:
                print(f"  [NEED] USDC.e → {name}: allowance={allowance}")
                needed.append((name, target, "usdc"))
        except Exception as e:
            print(f"  [ERROR] USDC.e check for {name}: {e}")
            needed.append((name, target, "usdc"))

        time.sleep(1)

        # Check CTF approval
        try:
            approved = ctf.functions.isApprovedForAll(pub_key, target).call()
            if approved:
                print(f"  [SKIP] CTF → {name}: already approved")
            else:
                print(f"  [NEED] CTF → {name}: not approved")
                needed.append((name, target, "ctf"))
        except Exception as e:
            print(f"  [ERROR] CTF check for {name}: {e}")
            needed.append((name, target, "ctf"))

        time.sleep(1)

    return needed


# --- Step 3: Set missing allowances ---
def set_allowances(needed):
    """Set only the missing allowances."""
    if not needed:
        print("\n[OK] All allowances already set! Nothing to do.")
        return 0, 0

    print(f"\n--- Setting {len(needed)} missing allowance(s) ---")
    success = 0
    failed = 0

    for name, target, token_type in needed:
        for attempt in range(3):
            try:
                # Use 'pending' nonce to chain transactions correctly
                nonce = web3.eth.get_transaction_count(pub_key, 'pending')

                if token_type == "usdc":
                    contract_fn = usdc.functions.approve(target, int(MAX_INT, 0))
                    label = f"USDC.e approve → {name}"
                else:
                    contract_fn = ctf.functions.setApprovalForAll(target, True)
                    label = f"CTF approve → {name}"

                tx = contract_fn.build_transaction({
                    "chainId": chain_id,
                    "from": pub_key,
                    "nonce": nonce,
                    "gas": 65000,
                    "maxFeePerGas": web3.to_wei(80, "gwei"),
                    "maxPriorityFeePerGas": web3.to_wei(50, "gwei"),
                })

                signed = web3.eth.account.sign_transaction(tx, private_key=priv_key)
                raw_tx = signed.rawTransaction if hasattr(signed, 'rawTransaction') else signed.raw_transaction
                tx_hash = web3.eth.send_raw_transaction(raw_tx)
                print(f"  [SENT] {label} | nonce={nonce} | tx: {tx_hash.hex()[:16]}...")

                receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                status = "OK" if receipt.status == 1 else "FAILED"
                print(f"  [{status}] {label} | block: {receipt.blockNumber}")

                if receipt.status == 1:
                    success += 1
                else:
                    failed += 1
                break  # Success or confirmed failure, move on

            except Exception as e:
                err_str = str(e)
                if "rate limit" in err_str.lower() or "too many" in err_str.lower():
                    wait = 20 * (attempt + 1)
                    print(f"  [RATE LIMITED] {label} — waiting {wait}s (attempt {attempt+1}/3)")
                    time.sleep(wait)
                elif "already known" in err_str.lower():
                    print(f"  [OK] {label}: tx already in mempool, waiting for confirmation...")
                    time.sleep(15)
                    break
                else:
                    print(f"  [ERROR] {label}: {e}")
                    failed += 1
                    break

        # Wait between transactions to avoid rate limiting
        time.sleep(15)

    return success, failed


# --- Main ---
if __name__ == "__main__":
    unstick_pending()
    time.sleep(5)  # Let RPC catch up

    needed = check_allowances()
    time.sleep(3)

    success, failed = set_allowances(needed)

    total_needed = len(needed)
    print(f"\n{'='*50}")
    print(f"Results: {success}/{total_needed} approvals succeeded, {failed} failed")
    if total_needed == 0:
        print("All 6 allowances were already set!")
    elif failed == 0 and success == total_needed:
        print("All allowances set! Ready for live trading.")
    else:
        print("WARNING: Some approvals failed. Re-run script to retry.")
