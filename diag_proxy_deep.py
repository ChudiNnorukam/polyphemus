"""Check proxy contract code and Polymarket proxy factory."""
from web3 import Web3
import json

w3 = Web3(Web3.HTTPProvider('https://polygon.drpc.org'))
wallet = '0x1C0523D33b0D1c7Df8Ec450C5318cFcFc32Ce80A'

# Get the contract bytecode
code = w3.eth.get_code(Web3.to_checksum_address(wallet))
print(f'Contract bytecode ({len(code)} bytes): {code.hex()}')

# EIP-1167 minimal proxy pattern: 363d3d373d3d3d363d73<address>5af43d82803e903d91602b57fd5bf3
# If it matches, extract the master address
hex_code = code.hex()
if '363d3d373d3d3d363d73' in hex_code:
    # Extract 20-byte address after the prefix
    idx = hex_code.index('363d3d373d3d3d363d73') + len('363d3d373d3d3d363d73')
    master_addr = '0x' + hex_code[idx:idx+40]
    print(f'EIP-1167 proxy detected! Master: {master_addr}')
else:
    print(f'Not a standard EIP-1167 proxy. Full bytecode: {hex_code}')

# Check Polymarket proxy factory
PROXY_FACTORY = '0xaB45c5A4B0c941a2F231C04C3f49182e1A254052'
# getProxy(address) function selector: 0xd5a9b4a2
try:
    # Encode the function call: getProxy(wallet)
    fn_sig = w3.keccak(text='getProxy(address)')[:4]
    data = fn_sig + bytes.fromhex(wallet[2:].rjust(64, '0'))
    result = w3.eth.call({
        'to': Web3.to_checksum_address(PROXY_FACTORY),
        'data': data.hex(),
    })
    proxy_addr = '0x' + result.hex()[-40:]
    print(f'\nProxy factory getProxy({wallet}) = {proxy_addr}')

    # Check if this proxy matches our wallet
    print(f'Proxy matches wallet: {proxy_addr.lower() == wallet.lower()}')

    # Check balance of the proxy address on CLOB
    if proxy_addr.lower() != wallet.lower():
        proxy_code = w3.eth.get_code(Web3.to_checksum_address(proxy_addr))
        print(f'Proxy at {proxy_addr}: {len(proxy_code)} bytes code')
except Exception as e:
    print(f'Proxy factory call failed: {e}')

# Also check the Polymarket Proxy Wallet Factory (alternative)
ALT_FACTORY = '0x54b56661CEC6d288CFf1CaCc6a72F7Ceb9e3A3c6'
try:
    fn_sig = w3.keccak(text='getProxy(address)')[:4]
    data = fn_sig + bytes.fromhex(wallet[2:].rjust(64, '0'))
    result = w3.eth.call({
        'to': Web3.to_checksum_address(ALT_FACTORY),
        'data': data.hex(),
    })
    proxy_addr2 = '0x' + result.hex()[-40:]
    print(f'\nAlt factory getProxy({wallet}) = {proxy_addr2}')
except Exception as e:
    print(f'Alt factory failed: {e}')

# Check the CTF Exchange for operator status
CTF_EXCHANGE = '0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E'
# isApprovedForAll(address,address) - check if exchange is approved
CTF_TOKEN = '0x4D97DCd97eC945f40cF65F87097ACe5EA0476045'
try:
    abi = json.loads('[{"inputs":[{"name":"account","type":"address"},{"name":"operator","type":"address"}],"name":"isApprovedForAll","outputs":[{"name":"","type":"bool"}],"stateMutability":"view","type":"function"}]')
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_TOKEN), abi=abi)
    approved = ctf.functions.isApprovedForAll(
        Web3.to_checksum_address(wallet),
        Web3.to_checksum_address(CTF_EXCHANGE)
    ).call()
    print(f'\nCTF isApprovedForAll(wallet, exchange): {approved}')
except Exception as e:
    print(f'CTF approval check: {e}')
