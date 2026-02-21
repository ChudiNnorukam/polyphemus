# py_clob_client v0.34.5 — Complete Diagnostics Report

**Generated**: 2026-02-13 | **VPS**: `159.223.236.50` | **Location**: `/opt/lagbot/venv/lib/python3.12/site-packages`

---

## 🎯 Key Findings

### ✅ WHAT WORKS
1. **py-clob-client v0.34.5** is correctly installed
2. Both balance methods exist and are syntactically correct
3. ClobClient can be initialized
4. Method signatures match official Polymarket API

### ❌ WHAT'S BROKEN
1. **No API credentials on VPS** — `/opt/lagbot/.env` does not exist
2. **Systemd service not configured** — No `EnvironmentFile` directive
3. **Cannot call authenticated endpoints** until credentials are loaded

---

## Part 1: ClobClient Initialization

### Constructor Signature

```python
ClobClient(
    host: str,                                    # CLOB API endpoint URL
    chain_id: int = None,                        # Blockchain chain ID (137 = Polygon mainnet)
    key: str = None,                             # Private key for signing (hex format)
    creds: py_clob_client.clob_types.ApiCreds = None,  # API credentials
    signature_type: int = None,                  # 1 = Proxy wallet, 2 = EOA
    funder: str = None,                          # Funder address (optional)
    builder_config: BuilderConfig = None,        # Custom builder config
)
```

### Authentication Levels

| Level | Requirements | Capabilities |
|-------|-------------|--------------|
| **L0** | `host` only | `get_markets()`, `get_order_book()`, market data |
| **L1** | `host` + `chain_id` + `key` | Order placement (unsigned) |
| **L2** | `host` + `chain_id` + `key` + `creds` | **Balance queries, approvals, all operations** |

### Correct L2 Initialization

```python
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

creds = ApiCreds(
    api_key="pm_api_key_...",
    api_secret="...",
    api_passphrase="...",
)

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key="0x1234....",  # Private key
    creds=creds,
    signature_type=1,
)
```

---

## Part 2: Balance & Allowance Methods

### Method 1: `update_balance_allowance()`

**Purpose**: Fetch fresh balance & allowance from the API (always current)

**Signature**:
```python
def update_balance_allowance(self, params: BalanceAllowanceParams = None) -> dict
```

**Parameters**:
```python
BalanceAllowanceParams(
    asset_type: AssetType = None,           # COLLATERAL or CONDITIONAL
    token_id: str = None,                   # Specific token (optional)
    signature_type: int = -1,               # Default uses client.sig_type
)
```

**Implementation**:
```python
def update_balance_allowance(self, params: BalanceAllowanceParams = None):
    self.assert_level_2_auth()              # ← REQUIRES LEVEL 2 AUTH
    request_args = RequestArgs(method="GET", request_path=UPDATE_BALANCE_ALLOWANCE)
    headers = create_level_2_headers(self.signer, self.creds, request_args)
    if params.signature_type == -1:
        params.signature_type = self.builder.sig_type
    url = add_balance_allowance_params_to_url(
        "{}{}".format(self.host, UPDATE_BALANCE_ALLOWANCE), params
    )
    return get(url, headers=headers)
```

**API Endpoint**: `GET /update-balance-allowance?asset_type=COLLATERAL&signature_type=1`

**Authentication**: Level 2 (signed headers required)

**Returns**:
```python
{
    "balance": "1000000000",      # String, 6 decimals (divide by 1e6)
    "allowance": "999999999",     # String, 6 decimals
    "asset_type": "COLLATERAL"    # String
}
```

---

### Method 2: `get_balance_allowance()`

**Purpose**: Same as `update_balance_allowance()` (different endpoint, same data)

**Signature**:
```python
def get_balance_allowance(self, params: BalanceAllowanceParams = None) -> dict
```

