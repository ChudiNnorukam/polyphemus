#!/bin/bash
# Compile FlashLiquidator.sol using Foundry

set -e

CONTRACT_DIR="contracts"
BUILD_DIR="build"

echo "Compiling FlashLiquidator.sol..."

# Check if Foundry is installed
if ! command -v forge &> /dev/null; then
    echo "Foundry not found. Install with: curl -L https://foundry.paradigm.xyz | bash"
    echo ""
    echo "For Forge-less compilation, use Hardhat or Truffle instead:"
    echo "  npm install --save-dev hardhat"
    echo "  npx hardhat compile"
    exit 1
fi

mkdir -p "$BUILD_DIR"

# Compile with Forge
forge compile \
    --root . \
    --contracts "$CONTRACT_DIR/FlashLiquidator.sol" \
    --out "$BUILD_DIR" \
    --optimize \
    --optimizer-runs 200

echo "✅ Compilation complete!"
echo ""
echo "Contract ABI and bytecode in: $BUILD_DIR/"
echo ""
echo "To deploy:"
echo "  forge create --rpc-url \$ARBITRUM_RPC \\"
echo "    --private-key \$PRIVATE_KEY \\"
echo "    --constructor-args <POOL_ADDRESSES_PROVIDER> <UNISWAP_ROUTER> \\"
echo "    contracts/FlashLiquidator.sol:FlashLiquidator"
echo ""
echo "Aave V3 PoolAddressesProvider on Arbitrum: 0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb"
echo "Uniswap V3 SwapRouter02 on Arbitrum: 0xE592427A0AEce92De3Edee1F18E0157C05861564"
