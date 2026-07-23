Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, that `msg.sender` is the router's address, not the originating user. A pool admin who allowlists the router to enable router-based access for their curated users inadvertently opens the pool to every user, completely defeating the per-user access-control gate.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the contract invoking the extension), and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which originates from `MetricOmmPool.swap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    ...
```

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        ...
    );
```

The full call chain is:

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...)          // msg.sender at pool = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the original user
```

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`. This creates two mutually exclusive failure modes:

| Router allowlisted? | Effect |
|---|---|
| No | Allowlisted users cannot use the router; swaps revert with `NotAllowedToSwap` even though they are individually permitted. |
| Yes | **Any** user bypasses the allowlist by routing through `MetricOmmSimpleRouter`; the allowlist is completely ineffective for all router-mediated swaps. |

The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with `msg.sender = router`.

## Impact Explanation
A curated pool with `SwapAllowlistExtension` is designed to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, whitelisted market makers). Once the router is allowlisted — the natural and expected configuration step for any curated pool accessible through the protocol's own periphery — any unpermissioned user can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool without restriction. The allowlist invariant — that only approved addresses may swap — is broken for all router-mediated flows, which is the primary supported public entrypoint for end users. This constitutes broken core pool functionality (access control) and an admin-boundary break reachable by an unprivileged caller.

## Likelihood Explanation
The trigger is a pool admin allowlisting the router, which is the natural and expected configuration for any curated pool meant to be accessible through the protocol's own periphery. There is no warning in the extension or the router that this combination defeats per-user gating. The pool admin acts in good faith; the code silently fails open. The condition is easily and repeatably reachable by any unprivileged user once the router is allowlisted.

## Recommendation
The `SwapAllowlistExtension` must gate on the originating user, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Extension-data forwarding**: The router encodes `msg.sender` (the originating user) into `extensionData` for each swap hop; the extension decodes and checks that address. This requires a convention between the router and the extension.
2. **Transient-storage `originalSender` field**: The pool or router exposes the originating user through a transient-storage slot (similar to how the router already stores the payer in `T_SLOT_PAY_PAYER`), and the extension reads it.

Either way, the extension must not treat the router as the actor being gated.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` attached to `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — `alice` is the only permitted swapper.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to let `alice` use the router.
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — `msg.sender` at the pool = router.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `bob`'s swap executes successfully against the curated pool, bypassing the allowlist entirely.