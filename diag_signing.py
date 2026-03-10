"""Deep diagnostic: compare order signatures across signature types."""
import os, json, time, asyncio
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path('/opt/lagbot/lagbot/.env'))

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs
from py_clob_client.order_builder.constants import BUY
import inspect

private_key = os.environ['PRIVATE_KEY']
wallet = os.environ['WALLET_ADDRESS']
creds = ApiCreds(
    api_key=os.environ['CLOB_API_KEY'],
    api_secret=os.environ['CLOB_SECRET'],
    api_passphrase=os.environ['CLOB_PASSPHRASE'],
)

# Check the signer source code for signature_type differences
from py_clob_client.signer import Signer
print("=== Signer.sign source ===")
print(inspect.getsource(Signer.sign))

# Check order builder's create_order for how sig_type is used
from py_clob_client.order_builder.builder import OrderBuilder
print("\n=== OrderBuilder.create_order source ===")
print(inspect.getsource(OrderBuilder.create_order))

# Check what py_order_utils does with signature_type
try:
    from py_order_utils.builders import OrderBuilder as PyOrderBuilder
    print("\n=== py_order_utils OrderBuilder ===")
    src = inspect.getsource(PyOrderBuilder)
    # Find signature_type usage
    for i, line in enumerate(src.split('\n')):
        if 'sig_type' in line.lower() or 'signature' in line.lower():
            print(f"  Line {i}: {line.strip()}")
except Exception as e:
    print(f"py_order_utils check failed: {e}")

# Check the signing utils
try:
    from py_order_utils import config as order_config
    print("\n=== py_order_utils config ===")
    for attr in dir(order_config):
        if not attr.startswith('_'):
            val = getattr(order_config, attr)
            if not callable(val):
                print(f"  {attr} = {val}")
except Exception as e:
    print(f"config check: {e}")

# Check signature type constants
try:
    from py_order_utils.model.signatures import SignatureType
    print("\n=== SignatureType values ===")
    for attr in dir(SignatureType):
        if not attr.startswith('_'):
            print(f"  {attr} = {getattr(SignatureType, attr)}")
except Exception as e:
    print(f"SignatureType: {e}")

# Now create orders with type 0 and 2 and compare the signatures
import aiohttp

async def get_real_token():
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
        return tokens[0], slug
    return None, None

token_id, slug = asyncio.run(get_real_token())
if token_id:
    print(f"\n=== Testing with {slug}, token={token_id[:30]}... ===")

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
            order_args = OrderArgs(
                token_id=token_id,
                price=0.50,
                size=10,
                side=BUY,
            )
            signed = client.create_order(order_args)
            sig = signed.signature if hasattr(signed, 'signature') else 'N/A'
            print(f"\nsig_type={sig_type}:")
            print(f"  signature length: {len(sig)}")
            print(f"  signature prefix: {sig[:20]}...")
            print(f"  signature suffix: ...{sig[-10:]}")

            # Try posting
            try:
                result = client.post_order(signed)
                print(f"  post_order: SUCCESS - {result}")
            except Exception as e:
                print(f"  post_order: FAILED - {e}")
        except Exception as e:
            print(f"\nsig_type={sig_type}: create_order FAILED - {e}")
