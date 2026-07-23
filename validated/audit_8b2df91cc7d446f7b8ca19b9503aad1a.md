Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-Pool Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap()` passes `msg.sender` (the immediate caller) as `sender` to `_beforeSwap`, which forwards it unchanged to `SwapAllowlistExtension.beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` on the pool, so the extension checks whether the **router** is allowlisted rather than the actual user. A pool admin who allowlists the router to enable normal router usage simultaneously opens the allowlist to every user on the network.

## Finding Description
`MetricOmmPool.swap()` captures `msg.sender` and passes it as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension via `_callExtensionsInOrder`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the address that called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other swap entry points) calls `pool.swap()` directly, making the router `msg.sender` inside the pool: [4](#0-3) 

The result is that the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. The pool admin faces an inescapable dilemma: if the router is not allowlisted, all router-mediated swaps revert even for allowlisted users; if the router is allowlisted, every user on the network can swap through the router and bypass the allowlist entirely.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on the `owner` parameter (the position owner), not `sender`, so the deposit allowlist is not affected by who calls `addLiquidity`: [5](#0-4) 

This asymmetry confirms the swap path has a wrong-actor binding bug.

## Impact Explanation
A pool admin who deploys a curated pool (e.g., for KYC'd counterparties or a controlled market-making arrangement) and attaches `SwapAllowlistExtension` receives no protection once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter` and execute swaps against the pool's LP liquidity. LP funds are exposed to unauthorized counterparties that the allowlist was designed to exclude. This constitutes a direct loss-of-principal risk for LPs on curated pools, meeting the threshold for a High severity finding under broken core pool functionality causing loss of funds.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the documented, supported periphery entrypoint for swaps. Any user who reads the protocol docs will use it. The bypass requires no special knowledge, no privileged role, and no unusual token behavior — only a standard router call. The pool admin must allowlist the router to make the pool usable through the router at all, which automatically opens the bypass. The attack is repeatable by any address at any time.

## Recommendation
The pool should pass the originating user as `sender`, not `msg.sender`. Two concrete approaches:

1. **Gate on `recipient` instead of `sender`**: For swap allowlists, check the `recipient` argument (the address receiving output tokens) rather than `sender`. This is already the second argument to `beforeSwap` and correctly identifies the economic beneficiary regardless of routing intermediaries.

2. **Router forwards real sender via `extensionData`**: Establish a protocol-level convention where the router encodes `msg.sender` into `extensionData`, and the extension reads and verifies it. This requires coordinated changes to the router and extension.

## Proof of Concept
```solidity
// Pool deployed with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// `attacker` is NOT allowlisted.

// Direct swap by attacker — correctly reverts:
vm.prank(attacker);
pool.swap(attacker, false, 1000, type(uint128).max, "", "");
// → reverts NotAllowedToSwap ✓

// Pool admin must allowlist the router to let allowedUser use it:
vm.prank(poolAdmin);
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

// Now attacker routes through the router — passes the allowlist check:
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    tokenIn: token0,
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// → succeeds; extension checks allowedSwapper[pool][router] == true
// → attacker swaps on a pool they should be barred from ✗
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
