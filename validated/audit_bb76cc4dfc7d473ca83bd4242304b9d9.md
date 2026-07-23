Audit Report

## Title
`SwapAllowlistExtension` Per-User Allowlist Bypassed via Router `msg.sender` Substitution — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool always sets to its own `msg.sender` — the immediate caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router to support standard UX inadvertently grants every user — including non-allowlisted ones — the ability to bypass the per-user gate by calling any `exact*` function on the router.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check at:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct for the pool-keyed mapping), and `sender` is the first argument forwarded by the pool. `MetricOmmPool.swap` always sets that argument to its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly with an empty `extensionData`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // ← user-supplied, not router-injected identity
    );
```

The pool's `msg.sender` is now the router, so the extension receives `sender = router_address` and evaluates `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][end_user]`. The same substitution applies to `exactInput` (L104), `exactOutputSingle` (L136), `exactOutput` (L165), and the recursive `_exactOutputIterateCallback` path (L220-228) where the router calls `pool.swap(msg.sender, ...)` with `msg.sender` still being the router. In none of these paths does the router encode the original caller into `extensionData` — it passes `""` or the `ExactOutputIterateCallbackData` struct (which contains no `originator` field).

This creates an irreconcilable dilemma for any pool admin who wants both a per-user allowlist and router support:

- **If the router is allowlisted**: `allowedSwapper[pool][router] = true` → every user who calls the router passes the check, defeating the allowlist entirely.
- **If the router is not allowlisted**: allowlisted users cannot use the router at all, breaking the supported periphery path.

## Impact Explanation

A pool admin who deploys a curated pool (e.g., KYC-gated, institutional-only) with `SwapAllowlistExtension` and allowlists the router to support standard UX loses the entire per-user curation guarantee. Any unprivileged user can call `router.exactInputSingle()` and execute swaps on the curated pool. The pool's token balances change, LP positions are affected by price movement, and the curation policy — the only mechanism protecting LP funds from unauthorized counterparties — is silently nullified. This constitutes an admin-boundary break where an unprivileged path bypasses a pool admin's access control, with direct fund impact on LPs whose positions are exposed to unauthorized swap counterparties.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. The allowlist state is publicly readable on-chain via `allowedSwapper[pool][router]`. Any user who observes that the router is allowlisted can immediately exploit this with a single `exactInputSingle` call. No special privileges, flash loans, or multi-step setup are required. The condition (router allowlisted by pool admin) is the expected production configuration for any curated pool that intends to support normal UX.

## Recommendation

The extension must resolve the end user's identity rather than the immediate pool caller. The router should encode the original `msg.sender` into `extensionData` before calling `pool.swap()`, and the extension should decode and check that address when `sender` is a known trusted router:

1. **Router side**: Replace `""` / bare `extensionData` with `abi.encode(msg.sender, params.extensionData)` so the original caller is always forwarded.
2. **Extension side**: Maintain a `trustedRouter` mapping; when `sender` is a trusted router, decode `extensionData` to extract the real user address and check `allowedSwapper[pool][realUser]` instead.

This requires a coordinated convention between the router and the extension but closes the bypass without breaking router support.

## Proof of Concept

```
Setup:
  pool P configured with SwapAllowlistExtension E
  pool admin calls E.setAllowedToSwap(P, router, true)   // to enable router UX
  pool admin does NOT call E.setAllowedToSwap(P, attacker, true)

Attack:
  attacker calls router.exactInputSingle({pool: P, ...})
    → router calls P.swap(recipient, ...)          // MetricOmmSimpleRouter.sol L72
    → P calls _beforeSwap(msg.sender=router, ...)  // MetricOmmPool.sol L230-231
    → E.beforeSwap(sender=router, ...) is called   // SwapAllowlistExtension.sol L31
    → allowedSwapper[P][router] == true → check passes
    → swap executes for attacker
    → attacker receives output tokens from curated pool

Foundry test outline:
  1. Deploy pool P with SwapAllowlistExtension E attached.
  2. Admin calls E.setAllowedToSwap(P, address(router), true).
  3. Confirm attacker address is NOT in allowedSwapper[P].
  4. Prank as attacker; call router.exactInputSingle with pool=P.
  5. Assert swap succeeds (no NotAllowedToSwap revert) and attacker receives tokens.
```