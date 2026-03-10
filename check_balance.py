#!/usr/bin/env python3
"""Check CLOB balance with both signature types."""
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
from dotenv import load_dotenv
import os

load_dotenv("polyphemus/.env")
pk = os.getenv("PRIVATE_KEY")
wa = os.getenv("WALLET_ADDRESS")
print(f"Wallet: {wa}")

# Try signature_type=1 (Proxy)
creds = ApiCreds(
    api_key=os.getenv("CLOB_API_KEY"),
    api_secret=os.getenv("CLOB_SECRET"),
    api_passphrase=os.getenv("CLOB_PASSPHRASE"),
)
client1 = ClobClient(host="https://clob.polymarket.com", chain_id=137, key=pk, creds=creds, signature_type=1)
bal1 = client1.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
b1 = float(bal1.get("balance", 0)) / 1e6
print(f"sig_type=1 (Proxy): balance=${b1:.2f}")
print(f"  raw: {bal1}")

# Try signature_type=2 (EOA)
try:
    client2 = ClobClient(host="https://clob.polymarket.com", chain_id=137, key=pk, signature_type=2)
    creds2 = client2.create_or_derive_api_creds()
    client2b = ClobClient(
        host="https://clob.polymarket.com", chain_id=137, key=pk,
        creds=ApiCreds(api_key=creds2.api_key, api_secret=creds2.api_secret, api_passphrase=creds2.api_passphrase),
        signature_type=2,
    )
    bal2 = client2b.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    b2 = float(bal2.get("balance", 0)) / 1e6
    print(f"sig_type=2 (EOA):   balance=${b2:.2f}")
    print(f"  raw: {bal2}")
except Exception as e:
    print(f"sig_type=2 error: {e}")
