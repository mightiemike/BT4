Audit Report

## Title
Swap Allowlist Bypass via Router — Any User Can Bypass `SwapAllowlistExtension` by Routing Through `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router is always the direct caller of `pool.swap()`, so `sender` resolves to the router address rather than the originating user. Any pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to all users, completely defeating the per-user allowlist.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension caller) and `sender` is the value forwarded by the pool from `MetricOmmPool.swap()`:

```solidity
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
```

So `sender` = `msg.sender` of `pool.swap()`. In every router path, the router is the direct caller of `pool.swap()`:

- `exactInputSingle`: calls `IMetricOmmPoolActions(params.pool).swap(...)` directly with the router as `msg.sender`.
- `exactInput`: each hop calls `IMetricOmmPoolActions(pool).swap(...)` from within the router loop.
- `exactOutputSingle`: calls `IMetricOmmPoolActions(params.pool).swap(...)` directly.
- `exactOutput` intermediate hops: called from `_exactOutputIterateCallback`, which is itself a function of the router contract, so the router is still `msg.sender` of each `pool.swap()` call.

In every case, the extension receives `sender = router_address`. A pool admin who wants to support router-mediated swaps must allowlist the router (`allowedSwapper[pool][router] = true`), which then allows any non-allowlisted user to call `router.exactInputSingle()` and pass the check, since the extension only sees the router address.

There is no mechanism in `SwapAllowlistExtension` to inspect the original user from `extensionData` or any other source. The `extensionData` field passed by the router is user-controlled and not authenticated.

## Impact Explanation
The per-user swap allowlist — the core access control mechanism of `SwapAllowlistExtension` — is completely defeated for any user who routes through `MetricOmmSimpleRouter`. Disallowed users can trade on restricted pools, constituting unauthorized access to private/curated liquidity pools. This is broken core pool functionality: the invariant that "only allowlisted users may swap on a curated pool" is violated regardless of which supported public entrypoint is used. This meets the "Admin-boundary break" and "Broken core pool functionality" allowed impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entrypoint for end users. Any pool admin who deploys a curated pool with `SwapAllowlistExtension` and also wants users to be able to use the router (the normal UX path) will allowlist the router, triggering the bypass. No special privileges are required — any user can call the router. The trigger condition (router allowlisted) is the expected operational state for any pool that supports router-mediated swaps.

## Recommendation
The extension must gate the **original user**, not the intermediary router. Viable approaches:

1. **Pass original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. Requires a trusted-router registry so only authenticated routers can supply this field.
2. **Trusted router registry in the extension**: The extension maintains a set of trusted routers; when `sender` is a trusted router, it reads the original user from `extensionData` and checks that address instead.
3. **Document incompatibility**: Enforce at the factory/config validation layer that `SwapAllowlistExtension` is incompatible with router-mediated swaps and prevent co-configuration.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin allowlists user1:
       swapExt.setAllowedToSwap(pool, user1, true)
3. Admin allowlists the router to support router-mediated swaps:
       swapExt.setAllowedToSwap(pool, router, true)
4. user2 (NOT allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap() → msg.sender of pool = router address.
6. Pool calls _beforeSwap(router, ...) → extension receives sender = router.
7. Extension checks allowedSwapper[pool][router] → true → passes.
8. user2 successfully swaps on the curated pool, bypassing the allowlist.
```

Root cause: `SwapAllowlistExtension.beforeSwap` at `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol` L37 checks `sender` which is unconditionally bound to `msg.sender` of `pool.swap()` at `metric-core/contracts/MetricOmmPool.sol` L231, and the router always occupies that position for all swap paths in `metric-periphery/contracts/MetricOmmSimpleRouter.sol` L72–80, L104–112, L136–137, L220–228.