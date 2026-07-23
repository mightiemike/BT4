Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Restrictions via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the router contract address, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user—including non-allowlisted ones—can bypass the per-user restriction by routing through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check at line 37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the value passed by the pool from `MetricOmmPool.swap` at lines 230–240, where `_beforeSwap(msg.sender, ...)` is called with `msg.sender` being the immediate caller of `pool.swap`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly at lines 72–80, making `msg.sender` of `pool.swap` the router contract address. Therefore `SwapAllowlistExtension` checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates an irreconcilable dilemma for the pool admin:
- **Do NOT allowlist the router**: Allowlisted users cannot use the router at all.
- **Allowlist the router**: ALL users (including non-allowlisted) can swap through the router.

The same issue applies to `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165) in `MetricOmmSimpleRouter`, since all of them call `pool.swap` with `msg.sender = router`.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC'd counterparties, whitelisted market makers) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. This constitutes broken core pool functionality: the allowlist guard fails open for the primary supported periphery path, allowing unauthorized users to trade against LP positions at oracle-derived prices without authorization, potentially draining LP value and violating access controls on curated pools.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery swap entrypoint. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants to support router-mediated swaps must allowlist the router, at which point the guard is fully bypassed. The router address is a single, publicly known contract, so the bypass requires no special setup beyond a standard router call.

## Recommendation
`SwapAllowlistExtension` should check the end user rather than the immediate `msg.sender` of `pool.swap`. The cleanest fix is to have the router encode `msg.sender` (the end user) into `extensionData`, and have the extension decode and check it. This requires a protocol-level convention where the router populates an authenticated originator field that the extension can verify. Alternatively, the extension can recognize known router addresses and, when `sender` is a router, require the extension payload to carry the authenticated end-user identity.

## Proof of Concept
```
Setup:
  - Pool deployed with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the only intended user
  - Bob is NOT allowlisted

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Bob's swap executes successfully against the restricted pool

Result:
  - Bob (non-allowlisted) swaps against a pool that was supposed to be restricted to alice only
  - The per-user allowlist guard is completely bypassed via the supported router path
```