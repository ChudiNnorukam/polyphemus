#!/usr/bin/env python3
"""Check wallet type and balances."""
import os
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3

load_dotenv(Path("/opt/polyphemus/polyphemus/.env"))
pk = os.getenv("PRIVATE_KEY")
wallet = os.getenv("WALLET_ADDRESS")
sig_type = os.getenv("SIGNATURE_TYPE", "1")

w3 = Web3(Web3.HTTPProvider(os.getenv("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")))

# Derive EOA from private key
acct = w3.eth.account.from_key(pk)
eoa = acct.address

print(f"EOA from key: {eoa}")
print(f"WALLET_ADDRESS: {wallet}")
print(f"SIGNATURE_TYPE: {sig_type}")
print(f"Match: {eoa.lower() == wallet.lower()}")

# Check if wallet is a contract
wallet_cs = Web3.to_checksum_address(wallet)
code = w3.eth.get_code(wallet_cs)
print(f"Wallet is contract: {len(code) > 0}")
print(f"Wallet code length: {len(code)}")

# Check balances of both addresses
pol_wallet = w3.eth.get_balance(wallet_cs)
pol_eoa = w3.eth.get_balance(eoa)
print(f"Wallet POL: {w3.from_wei(pol_wallet, 'ether'):.6f}")
print(f"EOA POL: {w3.from_wei(pol_eoa, 'ether'):.6f}")

# Check if EOA is a contract too
eoa_code = w3.eth.get_code(eoa)
print(f"EOA is contract: {len(eoa_code) > 0}")

# Check CTF token balance for a winning position
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_ABI = [{"name":"balanceOf","type":"function","stateMutability":"view",
    "inputs":[{"name":"owner","type":"address"},{"name":"id","type":"uint256"}],
    "outputs":[{"name":"","type":"uint256"}]}]

ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=CTF_ABI)

# Check balance of first winning token on BOTH addresses
# Token from SOL Up/Down Feb 13 (from API: asset field)
test_token = 99143450796781733735  # SOL Up Feb 13 6:45PM
bal_wallet = ctf.functions.balanceOf(wallet_cs, test_token).call()
bal_eoa = ctf.functions.balanceOf(eoa, test_token).call()
print(f"\nCTF token {test_token}:")
print(f"  Wallet balance: {bal_wallet / 1e6:.2f} shares")
print(f"  EOA balance: {bal_eoa / 1e6:.2f} shares")
