#!/usr/bin/env python3
"""Re-derive CLOB credentials with signature_type=2 (EOA) and update .env."""
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
from dotenv import load_dotenv
import os

load_dotenv("polyphemus/.env")
pk = os.getenv("PRIVATE_KEY")

# Derive CLOB creds with signature_type=2 (EOA)
client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=pk,
    signature_type=2,
)
creds = client.create_or_derive_api_creds()
print(f"New CLOB API Key (EOA): {creds.api_key[:8]}...")

# Validate
client2 = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=pk,
    creds=ApiCreds(api_key=creds.api_key, api_secret=creds.api_secret, api_passphrase=creds.api_passphrase),
    signature_type=2,
)
bal = client2.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
balance = float(bal.get("balance", 0)) / 1e6
print(f"Balance with sig_type=2: ${balance:.2f}")

if balance <= 0:
    print("ERROR: Balance still $0 — something is wrong")
    exit(1)

# Update .env: replace old CLOB creds and add SIGNATURE_TYPE
env_path = "polyphemus/.env"
with open(env_path, "r") as f:
    content = f.read()

# Replace CLOB credentials
old_key = os.getenv("CLOB_API_KEY")
old_secret = os.getenv("CLOB_SECRET")
old_pass = os.getenv("CLOB_PASSPHRASE")
content = content.replace(f"CLOB_API_KEY={old_key}", f"CLOB_API_KEY={creds.api_key}")
content = content.replace(f"CLOB_SECRET={old_secret}", f"CLOB_SECRET={creds.api_secret}")
content = content.replace(f"CLOB_PASSPHRASE={old_pass}", f"CLOB_PASSPHRASE={creds.api_passphrase}")

# Add SIGNATURE_TYPE=2 if not present
if "SIGNATURE_TYPE=" not in content:
    content += "\nSIGNATURE_TYPE=2\n"
else:
    import re
    content = re.sub(r"SIGNATURE_TYPE=\d+", "SIGNATURE_TYPE=2", content)

with open(env_path, "w") as f:
    f.write(content)
os.chmod(env_path, 0o600)

print(f"Updated .env with EOA credentials and SIGNATURE_TYPE=2")
print("Done!")
