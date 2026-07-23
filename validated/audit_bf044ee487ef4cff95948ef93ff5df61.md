Audit Report

## Title
SwapAllowlistExtension checks router address instead of real user, enabling full allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool populates with `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router so curated users can reach the pool through the standard periphery inadvertently opens the allowlist to every caller of the router, because the extension evaluates `allowedSwapper[pool][router]` and the router is allowlisted.

## Finding Description

**Root cause — wrong actor bound to the guard check.**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmPool.swap` populates that argument with `msg.sender` of the `swap()` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    ...
```

`ExtensionCalling._beforeSwap` forwards it verbatim to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L163-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, ...)
```

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the router contract:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The real user's address (`msg.sender` of `exactInputSingle`) is stored only in transient storage via `_setNextCallbackContext` for the payment callback. It is never forwarded to the pool or the extension. The extension therefore evaluates `allowedSwapper[pool][MetricOmmSimpleRouter]`.

**The bypass path.** A pool admin who wants allowlisted users to reach the pool through the standard periphery must call `setAllowedToSwap(pool, router, true)`. Once `allowedSwapper[pool][router] = true`, every caller of the router — including non-allowlisted users — passes the guard, because the extension sees `sender = router` and the router is allowlisted. The check on the actual user is never performed. The same bypass applies to `exactOutputSingle`, `exactInput`, and `exactOutput`.

## Impact Explanation
Any user can swap on a curated pool that has allowlisted the router, bypassing the access control the pool admin intended. Non-allowlisted users can consume LP liquidity at the pool's oracle-derived prices, causing direct loss to LPs whose positions were offered only to a restricted set of counterparties. This constitutes a broken core pool invariant (the allowlist guard fails open) and direct loss of LP principal — a High-severity impact under Sherlock contest thresholds.

## Likelihood Explanation
Pool admins who deploy a `SwapAllowlistExtension`-gated pool and want their allowlisted users to use the standard `MetricOmmSimpleRouter` periphery must allowlist the router. This is the natural and only supported operational step; there is no other mechanism to let curated users reach the pool through the router. Once the router is allowlisted, the bypass is unconditionally reachable by any unprivileged caller of the router with no special setup, no privileged role, and no precondition beyond having tokens to swap.

## Recommendation
The extension must gate on the economically relevant actor, not the direct pool caller. Two approaches:

1. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the real user's address into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it — but only when `sender` is a trusted router (to prevent spoofing via crafted `extensionData`).
2. **Direct-only policy**: Document and enforce that `SwapAllowlistExtension` is incompatible with router-mediated flows by reverting in `beforeSwap` when `sender` is not an EOA or is a known router, forcing direct pool calls only.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow curated users to reach the pool via the standard periphery.
3. An unprivileged attacker (not in the allowlist) calls `router.exactInputSingle(...)` targeting the pool.
4. The router calls `pool.swap(...)` — `msg.sender` of this call is the router.
5. The pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. The attacker successfully swaps on the curated pool without being allowlisted.

Minimal Foundry test:
```solidity
// Setup: pool with SwapAllowlistExtension, router allowlisted
extension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not in allowlist) swaps via router
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    ...
}));
// Succeeds — allowlist bypassed
```