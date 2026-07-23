Audit Report

## Title
`SwapAllowlistExtension` Per-User Allowlist Bypassed via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router becomes that `msg.sender`, so the extension checks whether the router is allowlisted rather than the actual end user. Any pool admin who allowlists the router to support standard UX inadvertently grants every unprivileged user access to the restricted pool.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the check:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool via `_callExtensionsInOrder`), and `sender` is the value forwarded by the pool. The pool always forwards its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    ...
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, passing `""` as `callbackData` and user-supplied `params.extensionData` — the original caller's identity is never encoded:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
```

The pool therefore sees `msg.sender = router`, calls `_beforeSwap(router, ...)`, and the extension evaluates `allowedSwapper[pool][router]`. If the router is allowlisted — the natural configuration for a pool admin who wants to support the canonical periphery entry point — the check passes for every call arriving through the router, regardless of who the actual end user is. The same path applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation

This is a direct admin-boundary break. The pool admin's per-user allowlist is silently voided for all router-mediated swaps. Any unprivileged user can trade on a curated pool (KYC-gated, counterparty-restricted, or regulatory-compliant) by simply routing through `MetricOmmSimpleRouter`. LP positions are exposed to unintended counterparties and unauthorized fund flows occur through the pool.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical periphery entry point. A pool admin deploying `SwapAllowlistExtension` who also wants to support standard router UX will allowlist the router — this is the expected and natural configuration. Once the router is allowlisted, the bypass is reachable by any unprivileged user with zero special setup, requiring only a standard router call.

## Recommendation

The extension must gate on the economically relevant actor, not the intermediary. Two options:

1. **Require direct pool calls only**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and that allowlisted users must call `pool.swap()` directly. This breaks standard UX.

2. **Forward original caller via `extensionData`**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it. The extension must reject calls where the decoded address is not itself allowlisted, and must also reject calls where `extensionData` is absent or malformed to prevent spoofing.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER.
2. Pool admin calls:
     swapExtension.setAllowedToSwap(pool, alice, true);   // allowlist alice
     swapExtension.setAllowedToSwap(pool, router, true);  // allowlist router for UX
3. bob (not allowlisted) calls:
     router.exactInputSingle(ExactInputSingleParams({
         pool: pool,
         zeroForOne: true,
         amountIn: 1000,
         recipient: bob,
         ...
     }));
4. Router calls pool.swap() — pool sees msg.sender = router.
5. Pool calls _beforeSwap(router, ...) [MetricOmmPool.sol L230-231].
6. Extension evaluates: allowedSwapper[pool][router] == true → passes [SwapAllowlistExtension.sol L37].
7. Bob's swap executes successfully despite not being allowlisted.
```