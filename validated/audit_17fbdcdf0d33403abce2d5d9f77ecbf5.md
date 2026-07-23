Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` populates with `msg.sender`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. If the router is allowlisted (a prerequisite for any allowlisted user to use it), every non-allowlisted user can bypass the curated pool's swap allowlist by routing through the public router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- always the direct caller of pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

From the pool's perspective, `msg.sender` is the router, so `sender` delivered to the extension is the **router address**, not the actual end-user. The allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an inescapable dilemma: if the router is not allowlisted, allowlisted users cannot use the router at all. If the router is allowlisted (the natural operational path), the check collapses to a single bit and every user — allowlisted or not — can swap by routing through the public router. The same structural problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput` paths, all of which call `pool.swap()` with `msg.sender = router`.

## Impact Explanation

A curated pool (KYC-only, institutional-only, or otherwise restricted) that deploys `SwapAllowlistExtension` and allowlists the router loses all per-user access control for every swap entering through `MetricOmmSimpleRouter`. Any unprivileged user can trade in the pool, defeating the curation policy entirely. Because the pool is oracle-driven and the attacker controls swap direction and size, this can be used to extract value from LP positions at prices the pool designers intended to restrict to vetted counterparties. This constitutes a direct admin-boundary break and broken core pool functionality causing potential loss of funds to LPs.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap entrypoint in the periphery. Pool admins who want allowlisted users to be able to use the router must allowlist it — this is the natural operational path. The bypass requires no special privileges, no flash loans, and no unusual token behavior. Any EOA can call `exactInputSingle` on the router to trade in a pool they were never authorized to access.

## Recommendation

The `sender` argument passed to `beforeSwap` must represent the economic actor (the end-user), not the intermediary contract. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should store `msg.sender` in transient storage before calling the pool and expose it through a `currentSwapper()` view function. Extensions that need the real user can call back into the router to retrieve it.

2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should, when `sender` is a known router, query the router's `currentSwapper()` to retrieve and check the actual end-user address.

## Proof of Concept

```
Setup:
  1. Pool admin deploys pool with SwapAllowlistExtension.
  2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  3. Admin calls setAllowedToSwap(pool, router, true)  // required for alice to use the router

Attack (bob is not allowlisted):
  4. bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: bob,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  5. Router calls pool.swap(bob, true, X, ...)
     → pool.msg.sender = router
     → _beforeSwap(sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router] == true  ✓  (no revert)
  6. Swap executes. bob receives output tokens.
     The allowlist check never evaluated bob's address.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
