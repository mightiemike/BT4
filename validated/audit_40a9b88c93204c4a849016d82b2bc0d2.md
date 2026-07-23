Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` checks the `sender` parameter, which is always `msg.sender` to `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to every user on the network, completely defeating the allowlist.

## Finding Description
The call path is confirmed by the production code:

**Step 1:** `MetricOmmPool.swap()` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [1](#0-0) 

**Step 2:** `ExtensionCalling._beforeSwap()` forwards `sender` unchanged to every configured extension: [2](#0-1) 

**Step 3:** `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — the actual end user is never visible: [3](#0-2) 

**Step 4:** `MetricOmmPool.swap()` calls `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...)`, meaning only a contract implementing the callback interface (i.e., the router, not the end user) can be `msg.sender` to the pool during a router-mediated swap: [4](#0-3) 

**Step 5:** `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly with `msg.sender` as the payer but the router as the pool's caller — the end user's address is stored only in transient callback context, never passed to the pool as `sender`: [5](#0-4) 

**Contrast with `DepositAllowlistExtension`**, which correctly gates on `owner` (the economic LP recipient), not `sender` (the direct pool caller): [6](#0-5) 

The structural inconsistency is clear: the deposit guard correctly identifies the economic actor; the swap guard does not.

## Impact Explanation
A pool admin deploying a curated pool (e.g., KYC-gated, institutional-only, or whitelist-only) with `SwapAllowlistExtension` must call `setAllowedToSwap(pool, router, true)` to allow their allowlisted users to use the standard `MetricOmmSimpleRouter`. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and every user on the network can swap on the curated pool by routing through `MetricOmmSimpleRouter`, regardless of individual allowlist status. The core pool access-control mechanism — restricting swaps to specific addresses — is rendered completely ineffective for all router-mediated swaps. This constitutes a broken core pool functionality causing unauthorized access to pools intended to be restricted.

## Likelihood Explanation
The likelihood is medium-high. The scenario requires a pool admin to: (1) deploy a pool with `SwapAllowlistExtension` to restrict swaps, and (2) allowlist the router to support router-mediated swaps for their users. This is the natural and expected configuration. The trap is invisible: the admin believes they are allowlisting a trusted intermediary, but they are disabling the allowlist entirely for all router-mediated swaps. Any unprivileged user (bob) can exploit this immediately and repeatably once the router is allowlisted, with no special capability required beyond calling `MetricOmmSimpleRouter.exactInputSingle()`.

## Recommendation
The extension must check the actual economic actor, not the intermediary. Two viable approaches:

1. **Router passes user identity in `extensionData`**: The router encodes `msg.sender` (the actual user) into `extensionData`. `SwapAllowlistExtension.beforeSwap()` decodes and checks this value when `extensionData` is non-empty, falling back to `sender` for direct pool calls. This requires a trusted encoding convention.

2. **Pool exposes an explicit `swapper` parameter**: Modify `MetricOmmPool.swap()` to accept an explicit `swapper` address (defaulting to `msg.sender` for direct calls), pass it through `_beforeSwap`, and have the extension check that address. The router would pass the actual user's address.

The simplest fix consistent with the existing `DepositAllowlistExtension` pattern is approach (1): the router encodes `abi.encode(msg.sender)` as the first word of `extensionData`, and `SwapAllowlistExtension.beforeSwap()` decodes and checks it when `extensionData.length >= 32`.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - setAllowedToSwap(pool, alice, true)   // alice is the intended gated user
  - setAllowedToSwap(pool, router, true)  // admin enables router support

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  - Pool receives swap() with msg.sender = router
  - _beforeSwap(sender=router, ...) is called
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Bob's swap executes successfully despite not being allowlisted

Result:
  - Bob bypasses the curated allowlist entirely
  - The allowlist is rendered ineffective for all router-mediated swaps
  - Any user can repeat this attack indefinitely
```

Foundry test plan: deploy `SwapAllowlistExtension`, configure a pool with it, allowlist only `alice` and the `router`, then call `exactInputSingle` from `bob`'s address and assert the swap succeeds (demonstrating the bypass).

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

**File:** metric-core/contracts/MetricOmmPool.sol (L258-263)
```text
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
