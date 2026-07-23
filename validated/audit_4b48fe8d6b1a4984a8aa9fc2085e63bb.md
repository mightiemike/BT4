Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass the Swap Allowlist via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the immediate caller of `MetricOmmPool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the allowlist to every user, because the extension only evaluates the router's identity and never the originating user's.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool), and `sender` is the first argument forwarded from `MetricOmmPool.swap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // always the immediate pool caller
    recipient,
    ...
```

`ExtensionCalling._beforeSwap` ABI-encodes this `sender` and forwards it to the extension:

```solidity
// ExtensionCalling.sol L162-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

At that point `msg.sender` inside the pool is the router, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same applies to every hop in `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181) — all call `pool.swap` from the router context.

A pool admin who wants legitimate allowlisted users to use the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every user — including those explicitly excluded — can bypass the gate by routing through `MetricOmmSimpleRouter`. The originating user's identity is never checked.

## Impact Explanation
A curated pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, institutional LPs, whitelisted market makers) loses its curation guarantee entirely once the router is allowlisted. Any unprivileged user can execute swaps on the pool, draining LP value at oracle-derived prices, extracting spread fees, or triggering stop-loss extensions in ways the pool admin did not intend. This constitutes a broken core pool invariant (curated access) and a direct loss of LP principal, meeting the contest's "Broken core pool functionality causing loss of funds" and "direct loss of user principal" impact criteria.

## Likelihood Explanation
The pool admin faces an unavoidable dilemma: either allowlist the router (enabling the bypass for all users) or do not allowlist the router (blocking all router-mediated swaps for legitimate users too). Any production deployment that wants to support the standard periphery router while also enforcing an allowlist will naturally allowlist the router, triggering the bypass. The attacker needs no special privilege — a single public call to `MetricOmmSimpleRouter.exactInputSingle` suffices, with no setup beyond knowing the pool address.

## Recommendation
The extension must receive and check the originating user identity, not the immediate pool caller. Two approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool; the extension decodes and checks it. This requires a trusted encoding convention and the extension must verify the caller is a known router.
2. **Architectural fix**: `MetricOmmPool.swap` should accept and forward an explicit `originator` address (distinct from `msg.sender`) so extensions can gate on the economic actor rather than the transport layer.

## Proof of Concept
```
Setup:
  pool P configured with SwapAllowlistExtension E
  allowedSwapper[P][alice] = true   // alice is the only allowed swapper
  allowedSwapper[P][router] = true  // admin allowlists router so alice can use it

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})

  Router calls (MetricOmmSimpleRouter.sol L72-80):
    P.swap(recipient, zeroForOne, amount, limit, "", extensionData)
    // msg.sender inside P = router

  Pool calls (MetricOmmPool.sol L230-240):
    _beforeSwap(router, recipient, ...)

  ExtensionCalling forwards (ExtensionCalling.sol L162-176):
    E.beforeSwap(router, recipient, ...)
    // checks allowedSwapper[P][router] == true  ✓
    // bob's identity is never checked

Result:
  bob executes a swap on a curated pool he is not allowed to access.
  The allowlist is completely bypassed.
```