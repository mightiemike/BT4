Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the originating user. If the router address is allowlisted (to enable router-based swaps), any unprivileged user can bypass the per-pool swap allowlist entirely by routing through the router.

## Finding Description
**Call path:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — `msg.sender` to the pool is the **router address**.
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` — `sender` = router address.
4. `ExtensionCalling._beforeSwap` encodes and dispatches `IMetricOmmExtensions.beforeSwap(sender, ...)` — `sender` = router address.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = router.

The check therefore passes if and only if the **router** is allowlisted, not the individual user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the allowlist to every user of the router. Conversely, a pool admin who allowlists individual users will find those users blocked when they route through the router.

**Root cause:** `SwapAllowlistExtension.beforeSwap` (line 37) uses the `sender` argument (= `msg.sender` of `pool.swap`) as the identity to gate. The router is always that `msg.sender`, so the actual originating user is never checked.

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly gates the `owner` argument (the position owner), not `sender` (the caller). The swap extension has no equivalent "real user" field to fall back on because the pool's `swap` interface does not carry the originating user separately.

**Existing guards:** `BaseMetricExtension.onlyPool` (via `msg.sender` check in `beforeSwap`) only verifies the caller is the pool — it does not recover the originating user. The router's transient callback context stores the payer but this is never forwarded to the extension.

## Impact Explanation
Any unprivileged user can execute swaps on a pool protected by `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter` if the router address is allowlisted. This breaks the core access-control invariant of the allowlist extension: the pool's swap gate is bypassed, allowing unauthorized parties to trade against restricted liquidity. This constitutes broken core pool functionality and unauthorized swap execution — a High-severity impact under Sherlock contest rules.

## Likelihood Explanation
The bypass requires the router to be allowlisted on the pool. A pool admin who wants to support router-based swaps for any allowlisted user must allowlist the router, which is the natural and expected configuration. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged user with no special capability — just a standard `exactInputSingle` call. The condition is realistic and repeatable.

## Recommendation
Pass the originating user through the swap path so the extension can gate on the real actor. One approach: add an `originator` field to the pool's `swap` interface (or extension data) that the router populates with `msg.sender` before calling the pool, and have `SwapAllowlistExtension` check that field. Alternatively, gate on `tx.origin` as a stopgap (with documented caveats), or require that the router itself enforces the allowlist check before forwarding to the pool.

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Pool admin allowlists the router: swapExtension.setAllowedToSwap(pool, address(router), true)
// 3. Unprivileged user (not individually allowlisted) calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    tokenIn: token0,
    recipient: attacker,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    extensionData: ""
}));
// 4. pool.swap is called with msg.sender = router (allowlisted)
// 5. SwapAllowlistExtension passes — attacker swaps successfully despite not being allowlisted
```