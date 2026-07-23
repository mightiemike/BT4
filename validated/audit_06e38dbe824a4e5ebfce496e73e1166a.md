Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the economic initiator, allowing any user to bypass per-pool swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` inside the pool, not the actual user. Because the pool admin must allowlist the router for any router-mediated swap to succeed, every user — including those not individually allowlisted — can bypass the curation policy by routing through the router.

## Finding Description
**Root cause — `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`:**

In `metric-core/contracts/MetricOmmPool.sol` lines 230–231, `_beforeSwap` is called with `msg.sender` as the first argument:
```solidity
_beforeSwap(
  msg.sender,   // ← this is the router when called via router
  ...
```
`ExtensionCalling._beforeSwap()` (lines 160–176 of `metric-core/contracts/ExtensionCalling.sol`) encodes this value verbatim as the `sender` argument to `IMetricOmmExtensions.beforeSwap`.

**`SwapAllowlistExtension.beforeSwap()` checks this `sender`:**

In `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol` line 37:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```
Here `msg.sender` is the pool (correct for pool identity) and `sender` is whoever called `pool.swap()` — the router, not the actual user.

**`MetricOmmSimpleRouter` calls `pool.swap()` directly:**

In `metric-periphery/contracts/MetricOmmSimpleRouter.sol` lines 72–80 (`exactInputSingle`), lines 104–112 (`exactInput`), lines 136–137 (`exactOutputSingle`), and lines 165–181 (`exactOutput`), the router calls `IMetricOmmPoolActions(pool).swap(...)` directly, making the router `msg.sender` inside the pool for all four swap paths.

**Exploit flow:**
1. Pool admin deploys a curated pool with `SwapAllowlistExtension`.
2. Pool admin allowlists the router: `allowedSwapper[pool][router] = true` (required for any allowlisted user to use the router).
3. Attacker (not individually allowlisted) calls `router.exactInputSingle(...)`.
4. Router calls `pool.swap(...)` → pool calls `_beforeSwap(msg.sender=router, ...)` → extension checks `allowedSwapper[pool][router] == true` → passes.
5. Attacker receives output tokens from the curated pool despite `allowedSwapper[pool][attacker] == false`.

**Existing guards are insufficient:** There is no mechanism in the router to encode the original caller into `extensionData`, and the extension has no fallback to decode such data. The `_setNextCallbackContext` call in the router (line 71) stores `msg.sender` only for the payment callback, not for extension authorization.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to restrict trading to a specific set of addresses (e.g., KYC'd users, whitelisted market makers) is completely open to any user who routes through `MetricOmmSimpleRouter`. This is a direct admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged public entrypoint. Disallowed users can execute swaps against the pool's liquidity, directly violating the pool's curation policy and potentially draining LP assets at prices the pool admin intended to restrict to authorized counterparties.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery swap path. Any pool admin who deploys a curated pool and wants allowlisted users to be able to use the router must allowlist the router address, which simultaneously opens the pool to all users. The bypass requires no special knowledge, no privileged access, and no setup beyond observing that the router is allowlisted — any EOA can exploit it immediately and repeatably across all four router swap paths (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

## Recommendation
The extension must gate the **economic initiator** of the swap, not the immediate caller of `pool.swap()`. The preferred fix is to have the router encode `msg.sender` (the actual user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap()` decode and check that address when `extensionData` is non-empty. The pool admin would then allowlist actual users, not the router. Alternatively, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and require direct `pool.swap()` calls on curated pools.

## Proof of Concept
```solidity
// Setup: curated pool with SwapAllowlistExtension
// Pool admin allowlists the router so allowlisted users can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Pool admin does NOT allowlist attacker
// allowedSwapper[pool][attacker] == false

// Attacker bypasses allowlist via router:
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 10_000,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Succeeds: pool calls _beforeSwap(msg.sender=router, ...)
// Extension checks allowedSwapper[pool][router] == true → passes
// Attacker receives token1 output despite allowedSwapper[pool][attacker] == false
```