All four cited code paths are confirmed in the actual repository. Let me verify the complete exploit chain:

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap` — confirmed at L230-231.
2. `ExtensionCalling._beforeSwap` encodes that `sender` verbatim into the extension call — confirmed at L162-165.
3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router — confirmed at L37.
4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender` — confirmed at L72-80.

The bypass path is fully reachable with no privileged preconditions beyond the natural admin action of allowlisting the router.

---

Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool, which is the pool's own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. If the router is allowlisted — a natural admin action to enable router-based trading — every non-allowlisted user can bypass the curated pool's swap gate by calling through the router, breaking the allowlist invariant entirely.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // pool's msg.sender — the router when routed
  recipient, ...
```

`ExtensionCalling._beforeSwap` encodes that `sender` verbatim into the extension call:

```solidity
// metric-core/contracts/ExtensionCalling.sol L162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is whatever the pool received as its own `msg.sender`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

At that point `msg.sender` to the pool is the **router address**, so the extension checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. Once the router is allowlisted, the check always passes regardless of who the actual caller is.

## Impact Explanation
A curated pool's entire swap-access policy is defeated. Any non-allowlisted user can execute swaps against the pool by routing through `MetricOmmSimpleRouter`. LP funds are exposed to trades the pool admin explicitly intended to block, constituting a direct loss of the curation invariant and broken core pool functionality. This meets the "Broken core pool functionality causing loss of funds" and "Admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
The trigger is unprivileged: any user can call the public router. The precondition — the router being allowlisted — is a natural and expected admin action for any curated pool that also wants to support the standard periphery UX. No special setup or malicious initial configuration is required beyond the normal deployment of a curated pool with router support.

## Recommendation
`SwapAllowlistExtension.beforeSwap` must gate on the economically relevant actor, not the intermediary. Two options:

1. **Require the router to forward the original caller**: add a field to `extensionData` that the router populates with `msg.sender` (the actual user), and have the extension verify that field when `sender` is a known trusted router. The extension then checks the forwarded identity against the allowlist.
2. **Register trusted routers and check recipient or forwarded identity**: require the pool admin to register trusted routers; when `sender` is a trusted router, check the `recipient` or a caller identity embedded in `extensionData`.

The simplest safe interim fix is to document that router-mediated swaps on allowlisted pools are unsupported and never allowlist the router as a blanket swapper.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists router: setAllowedToSwap(pool, router, true)
  - Admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  5. Swap executes; attacker receives output tokens

Result:
  - attacker, who is not on the allowlist, successfully swaps against the curated pool
  - The allowlist invariant is broken; LP funds are exposed to unauthorized counterparties
```