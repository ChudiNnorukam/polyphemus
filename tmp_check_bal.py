import os
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

w3 = Web3(Web3.HTTPProvider(os.environ.get("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com")))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
wallet = Web3.to_checksum_address(os.environ["WALLET_ADDRESS"])

usdc = w3.eth.contract(
    address=Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
    abi=[{"name": "balanceOf", "type": "function", "stateMutability": "view",
          "inputs": [{"name": "account", "type": "address"}],
          "outputs": [{"name": "", "type": "uint256"}]}],
)
bal = usdc.functions.balanceOf(wallet).call() / 1e6
pol = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
print(f"USDC.e: ${bal:.2f}")
print(f"POL: {pol:.4f}")
