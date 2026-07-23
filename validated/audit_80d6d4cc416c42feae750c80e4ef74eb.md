Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against the per-pool allowlist. When users route through `MetricOmmSimpleRouter`, the pool receives the router as `msg.sender` and forwards it as `sender` to the extension, so the extension checks whether the router is allowlisted rather than whether the end user is allowlisted. If the pool admin allowlists the router to enable allowlisted users to trade through it, the allowlist is completely bypassed for all users including those explicitly excluded.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
_beforeSwap(
  msg.sender,   // ← router address when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged as `sender` to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router — not the end user. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly with no mechanism to forward the original `msg.sender` into the pool's `sender` slot. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension`, allowlists Alice: `setAllowedToSwap(pool, alice, true)`
2. Admin also allowlists the router so Alice can use it: `setAllowedToSwap(pool, router, true)`
3. Bob (not allowlisted) calls `router.exactInputSingle(...)` — the extension checks `allowedSwapper[pool][router] = true` and passes
4. Bob's swap executes despite not being on the allowlist

If the admin does not allowlist the router, Alice's router calls revert with `NotAllowedToSwap` even though she is individually permitted, locking allowlisted users out of the primary swap interface.

## Impact Explanation
This breaks the core invariant of the allowlist extension: the guard is applied to the wrong actor. In Scenario A (router allowlisted), the curation policy is completely nullified for all router-mediated swaps — any user can trade regardless of allowlist status. In Scenario B (router not allowlisted), allowlisted users cannot use the primary swap interface. Both scenarios represent broken core pool functionality with direct fund-access impact: unauthorized users gain swap access to pools intended to be curated, or authorized users are locked out.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool deploying `SwapAllowlistExtension` for curation will encounter this issue the moment a user or admin attempts router access. No special privileges are required — any public user can call the router. The trigger is the standard, expected usage pattern.

## Recommendation
The `sender` argument passed to `beforeSwap` must represent the economic actor, not the immediate caller. Two viable fixes:

1. **Pass the original user through the router**: Encode the original `msg.sender` in `extensionData` and have the extension decode and check that value, requiring a protocol-level convention for periphery-forwarded identity.
2. **Gate on `recipient` instead of `sender`**: For swap allowlisting, the economically relevant actor is the one receiving output tokens. The pool already passes `recipient` as the second argument to `beforeSwap`. Gating on `recipient` is manipulation-resistant because the user controls who receives funds.
3. **Move allowlist enforcement into the router**: Keep the pool-level extension as a router-only gate and enforce user-level allowlisting inside a trusted, non-upgradeable router.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension, Alice allowlisted, Bob not allowlisted
swapExtension.setAllowedToSwap(pool, alice, true);
// Admin allowlists the router so Alice can use it:
swapExtension.setAllowedToSwap(pool, address(router), true);

// Bob (not allowlisted) bypasses the allowlist via the router:
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: bob,
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Succeeds: extension checks allowedSwapper[pool][router] = true
// Bob's swap executes despite not being on the allowlist.
```

The existing test `test_allowedSwapSucceeds` in `FullMetricExtensionTest` allowlists `callers[0]` (a `TestCaller` that calls the pool directly), never exercising the router bypass path.