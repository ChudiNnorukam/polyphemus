"""Test using BUILDER API credentials instead of CLOB credentials."""
import os, json, time, asyncio
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path('/opt/lagbot/lagbot/.env'))

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds, OrderArgs, BalanceAllowanceParams, AssetType,
)
from py_clob_client.order_builder.constants import BUY
import aiohttp

private_key = os.environ['PRIVATE_KEY']
wallet = os.environ['WALLET_ADDRESS']

# Standard CLOB creds
clob_creds = ApiCreds(
    api_key=os.environ['CLOB_API_KEY'],
    api_secret=os.environ['CLOB_SECRET'],
    api_passphrase=os.environ['CLOB_PASSPHRASE'],
)

# BUILDER creds
builder_creds = ApiCreds(
    api_key=os.environ['BUILDER_API_KEY'],
    api_secret=os.environ['BUILDER_SECRET'],
    api_passphrase=os.environ['BUILDER_PASSPHRASE'],
)

# Get a current market token
async def get_token():
    window = 300
    epoch = int(time.time() // window) * window
    slug = f'btc-updown-5m-{epoch}'
    url = f'https://gamma-api.polymarket.com/markets?slug={slug}'
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()
    if data:
        market = data[0] if isinstance(data, list) else data
        return json.loads(market['clobTokenIds'])[0], slug
    return None, None

token_id, slug = asyncio.run(get_token())
print(f"Market: {slug}")
print(f"Token: {token_id[:40]}...")

# Test 1: Builder creds + sig_type 0
print("\n=== Test 1: Builder creds + sig_type=0 ===")
client_b0 = ClobClient(
    host='https://clob.polymarket.com',
    key=private_key,
    chain_id=137,
    creds=builder_creds,
    signature_type=0,
    funder=wallet,
)
try:
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    bal = client_b0.get_balance_allowance(params)
    print(f"Balance: ${float(bal.get('balance', 0)) / 1e6:.2f}")
except Exception as e:
    print(f"Balance: {e}")

try:
    order_args = OrderArgs(token_id=token_id, price=0.50, size=10, side=BUY)
    signed = client_b0.create_order(order_args)
    result = client_b0.post_order(signed)
    print(f"Order: SUCCESS - {result}")
except Exception as e:
    err = str(e)
    print(f"Order: {err[:100]}")

# Test 2: Builder creds + sig_type 2
print("\n=== Test 2: Builder creds + sig_type=2 ===")
client_b2 = ClobClient(
    host='https://clob.polymarket.com',
    key=private_key,
    chain_id=137,
    creds=builder_creds,
    signature_type=2,
    funder=wallet,
)
try:
    params2 = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    bal2 = client_b2.get_balance_allowance(params2)
    print(f"Balance: ${float(bal2.get('balance', 0)) / 1e6:.2f}")
except Exception as e:
    print(f"Balance: {e}")

try:
    order_args2 = OrderArgs(token_id=token_id, price=0.50, size=10, side=BUY)
    signed2 = client_b2.create_order(order_args2)
    result2 = client_b2.post_order(signed2)
    print(f"Order: SUCCESS - {result2}")
except Exception as e:
    err2 = str(e)
    print(f"Order: {err2[:100]}")

# Test 3: Check if there's a builder-specific order method
print("\n=== Test 3: Check builder methods ===")
print(f"can_builder_auth: {client_b0.can_builder_auth()}")
builder_methods = [m for m in dir(client_b0) if 'builder' in m.lower()]
print(f"Builder methods: {builder_methods}")

# Test 4: Try get_builder_trades
try:
    trades = client_b0.get_builder_trades()
    print(f"Builder trades: {trades}")
except Exception as e:
    print(f"Builder trades: {str(e)[:80]}")
