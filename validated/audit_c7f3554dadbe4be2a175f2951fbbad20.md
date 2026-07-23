Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any caller to bypass per-user swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which sets it to the pool's own `msg.sender` — the direct caller. When `MetricOmmSimpleRouter` is used, the pool's `msg.sender` is the router contract, not the originating EOA. A pool admin who allowlists the router (required for legitimate users to reach the pool through the standard periphery) inadvertently grants every caller of the router access, completely voiding the per-user allowlist.

## Finding Description
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 231, passing the pool's direct caller as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap(), i.e. the router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension as the first argument. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (enforced by `onlyPool`) and `sender` is the router. The check becomes `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly with no mechanism to forward the originating EOA:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The same applies to `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181). None of these entry points pass the originating `msg.sender` into the pool's `swap` call in any way that reaches the extension's `sender` argument.

Existing guards are insufficient: `onlyPool` in `BaseMetricExtension` only confirms the caller is a valid pool — it does not help distinguish the originating user from the router.

## Impact Explanation
This is an admin-boundary break. A pool admin deploying a curated pool (e.g., KYC-gated or institutional-only) with `SwapAllowlistExtension` must allowlist the router address to allow approved users to trade through the standard periphery. Once `allowedSwapper[pool][router] = true`, the check `allowedSwapper[pool][router]` passes for every caller of the router — including users who were never individually approved. The per-user allowlist is completely ineffective for all router-mediated swaps. The pool admin's configured access control is silently voided by a public, permissionless periphery path.

## Likelihood Explanation
The scenario requires no special privileges or complex setup. The only precondition is that the admin has allowlisted the router, which is the expected and necessary operational step for any pool intending to support the standard periphery. The allowlist state is stored in public mappings, so any observer can detect when the router is approved and exploit the bypass by calling any of the four router entry points.

## Recommendation
The `sender` argument forwarded to `beforeSwap` must represent the originating user, not the intermediate router. Two complementary approaches:

1. **Router-side**: `MetricOmmSimpleRouter` should encode the originating `msg.sender` into `extensionData` before calling `pool.swap`, and `SwapAllowlistExtension.beforeSwap` should decode and check it when the direct `sender` is a known router.
2. **Extension-side**: The pool interface could be extended to carry a separate `originator` field through the hook call chain, populated by the router and forwarded by the pool to all extensions.

Until the extension receives the true originating address, `SwapAllowlistExtension` cannot enforce per-user access control for router-mediated swaps.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension.
2. Admin: setAllowedToSwap(pool, alice, true)       // alice is KYC'd
3. Admin: setAllowedToSwap(pool, router, true)       // needed so alice can use the router
4. Bob (not KYC'd) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: pool,
           recipient: bob,
           ...
       })
5. Router calls pool.swap(bob, ...) — pool's msg.sender = router.
6. _beforeSwap(router, ...) is called; extension receives sender = router.
7. Extension checks allowedSwapper[pool][router] → true.
8. Bob's swap executes. Allowlist bypassed.
```