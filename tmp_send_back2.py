#!/usr/bin/env python3
"""Transfer USDC.e from kingsleahh Safe back to Lagbot EOA."""
import os, time
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

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

SAFE_ABI = [
    {"name": "execTransaction", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "to", "type": "address"},
         {"name": "value", "type": "uint256"},
         {"name": "data", "type": "bytes"},
         {"name": "operation", "type": "uint8"},
         {"name": "safeTxGas", "type": "uint256"},
         {"name": "baseGas", "type": "uint256"},
         {"name": "gasPrice", "type": "uint256"},
         {"name": "gasToken", "type": "address"},
         {"name": "refundReceiver", "type": "address"},
         {"name": "signatures", "type": "bytes"},
     ],
     "outputs": [{"name": "success", "type": "bool"}]},
    {"name": "nonce", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "getOwners", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "address[]"}]},
    {"name": "getThreshold", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "domainSeparator", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "bytes32"}]},
]

ZERO = "0x0000000000000000000000000000000000000000"

def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    eoa = Web3.to_checksum_address(LAGBOT_EOA)
    safe_addr = Web3.to_checksum_address(KINGSLEAHH)
    usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    safe = w3.eth.contract(address=safe_addr, abi=SAFE_ABI)

    safe_bal = usdc_e.functions.balanceOf(safe_addr).call()
    eoa_bal = usdc_e.functions.balanceOf(eoa).call()
    print(f"Kingsleahh Safe: ${safe_bal/1e6:.2f} USDC.e")
    print(f"Lagbot EOA: ${eoa_bal/1e6:.2f} USDC.e")

    if safe_bal == 0:
        print("Nothing to transfer!")
        return

    owners = safe.functions.getOwners().call()
    print(f"Safe owners: {owners}")
    assert eoa.lower() in [o.lower() for o in owners], "EOA is not Safe owner!"

    # Encode ERC20 transfer(eoa, amount) calldata
    transfer_data = usdc_e.encodeABI(fn_name="transfer", args=[eoa, safe_bal])

    # Get Safe nonce and domain separator
    safe_nonce = safe.functions.nonce().call()
    domain_separator = safe.functions.domainSeparator().call()
    print(f"Safe nonce: {safe_nonce}")

    # EIP-712 Safe tx hash
    SAFE_TX_TYPEHASH = bytes.fromhex("bb8310d486368db6bd6f849402fdd73ad53d316b5a4b2644ad6efe0f941286d8")

    encoded = (
        SAFE_TX_TYPEHASH +
        bytes.fromhex(USDC_E[2:].lower()).rjust(32, b'\x00') +
        (0).to_bytes(32, 'big') +  # value (0 ETH)
        w3.keccak(bytes.fromhex(transfer_data[2:])) +  # keccak(data)
        (0).to_bytes(32, 'big') +  # operation CALL
        (0).to_bytes(32, 'big') +  # safeTxGas
        (0).to_bytes(32, 'big') +  # baseGas
        (0).to_bytes(32, 'big') +  # gasPrice
        b'\x00' * 32 +  # gasToken
        b'\x00' * 32 +  # refundReceiver
        safe_nonce.to_bytes(32, 'big')
    )
    safe_tx_hash = w3.keccak(encoded)
    final_hash = w3.keccak(b'\x19\x01' + domain_separator + safe_tx_hash)

    # Sign
    signed_msg = w3.eth.account.signHash(final_hash, PRIVATE_KEY)
    signature = (
        signed_msg.r.to_bytes(32, 'big') +
        signed_msg.s.to_bytes(32, 'big') +
        bytes([signed_msg.v])
    )

    print(f"\nTransferring ${safe_bal/1e6:.2f} USDC.e: Safe → Lagbot EOA")

    gas_price = w3.eth.gas_price
    nonce = w3.eth.get_transaction_count(eoa)

    tx = safe.functions.execTransaction(
        Web3.to_checksum_address(USDC_E),
        0,
        bytes.fromhex(transfer_data[2:]),
        0, 0, 0, 0,
        Web3.to_checksum_address(ZERO),
        Web3.to_checksum_address(ZERO),
        signature,
    ).build_transaction({
        "from": eoa,
        "nonce": nonce,
        "gas": 200000,
        "gasPrice": int(gas_price * 1.3),
        "chainId": 137,
    })

    signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    print(f"TX: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
    status = receipt.get("status", 0)
    gas_used = receipt.get("gasUsed", 0)
    gas_cost = w3.from_wei(gas_used * int(gas_price * 1.3), "ether")
    print(f"Status: {'SUCCESS' if status == 1 else 'FAILED'}")
    print(f"Gas: {gas_used} ({gas_cost:.6f} POL)")

    time.sleep(3)
    safe_after = usdc_e.functions.balanceOf(safe_addr).call()
    eoa_after = usdc_e.functions.balanceOf(eoa).call()
    print(f"\n=== TRANSFER COMPLETE ===")
    print(f"Kingsleahh Safe: ${safe_after/1e6:.2f}")
    print(f"Lagbot EOA: ${eoa_after/1e6:.2f}")

if __name__ == "__main__":
    main()
