Audit Report

## Title
`SwapAllowlistExtension` Checks Router Identity Instead of End-User, Allowing Any User to Bypass the Swap Allowlist - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — its direct caller. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to work), every unprivileged user can bypass the allowlist by routing through the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool directly: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the router the pool's `msg.sender`: [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. For any router-mediated swap to work on an allowlisted pool, the pool admin must allowlist the router address. Once the router is allowlisted, the check passes for every user who routes through it, regardless of whether that user is individually permitted. There is no secondary check on the actual end user's identity anywhere in the call path.

## Impact Explanation
A pool admin who deploys a `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties). Once the router is allowlisted — which is required for any router-mediated swap to succeed — the allowlist is rendered ineffective. Any unprivileged user can execute swaps on the restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The `recipient` is user-controlled, so output tokens flow directly to the unauthorized user. This constitutes a broken core pool access-control mechanism with direct fund-flow consequences: unauthorized users trade on pools they should be excluded from, potentially draining liquidity or extracting value at oracle-anchored prices reserved for specific counterparties.

## Likelihood Explanation
The trigger requires no special privilege. Any user with tokens can call the public router. The only precondition is that the pool admin has allowlisted the router — which is the natural, expected action for any pool that intends to support router-mediated trading. The vulnerability is therefore reachable in every realistic deployment of a `SwapAllowlistExtension`-gated pool that also supports the periphery router.

## Recommendation
The extension must gate the actual end user, not the intermediary. The cleanest fix is for the router to encode `msg.sender` (the real caller) into `extensionData`, and for the extension to verify both that the caller is the trusted router and that the embedded user address is allowlisted. Alternatively, the router can enforce its own per-user allowlist before calling the pool, and the pool-level extension only allowlists the router — this moves the guard to the periphery but avoids the identity-mismatch problem.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension in beforeSwap slot
  admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so router-mediated swaps work
  admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is NOT allowlisted

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({
      pool: pool,
      recipient: alice,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  Router calls pool.swap(alice, true, X, ...) with msg.sender = router

  Pool calls _beforeSwap(router, alice, ...)
  Extension checks: allowedSwapper[pool][router] == true  ✓
  Swap executes; alice receives output tokens.

Result:
  alice, who is not on the allowlist, successfully swaps on a restricted pool.
  The allowlist invariant is broken.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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
