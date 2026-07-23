Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any caller to bypass per-user swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes its own `msg.sender` (the router) as the `sender` argument to `_beforeSwap`, which forwards it unchanged to `SwapAllowlistExtension.beforeSwap`. The extension then evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originating_user]`. Any pool admin who allowlists the router so that legitimate users can reach the pool through the standard periphery simultaneously grants every unprivileged user the ability to bypass the per-user allowlist.

## Finding Description
`MetricOmmPool.swap` invokes `_beforeSwap` with `msg.sender` as the first argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged as `sender` to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (enforced by `onlyPool`) and `sender` is the router address. `MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

There is no mechanism by which the router forwards the originating EOA's address as `sender`. The same applies to `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181). The check `allowedSwapper[pool][router]` is `true` for every caller of the router — including users who were never individually approved.

Existing guards are insufficient: `onlyPool` in `BaseMetricExtension` only verifies the caller is a registered pool; it does not validate the identity of the originating user. There is no secondary check in the extension or the pool that recovers the true originator.

## Impact Explanation
This is an admin-boundary break. A pool admin who deploys a curated pool (e.g., KYC-gated, institutional-only) with `SwapAllowlistExtension` and allowlists the router address — a necessary operational step for legitimate users to access the pool through the standard periphery — simultaneously voids the per-user allowlist for all router-mediated swaps. Any unprivileged user can trade in the curated pool by calling any `MetricOmmSimpleRouter` entry point, bypassing the access control the pool admin believed was enforced. The pool admin's configured protection is silently and completely negated.

## Likelihood Explanation
The scenario requires no special privileges, no frontrunning, no flash loans, and no oracle manipulation. The only precondition is that the admin has allowlisted the router, which is the expected and necessary configuration for any pool that intends to support the standard periphery. The allowlist mappings are public, so any observer can confirm the router is approved and immediately exploit the bypass. The attack is repeatable on every swap.

## Recommendation
The `sender` argument forwarded to `beforeSwap` must represent the originating user, not the intermediate router. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should encode the originating `msg.sender` into `extensionData` (or a dedicated swap parameter) so the extension can decode the true caller.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode the true originator from `extensionData` when the direct caller (`sender`) is a known router, or the pool interface should be extended to carry a separate `originator` field through the hook call chain.

Until the hook receives the true originating address, `SwapAllowlistExtension` cannot enforce per-user access control for router-mediated swaps.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin: setAllowedToSwap(pool, alice, true)      // alice is KYC'd
3. Admin: setAllowedToSwap(pool, router, true)      // needed so alice can use the router
4. Bob (not KYC'd) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: pool,
           recipient: bob,
           ...
       })
5. Router calls pool.swap(bob, ...) — pool's msg.sender = router.
6. MetricOmmPool._beforeSwap(router, bob, ...) is called.
7. Extension checks allowedSwapper[pool][router] → true.
8. Bob's swap executes. Per-user allowlist bypassed.
```

Foundry test plan: deploy `SwapAllowlistExtension`, configure a pool with it, allowlist `alice` and the router, then call `exactInputSingle` from an address that is not `alice` and assert the swap succeeds (demonstrating the bypass).