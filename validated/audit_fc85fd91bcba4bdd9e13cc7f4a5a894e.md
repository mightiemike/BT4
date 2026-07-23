Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via the Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is always `msg.sender` of the `pool.swap()` call — the router contract, not the end user, when routing through `MetricOmmSimpleRouter`. If the router address is allowlisted for a pool, every user, including those not individually allowlisted, can bypass the swap allowlist by routing through the router. There is no configuration that simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- always the immediate caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is the value the pool forwarded — the router's address, not the end user's address.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

The router has no mechanism to forward the original `msg.sender` to the pool. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. When a pool admin allowlists the router address to enable periphery swaps, `allowedSwapper[pool][router] = true`, and the check passes for every user who routes through the router, regardless of whether that user is individually allowlisted.

The existing test `test_allowedSwapSucceeds` only exercises direct pool calls via `TestCaller`, never through `MetricOmmSimpleRouter`, so this bypass path is untested.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers, or protocol-controlled addresses) can be fully bypassed by any user who routes through `MetricOmmSimpleRouter`. Once the router is allowlisted — a natural and necessary step for any allowlisted pool that also wants to support the standard periphery — the allowlist provides zero protection. Unauthorized users can trade on the pool, drain liquidity at oracle-derived prices, or execute arbitrage in ways the pool admin explicitly intended to prevent. This constitutes broken core pool functionality and an admin-boundary break where an unprivileged path bypasses a pool admin-configured access control.

## Likelihood Explanation

The trigger requires the pool admin to allowlist the router, which is the expected operational step for any allowlisted pool that also wants to support the standard periphery swap path. The `SwapAllowlistExtension` provides no warning or mechanism to distinguish end users behind the router. Any pool that enables both the allowlist extension and the router is affected. The attacker needs no special privileges — only the ability to call `MetricOmmSimpleRouter`.

## Recommendation

The `sender` identity passed to `beforeSwap` must represent the economic actor, not the intermediary. The cleanest fix is to have `MetricOmmSimpleRouter` accept and forward an explicit `realSender` parameter to `pool.swap`, and have the pool pass it through to extensions alongside `msg.sender`. Alternatively, add an optional `originSender` field to the swap call that the pool passes to extensions as a separate argument, allowing extensions to gate on the true economic actor rather than the immediate caller.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   (alice is individually allowlisted)
  - allowedSwapper[pool][router] = true  (router allowlisted to enable periphery)
  - bob is NOT in allowedSwapper

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks allowedSwapper[pool][router] → true → passes
  5. Bob's swap executes successfully despite not being allowlisted

Result: bob bypasses the swap allowlist entirely by routing through the router.

Foundry test: Deploy pool with SwapAllowlistExtension, allowlist the router,
deploy MetricOmmSimpleRouter, call exactInputSingle from an address not in
allowedSwapper, assert the swap succeeds (no NotAllowedToSwap revert).
```