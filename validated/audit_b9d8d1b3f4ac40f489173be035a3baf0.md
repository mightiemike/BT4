Audit Report

## Title
`SwapAllowlistExtension` Validates Router Address Instead of Actual User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which is the router contract address when a swap is routed through `MetricOmmSimpleRouter`. `SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]` against this router address rather than the actual end user. Any user can bypass per-user swap restrictions on a pool by routing through `MetricOmmSimpleRouter` if the router is allowlisted, which is the expected configuration for a restricted pool that also supports router-based access.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- immediate caller, not the end user
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is the caller, it calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making `sender` = router address. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`.

A pool admin faces an inescapable dilemma: if the router is not allowlisted, even legitimately allowlisted users cannot use the router. If the router is allowlisted (the natural configuration), every user — including those not individually allowlisted — can swap freely through the router. There is no configuration that simultaneously allows allowlisted users to use the router while blocking non-allowlisted users.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified counterparties or institutional LPs) can be accessed by any arbitrary user via `MetricOmmSimpleRouter`. This breaks the admin-boundary invariant: an unprivileged user bypasses an access control that the pool admin explicitly configured. Unauthorized swaps drain LP liquidity from a pool intended to be restricted, constituting a direct loss of LP assets. Severity: Medium — requires the router to be allowlisted, but a pool admin who intends per-user gating would reasonably allowlist the router expecting it to propagate user identity.

## Likelihood Explanation
`SwapAllowlistExtension` is a production periphery contract. `MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who deploys a restricted pool and also wants users to access it via the standard router will allowlist the router, unknowingly opening the bypass to all users. No special privileges or non-standard tokens are required; any EOA can call the router.

## Recommendation
Pass the original user through the call chain rather than the immediate `msg.sender`. The preferred fix is to extend `pool.swap()` to accept an explicit `swapper` address (set by the router to `msg.sender` before calling the pool), and pass that to the extension instead of `msg.sender`. Alternatively, `MetricOmmSimpleRouter` could verify the allowlist before calling the pool, but this is weaker because it can be bypassed by calling the pool directly without the router.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowed.
3. Admin calls setAllowedToSwap(pool, router, true) — router allowlisted so alice can use it.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(recipient=bob, ...) — msg.sender in pool = router address.
6. _beforeSwap passes sender=router to SwapAllowlistExtension.
7. allowedSwapper[pool][router] == true → check passes.
8. Bob's swap executes successfully despite not being individually allowlisted.
```