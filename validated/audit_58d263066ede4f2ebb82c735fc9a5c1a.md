Audit Report

## Title
Swap Allowlist Bypassed via Router: Any User Can Trade in Curated Pools When Router Is Allowlisted - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` becomes the router address rather than the original EOA. If the pool admin allowlists the router to support router-mediated swaps for legitimate users, any unprivileged user can bypass the allowlist entirely by calling the router, breaking the core access-control invariant of curated pools.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`sender` is populated by `MetricOmmPool.swap` as `msg.sender` of the pool call (L230-231), then forwarded verbatim through `ExtensionCalling._beforeSwap`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router stores the original caller only in transient callback context (`_setNextCallbackContext`, L71) and then calls `pool.swap(params.recipient, ...)` (L72-80) with no originator field. The pool's `msg.sender` is now the router. The extension checks `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router, the check passes for **any** caller of the router. The original user's address is stored only in the router's transient callback context (`_getPayer()`, L195), which is used solely for payment and is never forwarded to the pool or extension. There is no mechanism for the extension to recover the original EOA.

## Impact Explanation
A pool admin who configures `SwapAllowlistExtension` intends to restrict trading to a curated set of addresses (e.g., KYC-verified users, institutional counterparties). If the admin also allowlists the router to give legitimate users access to router UX (deadline checks, slippage guards, multi-hop), the allowlist is silently voided for all router-mediated swaps. Any unprivileged user can trade in the restricted pool by calling `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on the router. This is an admin-boundary break: an unprivileged path bypasses the pool admin's access control, exposing LP funds to adverse selection from actors the pool was explicitly designed to exclude.

## Likelihood Explanation
The bypass requires the router to be allowlisted. A pool admin who wants allowlisted users to benefit from router UX must allowlist the router — there is no other supported path. The admin has no way to allowlist the router for specific users only; `allowedSwapper[pool][router] = true` is a single boolean that opens the gate to everyone. The scenario is a natural operational trap for any curated pool that also wants router support. The attack requires no special privileges, no capital beyond the swap amount, and is repeatable indefinitely.

## Recommendation
The extension must gate the **original user**, not the intermediary. The most robust fix is to pass the originator through the router: add an `originator` field to the swap call or extension data that the router populates with `msg.sender`, and have the extension verify it. This requires a coordinated change to the router and extension interface. Alternatively, document that the router must never be allowlisted on curated pools and add a revert in the extension if `sender` is a known router address.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true        // alice is the intended user
  allowedSwapper[pool][router] = true       // admin allowlists router for alice's UX

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls (MetricOmmSimpleRouter.sol L72-80):
    pool.swap(bob, zeroForOne, amount, ...)   // msg.sender = router

  pool calls (MetricOmmPool.sol L230-231):
    extension.beforeSwap(router, bob, ...)   // sender = router

  extension checks (SwapAllowlistExtension.sol L37):
    allowedSwapper[pool][router] == true     // passes!

  bob's swap executes in the curated pool despite not being allowlisted.
```