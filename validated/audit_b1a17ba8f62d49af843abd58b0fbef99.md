Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks the router's address rather than the actual user's address. A pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently opens the pool to every user, completely defeating the per-user gate.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the contract calling the extension) and `sender` is the address the pool forwarded — which is `msg.sender` of the original `pool.swap()` call:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData
);
```

The router is `msg.sender` of `pool.swap()`, so the pool passes the router's address as `sender` to the extension. The allowlist check resolves to `allowedSwapper[pool][router_address]`. If the admin allowlists the router so that approved users can use it, the check passes for every caller regardless of their identity. There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

## Impact Explanation

Any user can bypass `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter` whenever the router is allowlisted. The access-control invariant the extension is designed to enforce — that only approved addresses may swap — is completely broken for router-mediated paths. Unauthorized users gain full swap access to a pool the admin intended to restrict, enabling adverse selection against LPs, circumvention of KYC or risk-management gates, and front-running of allowlisted counterparties. This is broken core pool functionality: the allowlist extension fails to gate the economically relevant actor on the primary public swap path.

## Likelihood Explanation

Medium. A pool admin deploying a restricted pool with `SwapAllowlistExtension` will naturally want their approved users to be able to use the standard router. Allowlisting the router is the only mechanism available to enable this, and it is a straightforward, expected admin action. The admin has no indication from the extension's interface or documentation that doing so opens the pool to all users. Any unprivileged user can exploit this by simply calling `MetricOmmSimpleRouter` functions against the restricted pool.

## Recommendation

The extension must check the identity of the actual originating user, not the intermediary contract. Two viable approaches:

1. **Router-forwarded identity in `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks that address. The extension must verify the caller is a factory-registered router before trusting `extensionData`.
2. **Pool-level originator tracking**: The pool stores the original `tx.origin` or a router-forwarded address in transient storage before calling extensions, and the extension reads it from there.

Until fixed, pool admins must be explicitly warned that allowlisting the router is equivalent to setting `allowAllSwappers = true`.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only user A is approved.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so user A can use it.
4. User B (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(recipient, ...)` — pool passes `sender = router` to the extension.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. User B's swap executes successfully on the restricted pool.

Relevant code:
- [1](#0-0) 
- [2](#0-1) 
- [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
