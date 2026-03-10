"""Find proxy wallet and check on-chain balances."""
import os, json
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path('/opt/lagbot/lagbot/.env'))

wallet = os.environ['WALLET_ADDRESS']
private_key = os.environ['PRIVATE_KEY']

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

creds = ApiCreds(
    api_key=os.environ['CLOB_API_KEY'],
    api_secret=os.environ['CLOB_SECRET'],
    api_passphrase=os.environ['CLOB_PASSPHRASE'],
)

# Check get_address for each signature type
for sig_type in [0, 1, 2]:
    client = ClobClient(
        host='https://clob.polymarket.com',
        key=private_key,
        chain_id=137,
        creds=creds,
        signature_type=sig_type,
        funder=wallet,
    )
    try:
        addr = client.get_address()
        print(f'sig_type={sig_type}: get_address={addr}')
    except Exception as e:
        print(f'sig_type={sig_type}: get_address failed: {e}')

# Check builder funder for each type
for sig_type in [0, 1, 2]:
    client = ClobClient(
        host='https://clob.polymarket.com',
        key=private_key,
        chain_id=137,
        creds=creds,
        signature_type=sig_type,
        funder=wallet,
    )
    print(f'sig_type={sig_type}: builder.funder={client.builder.funder}')

# Check on-chain USDC and MATIC
from web3 import Web3

w3 = Web3(Web3.HTTPProvider('https://polygon.drpc.org'))

USDC_ADDRESS = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
USDC_ABI = json.loads('[{"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]')

usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_ADDRESS), abi=USDC_ABI)
bal = usdc.functions.balanceOf(Web3.to_checksum_address(wallet)).call()
print(f'\nUSDC balance of EOA ({wallet}): ${bal / 1e6:.2f}')

matic_bal = w3.eth.get_balance(Web3.to_checksum_address(wallet))
print(f'MATIC balance: {matic_bal / 1e18:.6f} MATIC')

nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(wallet))
print(f'Transaction nonce: {nonce}')

code = w3.eth.get_code(Web3.to_checksum_address(wallet))
print(f'Is contract: {len(code) > 0} ({len(code)} bytes)')
