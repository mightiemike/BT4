Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the actual user, allowing any user to bypass per-user allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` as the direct caller of `pool.swap()`. When users interact through `MetricOmmSimpleRouter`, `sender` is the router's address, not the originating user. If the pool admin allowlists the router (required for any router-based swap to succeed), every unprivileged user can bypass the per-user swap allowlist by routing through `MetricOmmSimpleRouter`.

## Finding Description

**Call path:**

1. User (not on allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(recipient, zeroForOne, amountIn, priceLimit, "", extensionData)` — `msg.sender` to the pool is the **router**.
3. Inside `MetricOmmPool.swap()`, the pool calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is the router address.
4. `ExtensionCalling._beforeSwap` forwards `sender = router_address` to `SwapAllowlistExtension.beforeSwap`.
5. `SwapAllowlistExtension.beforeSwap` evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the **router**, not the originating user. The check resolves to `allowedSwapper[pool][router]`.

**Root cause:** The pool correctly passes `msg.sender` (the direct caller of `pool.swap()`) as `sender` to the extension. When the router intermediates, `msg.sender` to the pool is the router, so the allowlist check is against the router address, not the actual trader.

**Exploit flow:**
- Pool admin configures `SwapAllowlistExtension` to gate individual swappers (e.g., KYC'd addresses only).
- Pool admin must allowlist the router address to permit any router-based swap: `setAllowedToSwap(pool, router, true)`.
- Once the router is allowlisted, **any** user — including those not individually allowlisted — can call `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the allowlist check passes because `allowedSwapper[pool][router] == true`.
- The per-user allowlist is entirely bypassed for all router-mediated swaps.

**Existing guards are insufficient:** The only guard is `allowedSwapper[pool][sender]` in `beforeSwap`. There is no mechanism to recover the original `msg.sender` of the router call from within the extension, and the pool does not forward the originating user separately.

## Impact Explanation
The `SwapAllowlistExtension` is a production extension designed to restrict swap access to approved addresses per pool. Its core invariant — that only allowlisted addresses may swap — is broken for all router-mediated swaps once the router is allowlisted. Any unprivileged user can swap in a pool intended to be permissioned (e.g., regulatory compliance, whitelist-only liquidity pools), causing unauthorized access to pool liquidity and potential loss of protocol/LP integrity guarantees. This is a broken core pool functionality / admin-boundary break impacting fund flows.

## Likelihood Explanation
The `MetricOmmSimpleRouter` is the primary user-facing interface. Any pool that uses `SwapAllowlistExtension` and also wants to support router-based swaps must allowlist the router, making the bypass trivially reachable by any unprivileged caller. No special privileges, flash loans, or complex setup are required — a single `exactInputSingle` call suffices. The condition is repeatable on every block.

## Recommendation
Pass the originating user through the extension data or a dedicated field. One approach: the router encodes `msg.sender` into `extensionData` and `SwapAllowlistExtension.beforeSwap` reads it when `sender` is a known router. A cleaner fix is for the pool to accept an explicit `originator` parameter in `swap()` that extensions can use, or for the router to pass the real user address in `extensionData` and for `SwapAllowlistExtension` to decode and check it when the direct `sender` is a trusted router. Alternatively, document that the allowlist gates the direct caller only and require users to call the pool directly for permissioned pools.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension configured.
// 2. Pool admin allowlists the router: swapAllowlist.setAllowedToSwap(pool, address(router), true).
// 3. Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] == false.

// Attack:
// attacker (not individually allowlisted) calls:
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: attacker,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// Result: swap succeeds because allowedSwapper[pool][router] == true,
// even though allowedSwapper[pool][attacker] == false.
// The per-user allowlist is bypassed.
```