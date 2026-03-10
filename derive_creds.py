#!/usr/bin/env python3
"""Derive CLOB API credentials from private key and populate an instance .env.

Usage:
    PK=<private_key> WA=<wallet_address> INSTANCE=<instance_name> python3 derive_creds.py

INSTANCE defaults to 'chudi'. For a new client:
    PK=0x... WA=0x... INSTANCE=emmanuel python3 derive_creds.py
"""
import os, sys

def main():
    pk = os.environ.get('PK')
    wa = os.environ.get('WA')
    instance = os.environ.get('INSTANCE', 'chudi')

    if not all([pk, wa]):
        print('ERROR: Set env vars PK and WA')
        print('Usage: PK=<private_key> WA=<wallet_address> INSTANCE=<name> python3 derive_creds.py')
        sys.exit(1)

    env_path = f'/opt/lagbot/instances/{instance}/.env'
    if not os.path.exists(env_path):
        print(f'ERROR: {env_path} not found. Create instance directory first.')
        sys.exit(1)

    # Derive CLOB credentials
    from py_clob_client.client import ClobClient
    client = ClobClient(
        host='https://clob.polymarket.com',
        chain_id=137,
        key=pk,
        signature_type=1,
    )
    creds = client.create_or_derive_api_creds()
    print(f'CLOB API Key: {creds.api_key[:8]}...')
    print(f'CLOB Secret: {creds.api_secret[:8]}...')
    print(f'CLOB Passphrase: {creds.api_passphrase[:8]}...')

    # Validate credentials
    from py_clob_client.clob_types import ApiCreds
    verified_client = ClobClient(
        host='https://clob.polymarket.com',
        chain_id=137,
        key=pk,
        creds=ApiCreds(
            api_key=creds.api_key,
            api_secret=creds.api_secret,
            api_passphrase=creds.api_passphrase,
        ),
        signature_type=1,
    )
    ok = verified_client.get_ok()
    print(f'CLOB auth test: {ok}')
    if ok != 'OK':
        print('ERROR: CLOB credential verification failed!')
        sys.exit(1)

    # Update .env file
    with open(env_path, 'r') as f:
        content = f.read()

    replacements = {
        'PRIVATE_KEY=FILL_IN': f'PRIVATE_KEY={pk}',
        'WALLET_ADDRESS=FILL_IN': f'WALLET_ADDRESS={wa}',
        'CLOB_API_KEY=FILL_IN': f'CLOB_API_KEY={creds.api_key}',
        'CLOB_SECRET=FILL_IN': f'CLOB_SECRET={creds.api_secret}',
        'CLOB_PASSPHRASE=FILL_IN': f'CLOB_PASSPHRASE={creds.api_passphrase}',
    }
    for old, new in replacements.items():
        content = content.replace(old, new)

    with open(env_path, 'w') as f:
        f.write(content)
    os.chmod(env_path, 0o600)
    print(f'Credentials written to {env_path}')
    print('All credentials validated and stored.')

if __name__ == '__main__':
    main()
