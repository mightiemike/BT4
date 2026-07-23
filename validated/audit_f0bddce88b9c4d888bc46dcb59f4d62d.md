Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. If the pool admin allowlists the router to support legitimate router-mediated swaps, any unprivileged user can bypass the allowlist entirely by routing through the public router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that `sender` value unchanged to each configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router the `msg.sender` to the pool. It does not encode the original `msg.sender` into `extensionData`: [4](#0-3) 

The result is that the extension receives `sender = router_address`, not the actual user. The allowlist check becomes `allowedSwapper[pool][router]`. This creates an irresolvable dilemma: if the router is not allowlisted, all router-mediated swaps revert even for legitimate users; if the router is allowlisted (the natural action for a pool admin who wants to support the periphery), every caller passes the check regardless of whether they are individually authorized.

## Impact Explanation
Any user can bypass the swap allowlist on a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, or `exactOutputSingle`. The allowlist invariant — that only approved addresses may trade on a curated pool — is broken. Unauthorized users gain access to pool liquidity, which can cause adverse selection for LPs (e.g., if the allowlist was intended to exclude informed traders or enforce KYC), constituting broken core pool functionality and a direct policy bypass with fund-impacting consequences for LP positions.

## Likelihood Explanation
Medium. The bypass requires the pool admin to allowlist the router address. However, this is the natural and expected action for any pool admin who wants their allowlisted users to be able to use the supported periphery. `MetricOmmSimpleRouter` is the primary user-facing swap interface; a pool admin who deploys a curated pool and then allowlists the router to support it inadvertently opens the allowlist to all users.

## Recommendation
The `SwapAllowlistExtension` must gate the economically relevant actor — the end user — not the intermediary router. Two approaches:

1. **Router forwards the original caller**: `MetricOmmSimpleRouter` encodes `msg.sender` into `extensionData` for each hop, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of `sender` when the caller is a known router.
2. **Extension checks both**: If `sender` is a known router, decode the real user from `extensionData`; otherwise check `sender` directly.

Either approach ensures the allowlist gates the same actor the pool admin intended to restrict, regardless of which supported periphery path reaches the pool.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, alice, true)
  pool admin: setAllowedToSwap(pool, router, true)   ← natural step to support periphery

Attack:
  Bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)          [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ passes
        → swap executes, Bob receives output tokens

Result:
  Bob swaps successfully on a pool he is not allowlisted for.
  allowedSwapper[pool][bob] == false, but the check was never applied.
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