**Implementation**:
```python
def get_balance_allowance(self, params: BalanceAllowanceParams = None):
    self.assert_level_2_auth()              # ← ALSO REQUIRES LEVEL 2 AUTH
    request_args = RequestArgs(method="GET", request_path=GET_BALANCE_ALLOWANCE)
    headers = create_level_2_headers(self.signer, self.creds, request_args)
    if params.signature_type == -1:
        params.signature_type = self.builder.sig_type
    url = add_balance_allowance_params_to_url(
        "{}{}".format(self.host, GET_BALANCE_ALLOWANCE), params
    )
    return get(url, headers=headers)
```

**API Endpoint**: `GET /get-balance-allowance?asset_type=COLLATERAL&signature_type=1`

**Authentication**: Level 2 (signed headers required)

**Returns**: Same as `update_balance_allowance()`

---

### Comparison

| Feature | `update_balance_allowance()` | `get_balance_allowance()` |
|---------|------------------------------|--------------------------|
| Endpoint | `/update-balance-allowance` | `/get-balance-allowance` |
| Auth Required | Level 2 | Level 2 |
| Response Format | Same | Same |
| Semantics | "Update" (like refresh) | "Get" (like query) |
| Use Case | When you want fresh data | When you want to query |

**Both require Level 2 authentication.**

---

## Part 3: AssetType Enum

```python
from py_clob_client.clob_types import AssetType

# Available values:
AssetType.COLLATERAL      # USDC (main asset)
AssetType.CONDITIONAL     # Conditional tokens (market-specific)
```

**Usage**:
```python
params = BalanceAllowanceParams(
    asset_type=AssetType.COLLATERAL,
    token_id=None,  # Leave None for overall balance
)
response = client.update_balance_allowance(params)
```

---

## Part 4: VPS Current State

### Environment Diagnostics

| Component | Status | Details |
|-----------|--------|---------|
| **py-clob-client** | ✅ v0.34.5 | `/opt/lagbot/venv/lib/python3.12/site-packages` |
| **Python venv** | ✅ Active | `/opt/lagbot/venv/bin/python3` |
| **Systemd service** | ✅ `lagbot` exists | Type=notify, WatchdogSec=120 |
| **.env file** | ❌ **MISSING** | `/opt/lagbot/.env` not found |
| **Service env vars** | ⚠️ Empty | Only `PYTHONUNBUFFERED=1` (no credentials) |
| **Private key** | ❌ Not loaded | Required for signing |
| **API credentials** | ❌ Not loaded | Required for authenticated calls |

### Error When Testing

```python
❌ PolyException: API Credentials are needed to interact with this endpoint!
```

**Root cause**: `assert_level_2_auth()` checks if `self.creds` is not None. Since no credentials were loaded, it raises `PolyException`.

---

## Part 5: VPS Setup Instructions

### Step 1: Create `.env` File

Create `/opt/lagbot/.env`:

```bash
#!/bin/bash
cat > /opt/lagbot/.env << 'DOTENV'
# Polymarket API Configuration
POLYMARKET_HOST=https://clob.polymarket.com
CHAIN_ID=137

# Your wallet credentials
PRIVATE_KEY=0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef
SIGNATURE_TYPE=1

# Polymarket API credentials (from your API key dashboard)
POLYMARKET_API_KEY=pm_...
POLYMARKET_API_SECRET=...
POLYMARKET_API_PASSPHRASE=...
DOTENV

chmod 600 /opt/lagbot/.env
```

### Step 2: Update Systemd Service

Edit `/etc/systemd/system/lagbot.service`:

**BEFORE**:
```ini
[Service]
Type=notify
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/lagbot/venv/bin/python3 /opt/lagbot/main.py
```

**AFTER**:
```ini
[Service]
Type=notify
EnvironmentFile=/opt/lagbot/.env
ExecStart=/opt/lagbot/venv/bin/python3 /opt/lagbot/main.py
Restart=on-failure
RestartSec=5
WatchdogSec=120
```

### Step 3: Reload & Restart

```bash
systemctl daemon-reload
systemctl restart lagbot
```

### Step 4: Verify (Test Script)

