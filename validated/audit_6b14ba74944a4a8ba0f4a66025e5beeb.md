Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Per-Pool Swap Allowlist — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router's address rather than the actual user. A pool admin who allowlists the router to support router-based swaps for approved users inadvertently opens the gate to every user who calls through the router, defeating the per-user allowlist entirely.

## Finding Description
The call chain producing the wrong identity is confirmed in production code:

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)` on a pool guarded by `SwapAllowlistExtension`.
2. The router calls `IMetricOmmPoolActions(params.pool).swap(...)` — at this point `msg.sender` inside the pool is the **router address**, not the user. The router does not forward the original caller's identity.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, forwarding the router address as `sender`.
4. `ExtensionCalling._beforeSwap` ABI-encodes `sender = router` and dispatches to `SwapAllowlistExtension.beforeSwap`.
5. Inside the extension, `msg.sender` = pool (verified by `onlyPool`), and `sender` = router.
6. The guard evaluates `allowedSwapper[pool][router]` — it never sees the actual user.

The root cause is in `SwapAllowlistExtension.beforeSwap` at line 37, which checks `allowedSwapper[msg.sender][sender]` where `sender` is the pool's `msg.sender` (the router), not the end user. The router at line 72-80 of `MetricOmmSimpleRouter.sol` calls `pool.swap(...)` directly without encoding the original `msg.sender` into the swap call or `extensionData`. The pool at line 230-240 of `MetricOmmPool.sol` passes `msg.sender` (the router) as `sender` to `_beforeSwap`. No existing guard corrects this identity substitution.

## Impact Explanation
A pool admin who deploys a `SwapAllowlistExtension`-guarded pool and allowlists the router (the natural step to support router-based swaps for approved users) makes the allowlist vacuous for all router-mediated paths. Every user who calls through `MetricOmmSimpleRouter` is implicitly approved because the check resolves to `allowedSwapper[pool][router] == true`. Non-allowlisted users can execute real swaps against a pool intended to be restricted (e.g., KYC-gated, institution-only, or compliance-restricted). The pool's core access-control invariant — that only explicitly approved addresses may swap — is broken for all router-mediated paths, constituting broken core pool functionality and an admin-boundary break reachable by any unprivileged user.

## Likelihood Explanation
Any pool that (a) configures `SwapAllowlistExtension` in its `beforeSwap` order and (b) allowlists the router so that approved users can use the standard periphery is vulnerable. This is the expected operational pattern: the router is the primary user-facing entry point in the periphery layer. The pool admin has no in-protocol mechanism to allowlist the router for approved users while simultaneously blocking unapproved users who also call through the router. The attack requires no special privileges — any user with token approval on the router can exploit it.

## Recommendation
The `sender` forwarded to `beforeSwap` must represent the economically relevant actor, not the intermediary contract. Two viable approaches:

1. **Pass original caller in extensionData**: The router encodes `msg.sender` (the actual user) into `extensionData` before calling the pool. The extension decodes and verifies it, with a trusted-router check inside the extension.
2. **Guard against router allowlisting**: Explicitly document that allowlisting the router address defeats the per-user gate, and provide a wrapper extension that decodes a signed user identity from `extensionData` when the direct caller is a known router.

The root fix is that the allowlist must gate the address that controls the economic decision (the end user), not the address that holds the ERC-20 approval or calls the pool.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension, router allowlisted
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// alice is NOT individually allowlisted
// assertFalse(swapExtension.isAllowedToSwap(address(pool), alice));

// Alice calls through the router — sender seen by extension = router (allowlisted)
vm.startPrank(alice);
token0.approve(address(router), type(uint256).max);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: alice,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        tokenIn: address(token0),
        deadline: block.timestamp,
        extensionData: ""
    })
);
vm.stopPrank();
// Swap succeeds — alice bypassed the per-user allowlist via the router
```

The exploit path is: `alice` → `MetricOmmSimpleRouter.exactInputSingle` (L67-86) → `pool.swap(msg.sender=router, ...)` (L230-240 MetricOmmPool.sol) → `_beforeSwap(sender=router, ...)` (L149-177 ExtensionCalling.sol) → `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router] == true` (L37 SwapAllowlistExtension.sol) → swap proceeds despite alice not being allowlisted.