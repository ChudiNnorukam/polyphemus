"""Redeem stuck settlements — batch 3."""
import os
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
    ("btc-updown-5m-1771047600", "0x9f4bf8c2db9e2cd3b8ee76a947d2f9ddb6e9d827cd43f7394d1a3328664ec328"),
    ("btc-updown-5m-1771047900", "0x0ca8fa1c34cf4984bd77b06c3c573b183a8bb4aaca12cf3b89586a6f02b8c1e1"),
    ("btc-updown-5m-1771048200", "0x5fbf6155427a1d7d316221f6632382de1cc880c6f84e8592b59c736fadc0a19d"),
    ("btc-updown-5m-1771048500", "0x62dfb40021f3d4be5577fee5a138ea654cb57004d6b9fe93f37fb67001022f32"),
    ("btc-updown-5m-1771050600", "0xaf0098300eee6d985be5c49592e6e3cabe7ef155fbc32d4e0773bd9ae60f1471"),
    ("btc-updown-5m-1771051200", "0xc186df97e32626bef9598aebc44c88f4f8ccefecaf5376fd7e014f5a09bb865a"),
]

bal_before = usdc.functions.balanceOf(wallet).call() / 1e6
print(f"USDC.e before: ${bal_before:.2f}")

for slug, cid_hex in slugs:
    cid = bytes.fromhex(cid_hex[2:])
    pd = ctf.functions.payoutDenominator(cid).call()
    if pd == 0:
        print(f"  SKIP {slug}: not resolved on-chain yet (pd=0)")
        continue

    print(f"  Redeeming {slug} (pd={pd})...")
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
