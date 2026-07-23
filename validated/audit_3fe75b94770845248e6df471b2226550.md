Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool binds to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. If the router is allowlisted (a natural operational setup for a public periphery contract), any non-allowlisted user can bypass the curated pool's swap gate by routing through the router.

## Finding Description

**Call path:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`).
2. The router calls `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender` inside the pool is the **router address**.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)` where `msg.sender` = router.
4. `ExtensionCalling._beforeSwap` encodes and dispatches to `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool address, `sender` = router address. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

**Root cause:** The pool correctly passes `msg.sender` as `sender` to the hook, but `msg.sender` at the pool boundary is the router, not the end user. The extension has no access to the original initiator. There is no mechanism in the router to forward the real user's identity to the extension.

**Why existing guards fail:** The `onlyPool` modifier on `beforeSwap` only ensures the call originates from a registered pool — it does not validate which human actor initiated the transaction. The allowlist mapping is keyed by `sender` (the pool's `msg.sender`), which is structurally the router for all router-mediated swaps.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd or approved addresses loses that guarantee entirely for any user who routes through `MetricOmmSimpleRouter`. If the router is allowlisted (the expected operational state for a public periphery), the allowlist is effectively nullified for all router-mediated swaps. This is a direct policy bypass on curated pools, constituting a broken core pool functionality and admin-boundary break reachable by any unprivileged trader.

## Likelihood Explanation
The `MetricOmmSimpleRouter` is the canonical public swap entrypoint. Any pool admin who allowlists the router (to permit normal trading) simultaneously opens the gate to all users. The attacker needs only to call the router's standard swap functions — no special permissions, no flash loans, no complex setup. The bypass is repeatable on every swap.

## Recommendation
The extension must gate on the economically relevant actor — the end user — not the intermediary. Options:

1. **Pass the original initiator through the router:** Have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a convention between router and extension.
2. **Check `recipient` instead of `sender`:** If the pool's design intent is to gate who receives output, `recipient` is already passed to the hook. However, `recipient` can also be set arbitrarily.
3. **Preferred:** Redesign the hook interface to include a dedicated `originator` field populated by the pool from a trusted source (e.g., transient storage set by the router before calling the pool), so the extension always sees the true initiating EOA regardless of intermediary.

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Router is also allowlisted (standard operational setup).
allowedSwapper[pool][router] = true;
allowedSwapper[pool][allowedUser] = true;

// Attack: non-allowlisted user calls router
// pool.beforeSwap sees sender = router → passes allowlist check
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp,
    priceLimitX64: 0,
    extensionData: ""
})); // succeeds — attacker bypasses allowlist
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only the router and one approved address, then call `exactInputSingle` from an unapproved EOA and assert the swap succeeds (demonstrating the bypass).