#!/usr/bin/env python3
"""Map all known wallet addresses and their balances."""
import os
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3

load_dotenv(Path("/opt/polyphemus/polyphemus/.env"))

rpc = os.getenv("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")
w3 = Web3(Web3.HTTPProvider(rpc))

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

ERC20_ABI = [{"name":"balanceOf","type":"function","stateMutability":"view",
    "inputs":[{"name":"account","type":"address"}],
    "outputs":[{"name":"","type":"uint256"}]}]

usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_NATIVE), abi=ERC20_ABI)

# All known addresses
addresses = {
    "Bot Proxy (Polyphemus)": os.getenv("WALLET_ADDRESS"),
    "Bot EOA (from private key)": w3.eth.account.from_key(os.getenv("PRIVATE_KEY")).address,
    "User Safe (Lagbot/UI)": "0x7E69be59E92a396EcCBba344CAe383927fcAD9Ad",
}

# Also try to load lagbot .env if it exists
lagbot_env = Path("/opt/lagbot/lagbot/.env")
if lagbot_env.exists():
    from dotenv import dotenv_values
    lagbot = dotenv_values(lagbot_env)
    if lagbot.get("WALLET_ADDRESS"):
        addresses["Lagbot Wallet"] = lagbot["WALLET_ADDRESS"]
    if lagbot.get("PRIVATE_KEY"):
        lagbot_eoa = w3.eth.account.from_key(lagbot["PRIVATE_KEY"]).address
        addresses["Lagbot EOA"] = lagbot_eoa

print("=== WALLET MAP ===\n")
for label, addr in addresses.items():
    addr_cs = Web3.to_checksum_address(addr)
    pol = w3.eth.get_balance(addr_cs)
    usdc_e_bal = usdc_e.functions.balanceOf(addr_cs).call()
    usdc_bal = usdc.functions.balanceOf(addr_cs).call()
    is_contract = len(w3.eth.get_code(addr_cs)) > 0

    print(f"{label}:")
    print(f"  Address: {addr_cs}")
    print(f"  Type: {'CONTRACT' if is_contract else 'EOA'}")
    print(f"  POL: {w3.from_wei(pol, 'ether'):.6f}")
    print(f"  USDC.e: ${usdc_e_bal / 1e6:.2f}")
    print(f"  USDC (native): ${usdc_bal / 1e6:.2f}")
    print()
