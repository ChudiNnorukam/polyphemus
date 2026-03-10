// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import {IFlashLoanSimpleReceiver} from "@aave/core-v3/contracts/flashloan/interfaces/IFlashLoanSimpleReceiver.sol";
import {IPoolAddressesProvider} from "@aave/core-v3/contracts/interfaces/IPoolAddressesProvider.sol";
import {IPool} from "@aave/core-v3/contracts/interfaces/IPool.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

interface IUniswapV3SwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(
        ExactInputSingleParams calldata params
    ) external payable returns (uint256 amountOut);
}

/**
 * @title FlashLiquidator
 * @notice Flash loan based liquidator for Aave V3
 * @dev Uses flash loans to liquidate positions with 0 capital
 */
contract FlashLiquidator is IFlashLoanSimpleReceiver, Ownable {
    using SafeERC20 for IERC20;

    IPoolAddressesProvider public immutable addressProvider;
    IPool public immutable pool;
    IUniswapV3SwapRouter public immutable uniswapRouter;

    // Fee tier for Uniswap swaps (3000 = 0.3%)
    uint24 public constant UNISWAP_FEE_TIER = 3000;

    // Flashloan parameters
    struct FlashloanParams {
        address collateralAsset;
        address debtAsset;
        address borrower;
        uint256 debtToCover;
    }

    event LiquidationExecuted(
        address indexed borrower,
        address indexed collateralAsset,
        address indexed debtAsset,
        uint256 debtAmount,
        uint256 profitAmount
    );

    event ProfitWithdrawn(address indexed to, uint256 amount);

    constructor(
        address _addressProvider,
        address _uniswapRouter
    ) {
        addressProvider = IPoolAddressesProvider(_addressProvider);
        pool = IPool(addressProvider.getPool());
        uniswapRouter = IUniswapV3SwapRouter(_uniswapRouter);
    }

    /**
     * @notice Execute a liquidation using a flash loan
     * @param collateralAsset The asset to seize as collateral
     * @param debtAsset The asset to repay (debt)
     * @param borrower The position to liquidate
     * @param debtToCover Amount of debt to cover
     */
    function liquidateWithFlashLoan(
        address collateralAsset,
        address debtAsset,
        address borrower,
        uint256 debtToCover
    ) external {
        require(debtToCover > 0, "Debt to cover must be > 0");
        require(collateralAsset != address(0), "Invalid collateral asset");
        require(debtAsset != address(0), "Invalid debt asset");
        require(borrower != address(0), "Invalid borrower");

        // Initiate flash loan
        address[] memory assets = new address[](1);
        assets[0] = debtAsset;

        uint256[] memory amounts = new uint256[](1);
        amounts[0] = debtToCover;

        // Store params for use in executeOperation callback
        bytes memory params = abi.encode(
            FlashloanParams({
                collateralAsset: collateralAsset,
                debtAsset: debtAsset,
                borrower: borrower,
                debtToCover: debtToCover
            })
        );

        pool.flashLoanSimple(address(this), debtAsset, debtToCover, params, 0);
    }

    /**
     * @notice Callback executed by Aave pool during flash loan
     * @param asset The asset being flash loaned
     * @param amount The amount of the flash loan
     * @param premium The fee to repay
     * @param initiator The address that initiated the flash loan
     * @param params Encoded parameters
     */
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bytes32) {
        require(msg.sender == address(pool), "Unauthorized");
        require(initiator == address(this), "Invalid initiator");

        // Decode parameters
        FlashloanParams memory flashParams = abi.decode(params, (FlashloanParams));

        // 1. Approve pool to spend the debt asset
        IERC20(flashParams.debtAsset).safeApprove(address(pool), amount);

        // 2. Execute liquidation call
        // liquidationCall(collateralAsset, debtAsset, user, debtToCover, receiveAToken)
        pool.liquidationCall(
            flashParams.collateralAsset,
            flashParams.debtAsset,
            flashParams.borrower,
            flashParams.debtToCover,
            false // Don't receive aToken
        );

        // 3. Swap collateral back to debt asset
        uint256 collateralReceived = IERC20(flashParams.collateralAsset).balanceOf(
            address(this)
        );

        require(collateralReceived > 0, "No collateral received from liquidation");

        // Approve Uniswap to spend collateral
        IERC20(flashParams.collateralAsset).safeApprove(
            address(uniswapRouter),
            collateralReceived
        );

        // Swap with 1% slippage tolerance
        uint256 minAmountOut = (amount * 99) / 100;

        IUniswapV3SwapRouter.ExactInputSingleParams memory swapParams = IUniswapV3SwapRouter
            .ExactInputSingleParams({
                tokenIn: flashParams.collateralAsset,
                tokenOut: flashParams.debtAsset,
                fee: UNISWAP_FEE_TIER,
                recipient: address(this),
                deadline: block.timestamp + 300,
                amountIn: collateralReceived,
                amountOutMinimum: minAmountOut,
                sqrtPriceLimitX96: 0
            });

        uint256 debtAssetReceived = uniswapRouter.exactInputSingle(swapParams);

        // 4. Repay flash loan + premium
        uint256 totalRepay = amount + premium;
        require(debtAssetReceived >= totalRepay, "Insufficient amount to repay flash loan");

        // Approve pool to spend debt asset for repayment
        IERC20(flashParams.debtAsset).safeApprove(address(pool), totalRepay);

        // 5. Emit event for profit tracking
        uint256 profit = debtAssetReceived - totalRepay;
        emit LiquidationExecuted(
            flashParams.borrower,
            flashParams.collateralAsset,
            flashParams.debtAsset,
            flashParams.debtToCover,
            profit
        );

        return keccak256("ERC3156FlashBorrower.onFlashLoan");
    }

    /**
     * @notice Withdraw accumulated profits
     * Only owner can withdraw
     */
    function withdraw() external onlyOwner {
        // Get all tokens held by this contract
        // In practice, only USDC or stable debt asset should accumulate here

        // Withdraw ETH if any
        if (address(this).balance > 0) {
            payable(owner()).transfer(address(this).balance);
        }
    }

    /**
     * @notice Withdraw specific token balance
     * @param token The token to withdraw
     */
    function withdrawToken(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        require(balance > 0, "No balance to withdraw");
        IERC20(token).safeTransfer(owner(), balance);
        emit ProfitWithdrawn(owner(), balance);
    }

    /**
     * @notice Get the address provider
     */
    function ADDRESSES_PROVIDER() external view override returns (IPoolAddressesProvider) {
        return addressProvider;
    }

    /**
     * @notice Get the Aave pool
     */
    function POOL() external view override returns (IPool) {
        return pool;
    }

    /**
     * @notice Accept ETH transfers
     */
    receive() external payable {}
}
