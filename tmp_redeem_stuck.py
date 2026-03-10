"""Redeem 3 stuck settlements manually."""
import os, sys, json
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from dotenv import load_dotenv

load_dotenv("/opt/lagbot/lagbot/.env")

RPC = os.environ.get("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")
PK = os.environ["PRIVATE_KEY"]
WALLET = os.environ["WALLET_ADDRESS"]

w3 = Web3(Web3.HTTPProvider(RPC))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

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

USDC_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=CTF_ABI)
usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=USDC_ABI)
wallet = Web3.to_checksum_address(WALLET)

slugs = [
    ("btc-updown-5m-1771049400", "0x4426590c217bfd1e83f4dcbb1f461ef3f296f454821316ee3ab5a23f5ccf96c0", 79),
    ("btc-updown-5m-1771049700", "0xba731bd11b5e8055deae35b0aa7bc3dc0482bc010d4a9a1f156d3f2d911802c6", 48),
    ("btc-updown-5m-1771050000", "0x18973f7a5ae60553f1500ff8b2dbe75d21f8681052c91b6f9dc4b8d1a4cf4866", 28),
]

bal_before = usdc.functions.balanceOf(wallet).call() / 1e6
print(f"USDC.e before: ${bal_before:.2f}")

for slug, cid_hex, shares in slugs:
    cid = bytes.fromhex(cid_hex[2:])
    pd = ctf.functions.payoutDenominator(cid).call()
    if pd == 0:
        print(f"  SKIP {slug}: not resolved on-chain yet (pd=0)")
        continue

    print(f"  Redeeming {slug} ({shares} shares, pd={pd})...")
    gas_price = w3.eth.gas_price
    nonce = w3.eth.get_transaction_count(wallet)
    tx = ctf.functions.redeemPositions(
        Web3.to_checksum_address(USDC_E),
        bytes(32),
        cid,
        [1, 2],
    ).build_transaction({
        "from": wallet,
        "nonce": nonce,
        "gas": 200000,
        "gasPrice": int(gas_price * 1.3),
        "chainId": 137,
    })
    signed = w3.eth.account.sign_transaction(tx, PK)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=90)
    status = "OK" if receipt.get("status") == 1 else "FAILED"
    gas_used = receipt.get("gasUsed", 0)
    print(f"    {status} | tx={tx_hash.hex()[:16]}... | gas={gas_used}")

bal_after = usdc.functions.balanceOf(wallet).call() / 1e6
print(f"\nUSDC.e after: ${bal_after:.2f} | recovered: ${bal_after - bal_before:.2f}")
