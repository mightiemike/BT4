Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end-user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives the pool's `msg.sender` as its `sender` argument and checks it against `allowedSwapper[pool][sender]`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the actual end-user. If the router is allowlisted, every address on the network can bypass the gate; if it is not allowlisted, every individually-allowlisted user is locked out of the router entirely.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // whoever called pool.swap() — the router, not the end-user
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the pool's `msg.sender`:

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

The extension then checks that router address against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The lookup is `allowedSwapper[pool][router]`, never `allowedSwapper[pool][actual_user]`. The same flaw applies to `exactInput` (L104-112) and `exactOutputSingle`/`exactOutput`, since the router is always the pool's `msg.sender` in every path.

No existing guard corrects this: `BaseMetricExtension` only validates that `msg.sender` is a registered pool; it does not recover the original caller's identity.

## Impact Explanation

**Bypass path (router allowlisted):** Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle` targeting a pool whose allowlist includes the router address. The extension passes, the swap executes, and the pool's access-control invariant is broken. Pools configured as private (KYC-gated, institutional, or partner-only) accept trades from arbitrary counterparties, exposing LP capital to adverse-selection risk and violating the pool admin's explicit access policy. This is an admin-boundary break reachable by any unprivileged caller with no special privileges required.

**Broken-functionality path (router not allowlisted):** Allowlisted users who attempt to swap through the router receive `NotAllowedToSwap`, making the standard periphery unusable for the pool's intended participants. This is broken core swap functionality for legitimate users.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary public swap interface. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router will encounter one of the two failure modes. Allowlisting the router is the natural configuration for a pool that wants to restrict direct pool access while still supporting the standard UX — making the bypass scenario the most likely real-world outcome. No flash loans, special tokens, or unusual conditions are required; a plain `exactInputSingle` call suffices.

## Recommendation

The extension must check the **original caller's identity**, not the intermediary router. Two viable approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. Requires coordinated changes to the router and extension.
2. **Trusted-router registry:** Add a registry of known routers to the extension. When `sender` is a known router, decode the actual user from `extensionData`; otherwise check `sender` directly.
3. **Document incompatibility and enforce it:** Revert in `beforeSwap` whenever `sender` is a known router address, forcing direct-pool-only access for allowlisted pools.

## Proof of Concept

```solidity
// Scenario: router is allowlisted, non-allowlisted user bypasses the gate

// Pool admin allowlists the router (natural config for standard UX)
extension.setAllowedToSwap(pool, address(router), true);

// Attacker (not individually allowlisted) calls the router
router.exactInputSingle(ExactInputSingleParams({
    pool:             pool,
    recipient:        attacker,
    zeroForOne:       true,
    amountIn:         1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:    type(uint128).max,
    deadline:         block.timestamp,
    extensionData:    ""
}));
// pool.swap called with msg.sender = router
// SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
// Attacker swaps successfully despite never being individually allowlisted

// Scenario: specific users allowlisted, router not allowlisted — legitimate user locked out
extension.setAllowedToSwap(pool, legitimateUser, true);
// router is NOT allowlisted

vm.prank(legitimateUser);
router.exactInputSingle(...);
// pool.swap called with msg.sender = router
// allowedSwapper[pool][router] == false → NotAllowedToSwap revert
// Legitimate user cannot use the router at all
```