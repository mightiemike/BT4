Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, allowing any caller to bypass per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to permit legitimate users to trade simultaneously opens the pool to every non-allowlisted user.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` verbatim as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks it against the per-pool allowlist, keyed by `msg.sender` (the pool): [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [3](#0-2) 

The originating user's address (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment purposes and is never forwarded to the pool or extension: [4](#0-3) 

The result: `allowedSwapper[pool][router]` is the only value the extension can check. A pool admin who sets this to `true` (the only way to let any allowlisted user trade via the router) simultaneously grants every non-allowlisted user the same access. There is no configuration that permits allowlisted users through the router while blocking non-allowlisted ones. The same flaw applies to `exactOutputSingle` and `exactInput`/`exactOutput` multi-hop paths. [5](#0-4) 

## Impact Explanation

Any non-allowlisted user can execute swaps against a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point), provided the pool admin has allowlisted the router. The swap settles real token transfers at the oracle-derived bid/ask price, directly against LP reserves. The pool's curation invariant — that only explicitly approved addresses may trade — is silently and completely broken. This constitutes a direct loss-of-policy impact with real fund exposure: LP assets are traded against actors the pool was explicitly designed to exclude.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the canonical user-facing entry point.
- Any pool admin who wants allowlisted users to use the router **must** allowlist the router address, which is the only triggering condition.
- No special privilege, flash loan, or non-standard token is required — a standard `exactInputSingle` call suffices.
- The bypass is silent: the extension emits no event and the pool emits a normal `Swap` event indistinguishable from a legitimate trade.

## Recommendation

Pass the originating user through the swap call chain. The cleanest fix is to add an `originator` field to the swap parameters that the router populates with `msg.sender` before calling `pool.swap()`, and have the pool forward it as a distinct argument to extensions alongside `sender`. `SwapAllowlistExtension.beforeSwap` should then gate on `originator` rather than `sender`. This preserves `sender` for callback settlement while giving extensions the true end-user identity for policy checks.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is KYC'd
  allowedSwapper[pool][bob]   = false  // bob is not KYC'd
  pool admin calls setAllowedToSwap(pool, router, true)
    → intent: let alice use the router
    → effect: allowedSwapper[pool][router] = true

Attack:
  bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
    → router calls pool.swap(recipient=bob, ...)          [MetricOmmSimpleRouter.sol L72-80]
    → pool calls _beforeSwap(sender=router, ...)          [MetricOmmPool.sol L230-240]
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes; bob receives tokens from LP reserves

Result:
  bob, who is explicitly not allowlisted, completes a swap
  against a pool whose entire purpose is to exclude him.
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
