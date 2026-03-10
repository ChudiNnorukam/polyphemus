"""Diagnostic: check wallet type, allowances, and test order signing."""
import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).parent / "polyphemus" / ".env"
if not env_path.exists():
    # Try lagbot path on VPS
    env_path = Path("/opt/lagbot/lagbot/.env")
load_dotenv(env_path)

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType, OrderArgs
from py_clob_client.order_builder.constants import BUY

private_key = os.environ["PRIVATE_KEY"]
wallet_address = os.environ["WALLET_ADDRESS"]
clob_api_key = os.environ["CLOB_API_KEY"]
clob_secret = os.environ["CLOB_SECRET"]
clob_passphrase = os.environ["CLOB_PASSPHRASE"]

print(f"Wallet: {wallet_address}")
print(f"Private key (first 8): {private_key[:8]}...")

# Test each signature type
for sig_type in [0, 1, 2]:
    print(f"\n{'='*60}")
    print(f"Testing signature_type={sig_type}")
    print(f"{'='*60}")
    try:
        creds = ApiCreds(
            api_key=clob_api_key,
            api_secret=clob_secret,
            api_passphrase=clob_passphrase,
        )
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
            creds=creds,
            signature_type=sig_type,
            funder=wallet_address,
        )

        # Test 1: Balance
        try:
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            bal = client.get_balance_allowance(params)
            balance = float(bal.get("balance", 0)) / 1e6
            allowance = float(bal.get("allowance", 0)) / 1e6
            print(f"  Balance: ${balance:.2f}")
            print(f"  Allowance: ${allowance:.2f}")
        except Exception as e:
            print(f"  Balance FAILED: {e}")

        # Test 2: Try to create (NOT post) a small order to test signing
        try:
            # Use a known BTC 5-min token (we'll just sign, not submit)
            # Get a real token from midpoints
            test_args = OrderArgs(
                token_id="placeholder",
                price=0.50,
                size=10,
                side=BUY,
            )
            signed = client.create_order(test_args)
            print(f"  Order signing: SUCCESS")
            print(f"  Signed order keys: {list(signed.keys()) if isinstance(signed, dict) else type(signed)}")
        except Exception as e:
            print(f"  Order signing: FAILED - {e}")

    except Exception as e:
        print(f"  Client init FAILED: {e}")
