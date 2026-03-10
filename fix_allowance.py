"""Try update_balance_allowance to sync on-chain allowances to CLOB."""
import os, json, time, asyncio
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path('/opt/lagbot/lagbot/.env'))

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds, OrderArgs, BalanceAllowanceParams, AssetType,
    PartialCreateOrderOptions,
)
from py_clob_client.order_builder.constants import BUY
import aiohttp

private_key = os.environ['PRIVATE_KEY']
wallet = os.environ['WALLET_ADDRESS']
creds = ApiCreds(
    api_key=os.environ['CLOB_API_KEY'],
    api_secret=os.environ['CLOB_SECRET'],
    api_passphrase=os.environ['CLOB_PASSPHRASE'],
)

# Step 1: Check balance/allowance with type 2 BEFORE update
client2 = ClobClient(
    host='https://clob.polymarket.com',
    key=private_key,
    chain_id=137,
    creds=creds,
    signature_type=2,
    funder=wallet,
)

params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
before = client2.get_balance_allowance(params)
print(f"BEFORE update (type 2):")
print(f"  Balance: ${float(before.get('balance', 0)) / 1e6:.2f}")
print(f"  Allowance: ${float(before.get('allowance', 0)) / 1e6:.2f}")

# Step 2: Call update_balance_allowance to sync from chain
print("\nCalling update_balance_allowance (type 2)...")
try:
    updated = client2.update_balance_allowance(params)
    print(f"  Result: {updated}")
    balance = float(updated.get('balance', 0)) / 1e6
    allowance = float(updated.get('allowance', 0)) / 1e6
    print(f"  Balance: ${balance:.2f}")
    print(f"  Allowance: ${allowance:.2f}")
except Exception as e:
    print(f"  Failed: {e}")

# Step 3: Also try update for type 0
client0 = ClobClient(
    host='https://clob.polymarket.com',
    key=private_key,
    chain_id=137,
    creds=creds,
    signature_type=0,
    funder=wallet,
)

print("\nCalling update_balance_allowance (type 0)...")
try:
    updated0 = client0.update_balance_allowance(params)
    print(f"  Result: {updated0}")
    balance0 = float(updated0.get('balance', 0)) / 1e6
    allowance0 = float(updated0.get('allowance', 0)) / 1e6
    print(f"  Balance: ${balance0:.2f}")
    print(f"  Allowance: ${allowance0:.2f}")
except Exception as e:
    print(f"  Failed: {e}")

# Step 4: If type 0 now has balance/allowance, try placing an order
print("\nChecking balance after update (type 0)...")
try:
    bal0 = client0.get_balance_allowance(params)
    b = float(bal0.get('balance', 0)) / 1e6
    a = float(bal0.get('allowance', 0)) / 1e6
    print(f"  Balance: ${b:.2f}")
    print(f"  Allowance: ${a:.2f}")

    if b > 0:
        print("\n*** TYPE 0 NOW HAS BALANCE! Testing order... ***")
        # Get current market token
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
                return json.loads(market['clobTokenIds'])[0]
            return None

        token_id = asyncio.run(get_token())
        if token_id:
            order_args = OrderArgs(
                token_id=token_id,
                price=0.50,
                size=10,
                side=BUY,
            )
            signed = client0.create_order(order_args)
            try:
                result = client0.post_order(signed)
                print(f"ORDER PLACED SUCCESSFULLY: {result}")
            except Exception as e:
                print(f"Order failed: {e}")
except Exception as e:
    print(f"  Balance check failed: {e}")

# Step 5: Re-check type 2 after update
print("\nRe-check after update (type 2)...")
try:
    bal2 = client2.get_balance_allowance(params)
    print(f"  Balance: ${float(bal2.get('balance', 0)) / 1e6:.2f}")
    print(f"  Allowance: ${float(bal2.get('allowance', 0)) / 1e6:.2f}")
except Exception as e:
    print(f"  Failed: {e}")
