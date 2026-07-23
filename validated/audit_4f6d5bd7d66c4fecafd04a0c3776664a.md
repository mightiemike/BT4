Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the user. A pool admin who allowlists the router to enable router-mediated swaps for curated users inadvertently grants swap access to every caller of the router, completely defeating the access-control gate.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing its own `msg.sender` as the `sender` argument: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded above: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly with no mechanism to forward the original `msg.sender` of the router call into the pool's `sender` slot: [3](#0-2) 

The `extensionData` field is passed through but `SwapAllowlistExtension.beforeSwap` ignores it entirely (the last `bytes calldata` parameter is unnamed and unused in the check). There is no `originator` field, no trusted-forwarder pattern, and no other mechanism to recover the true user identity inside the extension. The only identity available to the pool is `msg.sender`, which is the router when the router is the caller.

## Impact Explanation
A pool admin who deploys `SwapAllowlistExtension` to restrict trading to specific counterparties and wants those counterparties to use the canonical router must call `setAllowedToSwap(pool, router, true)`. Once set, `allowedSwapper[pool][router] == true`, and the extension's check passes for **every** caller of the router — including users who were never individually allowlisted. The entire curated-pool access-control model collapses. LP funds in the pool are exposed to unauthorized counterparties, which is the exact harm the allowlist was deployed to prevent. This constitutes a broken core pool functionality (access-controlled swap) and an admin-boundary break where the pool admin's intended restriction is bypassed by an unprivileged path. [4](#0-3) 

## Likelihood Explanation
Any production pool that (a) deploys `SwapAllowlistExtension` to restrict trading and (b) wants allowlisted users to use the standard router will trigger this path. The router is the canonical entry point for EOA swaps; allowlisting it is the natural, expected configuration step. No special attacker capability is required beyond calling `router.exactInputSingle` with a valid pool address. The only alternative — not allowlisting the router — means individually allowlisted users cannot use the router at all, breaking the primary swap UX.

## Recommendation
The `sender` identity that the allowlist gates must be the original user, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Pass the original user through the router**: Add an optional `originator` field to the pool's `swap()` call (or encode it in `extensionData`) so the router can forward `msg.sender`. The extension then checks `originator` when it is set, falling back to `sender` for direct calls.

2. **Gate on `recipient` or require direct calls**: Restrict the allowlist check to the `recipient` argument (which the router does forward from `params.recipient`) if the pool's policy is that the recipient must be allowlisted, or document that allowlisted pools must be called directly and enforce this in the extension by reverting when `sender` is a known router.

## Proof of Concept
```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended gated user
  admin calls setAllowedToSwap(pool, router, true)      // to let alice use the router

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

Trace:
  router.exactInputSingle()
    → pool.swap(bob, ...)          // msg.sender of pool.swap = router
    → _beforeSwap(router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓  (passes)
    → swap executes for bob

Result:
  bob, who was never individually allowlisted, successfully swaps on the curated pool.
  The allowlist is completely bypassed for any user who routes through the router.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
