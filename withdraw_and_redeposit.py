"""Withdraw USDC from Polymarket exchange and check deposit options."""
import os, json
from dotenv import load_dotenv
from pathlib import Path
from web3 import Web3
from eth_account import Account

load_dotenv(Path('/opt/lagbot/lagbot/.env'))

w3 = Web3(Web3.HTTPProvider('https://polygon.drpc.org'))
wallet = os.environ['WALLET_ADDRESS']
private_key = os.environ['PRIVATE_KEY']

# CTF Exchange ABI (minimal - just what we need)
# Check available functions on the exchange contract
exchange_addr = '0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E'

# Get the exchange contract's function selectors
# Common Polymarket exchange functions:
# - deposit(address, uint256)
# - withdraw(address, uint256)
# - depositFor(address, address, uint256)

# Let's check if these functions exist by calling them with static data
# Function selectors:
selectors = {
    'withdraw(address,uint256)': w3.keccak(text='withdraw(address,uint256)')[:4].hex(),
    'withdrawTo(address,address,uint256)': w3.keccak(text='withdrawTo(address,address,uint256)')[:4].hex(),
    'deposit(address,uint256)': w3.keccak(text='deposit(address,uint256)')[:4].hex(),
    'getBalance(uint256,address)': w3.keccak(text='getBalance(uint256,address)')[:4].hex(),
    'balanceOf(address)': w3.keccak(text='balanceOf(address)')[:4].hex(),
}

print("Function selectors:")
for name, sel in selectors.items():
    print(f"  {name}: 0x{sel}")

# Check the exchange contract code to find matching selectors
code = w3.eth.get_code(Web3.to_checksum_address(exchange_addr)).hex()
print(f"\nExchange contract code length: {len(code)//2} bytes")

for name, sel in selectors.items():
    if sel in code:
        print(f"  FOUND: {name} (0x{sel})")
    else:
        print(f"  NOT FOUND: {name}")

# Try Neg Risk Exchange too
neg_risk_addr = '0xC5d563A36AE78145C45a50134d48A1215220f80a'
code_nr = w3.eth.get_code(Web3.to_checksum_address(neg_risk_addr)).hex()
print(f"\nNeg Risk contract code length: {len(code_nr)//2} bytes")

# Check USDC.e balance of the exchange contract (where deposited funds sit)
USDC_E = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
abi = json.loads('[{"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]')
usdc_contract = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=abi)
exchange_usdc = usdc_contract.functions.balanceOf(Web3.to_checksum_address(exchange_addr)).call()
print(f"\nUSDC.e held by CTF Exchange: ${exchange_usdc / 1e6:,.2f}")

# Check the Neg Risk adapter too
adapter_addr = '0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296'
adapter_usdc = usdc_contract.functions.balanceOf(Web3.to_checksum_address(adapter_addr)).call()
print(f"USDC.e held by Neg Risk Adapter: ${adapter_usdc / 1e6:,.2f}")

# Also check if there's a simpler withdraw via the CLOB HTTP API
import requests
print("\nChecking CLOB API for withdrawal endpoints...")
try:
    resp = requests.get("https://clob.polymarket.com/", timeout=10)
    print(f"CLOB API root: {resp.status_code}")
except Exception as e:
    print(f"CLOB root check: {e}")
