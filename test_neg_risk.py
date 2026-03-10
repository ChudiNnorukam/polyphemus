"""Test order with explicit neg_risk=True and check exchange addresses."""
import os, json, time, asyncio
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path('/opt/lagbot/lagbot/.env'))

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds, OrderArgs, PartialCreateOrderOptions
)
from py_clob_client.order_builder.constants import BUY
import inspect
import aiohttp

private_key = os.environ['PRIVATE_KEY']
wallet = os.environ['WALLET_ADDRESS']
creds = ApiCreds(
    api_key=os.environ['CLOB_API_KEY'],
    api_secret=os.environ['CLOB_SECRET'],
    api_passphrase=os.environ['CLOB_PASSPHRASE'],
)

# Check exchange addresses for neg_risk=True vs False
try:
    from py_clob_client.config import get_contract_config
    config_false = get_contract_config(137, neg_risk=False)
    config_true = get_contract_config(137, neg_risk=True)
    print(f"Exchange (neg_risk=False): {config_false.exchange}")
    print(f"Exchange (neg_risk=True):  {config_true.exchange}")
except Exception as e:
    print(f"Config check failed: {e}")

# Get a real BTC 5-min token
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
        tokens = json.loads(market['clobTokenIds'])
        neg_risk = market.get('negRisk', 'unknown')
        print(f"\nMarket: {slug}")
        print(f"Gamma API negRisk field: {neg_risk}")
        print(f"Token: {tokens[0][:40]}...")
        return tokens[0]
    return None

token_id = asyncio.run(get_token())
if not token_id:
    print("No market found")
    exit()

# Test with signature_type=2 (has balance) and explicit neg_risk options
for sig_type in [0, 2]:
    for neg_risk in [False, True]:
        client = ClobClient(
            host='https://clob.polymarket.com',
            key=private_key,
            chain_id=137,
            creds=creds,
            signature_type=sig_type,
            funder=wallet,
        )

        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=0.50,
                size=10,
                side=BUY,
            )
            options = PartialCreateOrderOptions(neg_risk=neg_risk)
            signed = client.create_order(order_args, options=options)

            try:
                result = client.post_order(signed)
                print(f"\nsig={sig_type} neg_risk={neg_risk}: SUCCESS! {result}")
            except Exception as e:
                error_msg = str(e)
                # Extract just the error message
                if 'error_message' in error_msg:
                    short = error_msg.split("error_message=")[1][:80]
                else:
                    short = error_msg[:80]
                print(f"sig={sig_type} neg_risk={neg_risk}: {short}")
        except Exception as e:
            print(f"sig={sig_type} neg_risk={neg_risk}: create_order FAILED - {str(e)[:80]}")
