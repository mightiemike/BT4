Audit Report

## Title
Swap Allowlist Actor-Binding Mismatch: Router Address Checked Instead of Actual Swapper - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the original user. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If a pool admin allowlists the router address to enable router-mediated swaps for their curated users, every unprivileged user can bypass the per-user swap allowlist by routing through the public router.

## Finding Description

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()` — `msg.sender` = user.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — `msg.sender` at the pool = **router address**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` passing the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes `sender` (= router) and dispatches to `SwapAllowlistExtension.beforeSwap`.
5. `SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool, `sender` = **router address**. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Root cause:** `MetricOmmPool.swap()` has no explicit `sender` parameter; it uses `msg.sender` internally and forwards it to the extension. The router stores the original user's address only in transient callback context (for payment), but never passes it to the pool or extension. There is no path for the extension to observe the original user.

**Bypass path:**
- Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists specific users for direct swaps.
- Pool admin also allowlists the router address (`setAllowedToSwap(pool, router, true)`) — a reasonable action to enable router-mediated swaps for those users.
- Result: `allowedSwapper[pool][router] = true`, so `beforeSwap` passes for **any** caller who routes through the router, regardless of whether they are individually allowlisted.
- Any non-allowlisted user calls `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router targeting the curated pool and swaps successfully.

**Existing guards are insufficient:** The `onlyPool` modifier on the extension only verifies the caller is the pool; it does not verify which user initiated the swap. The pool's `whenNotPaused` and reentrancy guards are orthogonal. No mechanism in the current interface propagates the original EOA/contract address through the router → pool → extension path.

## Impact Explanation
A curated pool's swap allowlist is completely bypassed for any user who routes through the public `MetricOmmSimpleRouter`. The pool admin's intended access-control policy (only specific addresses may trade) fails open. Disallowed users can execute swaps, draining LP value or trading against oracle prices in a pool designed to be restricted. This is a direct policy bypass on a core security feature, constituting broken core pool functionality and an admin-boundary break reachable by any unprivileged caller.

## Likelihood Explanation
The bypass requires the router to be allowlisted, which a pool admin would do to support router-mediated swaps for their curated users — a natural and expected operational step. Once the router is allowlisted, the bypass is unconditional and repeatable by any address. No special privileges, flash loans, or oracle manipulation are required. The attacker only needs to call the public router.

## Recommendation
Pass the original initiating user through the swap interface so the extension can gate the correct actor. Options:
1. Add an explicit `originator` parameter to `IMetricOmmPoolActions.swap` that the router populates with `msg.sender` before calling the pool, and forward it to extensions as the gated identity.
2. Alternatively, have `SwapAllowlistExtension.beforeSwap` read the original sender from a trusted source (e.g., router-set transient storage) rather than relying on the `sender` argument, which reflects the immediate pool caller.
3. Document clearly that the allowlist gates the direct pool caller, and require pool admins to use a dedicated allowlist-aware router wrapper that enforces per-user checks before calling the pool.

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; alice is allowlisted, bob is not
swapExtension.setAllowedToSwap(address(pool), alice, true);
// Admin also allowlists the router to enable router-mediated swaps for alice
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Bob (not individually allowlisted) calls the router
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bob,
    tokenIn: token0,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Bob's swap succeeds — allowlist bypassed
```

The pool's `swap()` sees `msg.sender = router`, the extension checks `allowedSwapper[pool][router] = true`, and the swap proceeds for Bob despite him not being individually allowlisted.