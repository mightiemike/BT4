Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Enabling Full Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always the immediate `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is used, that value is the router contract address, not the end user. A pool admin who allowlists the router to support standard periphery swaps inadvertently opens the allowlist to every address on the network that can call the router.

## Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the hook.**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

**Step 2 — The extension checks that `sender` value against the allowlist.**

`SwapAllowlistExtension.beforeSwap` uses `msg.sender` (the pool) as the mapping key and `sender` (the pool's caller) as the identity being gated: [2](#0-1) 

**Step 3 — The router calls `pool.swap()` directly, making itself the pool's `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` stores the real user in transient storage for the payment callback but never forwards that identity to the pool: [3](#0-2) 

The pool receives `msg.sender = router`, passes `router` as `sender` to `_beforeSwap`, and the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Step 4 — The same mismatch applies to all router entry points.**

`exactInput`, `exactOutputSingle`, and `exactOutput` all call `pool.swap()` from the router's address: [4](#0-3) [5](#0-4) [6](#0-5) 

**The structural trap.** A pool admin who wants allowlisted users to swap through the router must call `setAllowedToSwap(pool, router, true)`: [7](#0-6) 

The moment that entry is set, `allowedSwapper[pool][router]` is `true` for every call arriving through the router, regardless of who the real user is. The allowlist is silently open to the entire public.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd, whitelisted, or otherwise curated addresses is fully bypassed. Any unprivileged user can call any router entry point and trade on the restricted pool as if they were allowlisted. The pool settles real token transfers — input tokens received from and output tokens sent to — actors the pool admin explicitly intended to exclude. This constitutes broken core pool functionality and a direct curation failure with loss of principal to unauthorized counterparties.

## Likelihood Explanation
The bypass requires only that the pool admin allowlists the router, which is the natural and expected configuration for any pool that wants to support the standard periphery swap path. A pool admin who does not allowlist the router forces all allowlisted users to call `pool.swap()` directly and implement `metricOmmSwapCallback` themselves, making the router entirely unusable for that pool. The bypass is therefore triggered by the ordinary, documented deployment pattern, not by an exotic misconfiguration. Any unprivileged user can exploit it with a single router call.

## Recommendation
The `sender` forwarded to extension hooks must represent the economic actor, not the immediate `msg.sender` of `pool.swap()`. Two viable approaches:

1. **Explicit originator parameter**: Add an `originator` field to the `swap()` call signature. The router passes `msg.sender` (the real user) as `originator`; direct callers pass `address(0)` or themselves. The pool forwards `originator` (falling back to `msg.sender` when zero) to `_beforeSwap`.

2. **Extension-data originator**: Require the router to prepend the real user's address into `extensionData` for allowlist-aware pools, and have `SwapAllowlistExtension` decode and verify it (with a pool-level flag enabling this mode).

Either approach must be applied consistently across `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Proof of Concept
```solidity
// Setup
pool = factory.createPool(..., extensionOrder: beforeSwap → SwapAllowlistExtension);
admin.setAllowedToSwap(pool, router, true);   // admin enables router so allowlisted users can swap
// alice is NOT in the allowlist

// Attack — alice calls the router directly
router.exactInputSingle(ExactInputSingleParams({
    pool:        pool,
    recipient:   alice,
    zeroForOne:  true,
    amountIn:    1e18,
    ...
}));
// pool.swap() is called with msg.sender = router
// _beforeSwap(router, ...) is dispatched
// SwapAllowlistExtension checks allowedSwapper[pool][router] → true
// Swap executes; alice receives output tokens despite never being allowlisted
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-181)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```
