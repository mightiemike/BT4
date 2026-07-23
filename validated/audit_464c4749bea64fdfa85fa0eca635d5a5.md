Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Actual User, Allowing Any User to Bypass Curated Pool Restrictions via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which resolves to `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual user. A pool admin who allowlists the router — the only way to let approved users trade via the standard periphery — inadvertently grants every on-chain user access to the curated pool.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // whoever called pool.swap()
  ...
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` inside the extension = the pool):

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    ...
    params.extensionData
  );
```

The actual user's address is stored only in transient storage for the payment callback (`_setNextCallbackContext`) and is never surfaced to the extension. The pool's `msg.sender` is the router, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. Once the router is allowlisted — the necessary step for any approved user to use the router on a curated pool — the check passes for every caller regardless of identity.

## Impact Explanation
Any user can trade on a curated pool (KYC-gated, institution-only, or regulatory-restricted) by routing through the public `MetricOmmSimpleRouter`. The allowlist invariant — "only approved addresses may swap" — is completely broken. LP funds are exposed to counterparties the pool was explicitly designed to exclude, and regulatory or compliance guarantees of the pool operator are violated. This constitutes a broken core pool functionality causing direct policy-level fund impact for LPs in curated pools.

## Likelihood Explanation
The bypass requires the router to be allowlisted. However, this is not an edge case — it is the only way allowlisted users can use the router on a curated pool. Any operator who deploys a curated pool and wants their approved users to access it through the standard periphery must allowlist the router. The configuration that triggers the vulnerability is the expected production configuration for curated pools with router support. The attack requires no special privileges: any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()`.

## Recommendation
The `SwapAllowlistExtension` must gate on the actual economic actor, not the immediate caller of `pool.swap()`. Two sound approaches:

1. **Router-forwarded identity via `extensionData`**: Require the router to ABI-encode the actual user's address into `extensionData` and have the extension decode and check it. The extension should reject calls where `sender` is a known router but no valid user identity is present in `extensionData`.

2. **Separate allowlist entry for routed vs. direct swaps**: Document clearly that allowlisting the router opens the pool to all users, and provide a separate extension variant that decodes user identity from `extensionData` for router-mediated flows.

Using `tx.origin` is not recommended as it breaks contract-to-contract composability.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is approved.
3. Admin calls `setAllowedToSwap(pool, router, true)` — router is approved so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)` via `_beforeSwap(msg.sender, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully trades on the curated pool despite never being allowlisted.