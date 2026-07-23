Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender = address(router)` is what the extension checks — not the end user. Any pool admin who allowlists the router (the only way to let allowlisted users use the router) inadvertently grants every non-allowlisted user the ability to bypass the curation gate by routing through the public router.

## Finding Description

**Call chain binding:**

`MetricOmmPool.swap` passes `msg.sender` (the immediate caller of the pool) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct key for the per-pool mapping) and `sender` is whoever called `pool.swap()`. When the user goes through the router, `sender = address(router)`, not the user's address.

**Router call path:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern applies to `exactInput` (L92-125), `exactOutputSingle` (L130-147), and `exactOutput` (L154-188). In every router-mediated path the pool sees `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` — a single bit that covers every user who ever routes through that contract. [5](#0-4) 

**The dilemma for the pool admin:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router — broken UX |
| Allowlist the router | Every non-allowlisted user can bypass the gate — broken security |

There is no configuration that simultaneously lets allowlisted users use the router and blocks non-allowlisted users.

## Impact Explanation

A pool admin who deploys a curated pool (KYC-gated, jurisdiction-restricted, or partner-only) with `SwapAllowlistExtension` and allowlists the router to support normal user flows inadvertently opens the gate to all users. The allowlist invariant — "only approved addresses may swap" — is completely broken for router-mediated swaps. This constitutes a broken core pool access-control mechanism and an admin-boundary bypass reachable by any unprivileged user, satisfying the contest's allowed impact gate for admin-boundary breaks and broken core pool functionality.

## Likelihood Explanation

Allowlisting the router is the natural and expected configuration for any curated pool that wants to support the protocol's own periphery. The bypass requires no special knowledge, no privileged access, and no unusual token behavior. Any user can call `exactInputSingle` on the public router. The only precondition is that the pool admin has made the reasonable configuration choice of allowlisting the router, which is the expected operational state.

## Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary contract. Two viable approaches:

1. **Router-forwarded identity**: The router encodes `msg.sender` (the real user) into `extensionData` and the extension decodes and checks it. This requires a coordinated change to both the router and the extension.
2. **Separate sender vs. swapper parameters**: The pool could expose a dedicated `swapper` field (distinct from `msg.sender`) that the router populates with the real user address, and the extension checks that field.

Until one of these is implemented, `SwapAllowlistExtension` should document that it cannot enforce per-user restrictions for router-mediated swaps, and curated pools should not allowlist the router.

## Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, alice, true)       // KYC'd user
  pool admin: setAllowedToSwap(pool, router, true)      // to let alice use the router

Attack:
  bob (non-KYC'd) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

Execution trace:
  router.exactInputSingle()          msg.sender = bob
    pool.swap(recipient=bob, ...)    msg.sender = router
      _beforeSwap(sender=router, ...)
        SwapAllowlistExtension.beforeSwap(sender=router, ...)
          allowedSwapper[pool][router] == true  ✓  (no revert)
      swap executes for bob

Bob's swap succeeds. The allowlist checked the router, not Bob.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