```python
#!/usr/bin/env python3
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType, ApiCreds

# Load environment
load_dotenv('/opt/lagbot/.env')

# Create credentials
creds = ApiCreds(
    api_key=os.getenv('POLYMARKET_API_KEY'),
    api_secret=os.getenv('POLYMARKET_API_SECRET'),
    api_passphrase=os.getenv('POLYMARKET_API_PASSPHRASE'),
)

# Initialize
client = ClobClient(
    host=os.getenv('POLYMARKET_HOST'),
    chain_id=int(os.getenv('CHAIN_ID')),
    key=os.getenv('PRIVATE_KEY'),
    creds=creds,
    signature_type=int(os.getenv('SIGNATURE_TYPE')),
)

# Test call
try:
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    response = client.update_balance_allowance(params)
    
    balance = int(response['balance']) / 1e6
    allowance = int(response['allowance']) / 1e6
    
    print(f"✅ SUCCESS")
    print(f"   Balance: {balance:.2f} USDC")
    print(f"   Allowance: {allowance:.2f} USDC")
    
except Exception as e:
    print(f"❌ FAILED: {e}")
```

---

## Part 6: Complete Reference Implementation

```python
import os
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType, ApiCreds

class PolywatchClient:
    def __init__(self, env_path: str = '/opt/lagbot/.env'):
        # Load credentials from .env
        load_dotenv(env_path)
        
        self.creds = ApiCreds(
            api_key=os.getenv('POLYMARKET_API_KEY'),
            api_secret=os.getenv('POLYMARKET_API_SECRET'),
            api_passphrase=os.getenv('POLYMARKET_API_PASSPHRASE'),
        )
        
        self.client = ClobClient(
            host=os.getenv('POLYMARKET_HOST', 'https://clob.polymarket.com'),
            chain_id=int(os.getenv('CHAIN_ID', '137')),
            key=os.getenv('PRIVATE_KEY'),
            creds=self.creds,
            signature_type=int(os.getenv('SIGNATURE_TYPE', '1')),
        )
    
    def get_balance(self) -> dict:
        """Get USDC balance and allowance"""
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        response = self.client.update_balance_allowance(params)
        
        return {
            'balance_raw': int(response['balance']),
            'balance_usdc': int(response['balance']) / 1e6,
            'allowance_raw': int(response['allowance']),
            'allowance_usdc': int(response['allowance']) / 1e6,
            'asset_type': response['asset_type'],
        }
    
    def get_conditional_balance(self, token_id: str) -> dict:
        """Get balance for a specific conditional token"""
        params = BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL,
            token_id=token_id,
        )
        response = self.client.update_balance_allowance(params)
        
        return {
            'balance_raw': int(response['balance']),
            'balance_tokens': int(response['balance']) / 1e6,
            'allowance_raw': int(response['allowance']),
            'token_id': token_id,
        }

# Usage
if __name__ == '__main__':
    client = PolywatchClient()
    
    balance = client.get_balance()
    print(f"USDC: {balance['balance_usdc']:.2f}")
    print(f"Approval: {balance['allowance_usdc']:.2f}")
```

---

## Part 7: Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `TypeError: ClobClient.__init__() got an unexpected keyword argument 'private_key'` | Wrong parameter name | Use `key=` not `private_key=` |
| `PolyException: API Credentials are needed...` | No credentials loaded | Create `.env` file with `POLYMARKET_API_*` vars |
| `FileNotFoundError: .env` | .env file missing | Create `/opt/lagbot/.env` |
| `ModuleNotFoundError: web3` | web3 not installed | `pip install web3` (optional, for MATIC queries) |
| `JSONDecodeError` in response | Malformed API response | Check endpoint URL and auth headers |

---

## Summary Table

| Item | Value |
|------|-------|
| **Package** | py-clob-client v0.34.5 |
| **VPS** | 159.223.236.50 |
| **Venv** | /opt/lagbot/venv |
| **Service** | lagbot (systemd) |
| **Status** | ✅ Installed, ❌ Credentials missing |
| **Blocking Issue** | No `.env` file at `/opt/lagbot/.env` |
| **Solution** | Create `.env` + update systemd service + restart |
| **Time to Fix** | ~5 minutes |

---

## Files to Create/Modify

```
/opt/lagbot/.env                              [CREATE]
/etc/systemd/system/lagbot.service            [MODIFY - add EnvironmentFile]
```

---

