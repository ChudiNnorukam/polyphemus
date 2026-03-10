"""Check on-chain balances for wallet."""
from web3 import Web3
import json

w3 = Web3(Web3.HTTPProvider('https://polygon.drpc.org'))

wallet = '0x1C0523D33b0D1c7Df8Ec450C5318cFcFc32Ce80A'

# ERC20 balanceOf ABI
abi = json.loads('[{"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]')

# USDC.e (bridged) on Polygon
USDC_E = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
usdc_e = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=abi)
bal_e = usdc_e.functions.balanceOf(Web3.to_checksum_address(wallet)).call()
print(f'USDC.e balance: ${bal_e / 1e6:.2f}')

# Native USDC on Polygon
USDC_NATIVE = '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359'
usdc_n = w3.eth.contract(address=Web3.to_checksum_address(USDC_NATIVE), abi=abi)
bal_n = usdc_n.functions.balanceOf(Web3.to_checksum_address(wallet)).call()
print(f'USDC native balance: ${bal_n / 1e6:.2f}')

# MATIC/POL
matic = w3.eth.get_balance(Web3.to_checksum_address(wallet))
print(f'MATIC/POL balance: {matic / 1e18:.6f}')

# Transaction count
nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(wallet))
print(f'Transaction nonce: {nonce}')

# Is this a contract or EOA?
code = w3.eth.get_code(Web3.to_checksum_address(wallet))
print(f'Is contract: {len(code) > 0} ({len(code)} bytes)')

# Check if Polymarket CTF Exchange has USDC allowance from this wallet
ALLOWANCE_ABI = json.loads('[{"constant":true,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"}]')

CTF_EXCHANGE = '0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E'
NEG_RISK = '0xC5d563A36AE78145C45a50134d48A1215220f80a'
NEG_ADAPTER = '0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296'

usdc_contract = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=abi + ALLOWANCE_ABI)

for name, spender in [('CTF Exchange', CTF_EXCHANGE), ('Neg Risk', NEG_RISK), ('Neg Adapter', NEG_ADAPTER)]:
    allow = usdc_contract.functions.allowance(
        Web3.to_checksum_address(wallet),
        Web3.to_checksum_address(spender)
    ).call()
    print(f'USDC.e allowance to {name}: ${allow / 1e6:.2f}')
