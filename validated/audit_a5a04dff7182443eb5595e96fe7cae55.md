Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of user address, allowing any caller to bypass per-user swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the direct caller of `swap()`. When `MetricOmmSimpleRouter` is used, `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any allowlisted user to use the router), every caller of the router — including explicitly excluded addresses — passes the allowlist check, completely nullifying the per-user access control.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // always the direct caller of swap()
    recipient,
    ...
    extensionData
);
```

`SwapAllowlistExtension.beforeSwap()` checks that `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly without encoding the actual user into `extensionData`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // user-supplied, not router-injected user identity
    );
```

The same pattern applies to `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181) — all router entry points call `pool.swap()` with the router as `msg.sender`.

**Two cases arise:**
- **Case 1 — Router not allowlisted:** All router-mediated swaps revert with `NotAllowedToSwap`, even for individually allowlisted users. Allowlisted users cannot use the router at all.
- **Case 2 — Router allowlisted (necessary for Case 1 to be avoided):** The extension passes for every caller of the router, including addresses explicitly excluded from the allowlist. The per-user gate is completely bypassed.

There is no existing guard in `SwapAllowlistExtension` that decodes `extensionData` to recover the originating user, and the router does not inject the caller's identity into `extensionData`.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, institutional traders, or whitelisted market makers) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. A disallowed user can execute swaps against the pool's liquidity at oracle prices, extracting value that the pool admin intended to reserve for allowlisted participants. This is a direct loss of the pool's access-control invariant with fund-impacting consequences: unauthorized users trade at oracle-fair prices against LP capital that was meant to be protected. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
The likelihood is medium. It requires: (1) a pool deployed with `SwapAllowlistExtension` as a configured `beforeSwap` hook, and (2) the pool admin allowlisting the router — which is the only way to let allowlisted users use the router. Both conditions are the natural, expected production configuration for any curated pool that also wants to support the standard periphery router. `MetricOmmSimpleRouter` is the primary public swap entrypoint in the periphery, so the attack path is reachable by any user who reads the contracts.

## Recommendation
The `sender` forwarded to extensions must represent the economic actor, not the intermediary contract. Two complementary fixes:

1. **Router-side (preferred):** Have `MetricOmmSimpleRouter` encode `msg.sender` (the actual user) into `extensionData` before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap()` decode and check that address when present, falling back to `sender` for direct pool calls.

2. **Extension-side:** Redesign `SwapAllowlistExtension` to decode a user-supplied originator address from `extensionData` when `sender` is a known router, or extend the pool interface to carry an explicit `originator` field alongside `sender`.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, allowedUser, true)
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so allowedUser can use the router at all)

Attack:
  - disallowedUser calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes for disallowedUser at oracle price
  - disallowedUser receives output tokens; allowlist policy is bypassed

Call path:
disallowedUser → MetricOmmSimpleRouter.exactInputSingle()
    → pool.swap(recipient, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
            → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                → allowedSwapper[pool][router] == true → PASSES
        → swap executes
```