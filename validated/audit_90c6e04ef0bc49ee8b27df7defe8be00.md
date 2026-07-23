Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Complete Allowlist Bypass — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` resolves to the router address, not the originating user. A pool admin who allowlists the router — required for any router-mediated swap to succeed — inadvertently grants swap access to every caller of the router, completely defeating the per-user allowlist.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap()
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that value against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router is the direct caller of `pool.swap()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`. The same substitution occurs for `exactInput` (all hops, L104-112), `exactOutputSingle` (L136-137), `exactOutput` (L165-181), and the recursive `_exactOutputIterateCallback` hops (L220-228). For a curated pool to support router-mediated swaps, the pool admin must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, the check passes for **every** caller of the router, regardless of whether that caller is individually allowlisted. Existing guards are insufficient: there is no mechanism in the extension or the pool to recover the original `msg.sender` from the router call stack.

## Impact Explanation
A curated pool (KYC-only, institutional-only, or whitelist-gated) relies on `SwapAllowlistExtension` to enforce that only approved addresses can trade. Once the router is allowlisted, any unprivileged user can call `router.exactInputSingle()` and execute swaps on the restricted pool. This is a direct loss of the curation invariant and allows unauthorized users to extract value from LP positions that were priced for a controlled counterparty set. This meets the admin-boundary break criterion: an unprivileged path bypasses the pool admin's access-control configuration.

## Likelihood Explanation
Pool admins who deploy a curated pool and also want their allowlisted users to access the standard periphery router will naturally allowlist the router — there is no other way to make router-mediated swaps work. The bypass is therefore reachable on any curated pool that supports the periphery router, which is the expected production configuration. No special attacker capability is required beyond calling the public router entry points.

## Recommendation
The extension must check the identity of the economic actor, not the intermediary. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes the original `msg.sender` in `extensionData`; the extension decodes and verifies it. This requires a trust model between router and extension (e.g., the extension only accepts this encoding from a known router address).
2. **Require direct pool interaction for allowlisted pools**: Document and enforce at the factory level that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call `pool.swap()` directly.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls:
     extension.setAllowedToSwap(pool, alice, true);   // allowlist alice
     extension.setAllowedToSwap(pool, router, true);  // required for router to work
3. bob (not allowlisted) calls:
     router.exactInputSingle({pool: pool, recipient: bob, ...})
4. Router calls pool.swap(bob, ...) with msg.sender = router.
5. Extension evaluates: allowedSwapper[pool][router] == true → passes.
6. bob's swap executes on the curated pool.
   Direct call by bob would revert: allowedSwapper[pool][bob] == false.
```

The root cause is confirmed at `SwapAllowlistExtension.beforeSwap` line 37, where `sender` is bound to the router address rather than the originating user whenever the periphery router is the direct caller of `pool.swap()`.