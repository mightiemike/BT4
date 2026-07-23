Audit Report

## Title
SwapAllowlistExtension Checks Immediate Caller (Router) Instead of Originating User, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` populates with its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension sees the router address rather than the real user. Any pool admin who allowlists the router to enable normal UX for their curated users simultaneously opens the pool to every user on the network.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← immediate caller, not originating user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that value against its allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call. The original user's identity is stored only in transient storage as the payer for the callback — it is never forwarded to the pool or extension:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData   // ← no originating user identity here
    );
```

The same pattern applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181). In all cases the router is `msg.sender` to the pool.

**Consequence:** There is no configuration that simultaneously (a) lets allowlisted users use the router and (b) blocks non-allowlisted users from using the router. If the admin allowlists the router so that `alice` can swap via the standard UX, `bob` (never allowlisted) can call `router.exactInputSingle({pool: pool, ...})` and the check `allowedSwapper[pool][router] == true` passes.

Existing guards are insufficient: `allowAllSwappers` is a separate escape hatch, and `allowedSwapper` maps pool→address→bool with no way to distinguish the router acting on behalf of an authorized user from the router acting on behalf of an unauthorized one.

## Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to known counterparties. To let those counterparties use the canonical router, the admin must call `setAllowedToSwap(pool, router, true)`. From that moment, any unprivileged user can call `router.exactInputSingle` targeting the pool and bypass the allowlist entirely. Unauthorized swaps drain LP-owned token reserves at oracle prices, causing direct loss of LP principal. This satisfies "admin-boundary break bypassed by an unprivileged path" and "broken core pool functionality causing loss of funds."

## Likelihood Explanation

The router is the canonical periphery entry point. Any pool admin who wants allowlisted users to have standard UX must allowlist the router — the exact configuration that opens the bypass. The attacker requires no special privilege: a single call to `exactInputSingle` with the target pool address suffices. The trigger is fully permissionless once the router is allowlisted, and the scenario is the expected production deployment pattern.

## Recommendation

Pass the originating user through the swap path rather than the immediate caller. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`**: append the original `msg.sender` to `extensionData` (or a dedicated transient slot) before calling `pool.swap`, so extensions can read the real initiator.
2. **In `SwapAllowlistExtension.beforeSwap`**: when `extensionData` contains an originating-user field, decode and check that address instead of `sender`. Fall back to `sender` for direct pool calls where no router field is present.

The simplest safe fix is for the router to ABI-encode the original `msg.sender` into `extensionData` and for `SwapAllowlistExtension` to decode and verify that value when present.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)
  pool admin calls setAllowedToSwap(pool, router, true)  ← required for alice to use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

Execution trace:
  router.exactInputSingle()           msg.sender = bob
    pool.swap(recipient=bob, ...)     msg.sender = router
      _beforeSwap(sender=router, ...)
        SwapAllowlistExtension.beforeSwap(sender=router)
          allowedSwapper[pool][router] == true  ← passes
      swap executes, LP funds transferred to bob

Result:
  bob swaps on a pool he was never authorized to access.
  LP principal is reduced by the swap output sent to bob.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
