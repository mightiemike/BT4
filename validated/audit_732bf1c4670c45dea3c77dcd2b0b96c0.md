Audit Report

## Title
SwapAllowlistExtension Allowlist Keyed on Router Address Instead of Originating User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps using `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the argument forwarded from `MetricOmmPool.swap()` as `msg.sender` of that call. When `MetricOmmSimpleRouter` intermediates a swap, the pool's `msg.sender` is the router address — not the originating user — so the allowlist key is non-unique across all users of the router. This produces two broken configurations: allowlisting the router grants unrestricted access to all users, and allowlisting individual users blocks them from using the standard router.

## Finding Description

In `MetricOmmPool.swap()`, `_beforeSwap` is called with `msg.sender` as the `sender` argument: [1](#0-0) 

In `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly, making the pool's `msg.sender` the router contract address, not the originating EOA: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the router address for every user who routes through `MetricOmmSimpleRouter`. The lookup `allowedSwapper[pool][router]` is identical for all callers regardless of their identity.

**Config A — Router allowlisted:** Any non-allowlisted user calls `exactInputSingle()`. The pool sees `sender = router`. `allowedSwapper[pool][router] == true`. Swap proceeds. Allowlist is fully bypassed for all users.

**Config B — Individual users allowlisted:** An allowlisted user calls `exactInputSingle()`. The pool sees `sender = router`. `allowedSwapper[pool][router] == false`. Swap reverts with `NotAllowedToSwap`. Legitimate users cannot use the standard periphery router.

The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in the router, all of which call `pool.swap()` directly. [4](#0-3) 

## Impact Explanation

**Config A** is a broken access-control invariant: any unprivileged user bypasses a swap allowlist on a restricted pool, enabling unauthorized swaps, potential liquidity drain at oracle prices, and front-running of restricted LPs — direct loss of user principal and protocol fees. **Config B** is broken core pool functionality: allowlisted users cannot execute swaps through the standard documented entry point, effectively freezing their ability to interact with the pool. Both impacts meet Sherlock High thresholds.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, public, documented swap entry point. Any user can call it without privilege. The bug is triggered on every router-mediated swap to any pool with `SwapAllowlistExtension` configured. No admin action, malicious setup, or non-standard token is required. Likelihood is High.

## Recommendation

The allowlist must gate the originating user, not the intermediary router. The preferred fix is to have the router encode `msg.sender` into `extensionData` before forwarding to the pool, and have `SwapAllowlistExtension.beforeSwap()` decode and verify that value instead of (or in addition to) `sender`. The extension should additionally verify that the call originates from a trusted router when relying on `extensionData`. Alternatively, require allowlisted users to call `pool.swap()` directly rather than through the router, or deploy a router variant that enforces its own allowlist before calling the pool.

## Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension (beforeSwap order set)
  allowlist.setAllowedToSwap(pool, router_address, true)   // Config A: router allowlisted
  // alice is NOT individually listed

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient=alice, ...)          // msg.sender to pool = router
    → pool calls _beforeSwap(sender=router_address, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router_address] == true
    → hook returns selector (no revert)
    → swap executes for alice despite alice not being allowlisted

Result:
  alice successfully swaps in a pool she is not authorized to access.
  The allowlist provides zero protection for any user routing through the router.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
