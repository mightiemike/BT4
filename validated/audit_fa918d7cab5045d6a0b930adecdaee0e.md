Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool, which is the pool's `msg.sender` at the time `swap` is called. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool that allowlists the router (required for legitimate router-mediated swaps) therefore allows every address — including explicitly excluded ones — to bypass the per-user gate by routing through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (used as the mapping key) and `sender` is the first argument passed by the pool. The pool sets that argument to its own `msg.sender` at the time `swap` is called:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // becomes `sender` in the extension
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly:

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

At this call site, `msg.sender` to the pool is the router contract address. `ExtensionCalling._beforeSwap` faithfully forwards it:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

The extension then evaluates `allowedSwapper[pool][router]`. For any legitimate user to swap through the router, the pool admin must add the router to the allowlist. Once the router is allowlisted, every address — including addresses the admin explicitly excluded — can bypass the per-user gate simply by routing through the router. The `SwapAllowlistExtension` does not inspect `extensionData` and has no mechanism to recover the original end-user address.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-internal actors) is fully bypassable by any user who routes through `MetricOmmSimpleRouter`. The allowlist guard — the only access-control layer on the swap path — is rendered ineffective. Any non-allowlisted user can execute swaps, altering pool balances and LP positions in ways the pool admin explicitly prohibited. This is a direct break of the access-control invariant with fund-impacting consequences.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap interface. Any pool that uses `SwapAllowlistExtension` and also needs to support router-mediated swaps (the normal user flow) must allowlist the router, making the bypass trivially reachable by any unprivileged address. No special permissions, flash loans, or complex setup are required — a single call to `exactInputSingle` on the router suffices.

## Recommendation
The extension must gate the economic actor, not the immediate caller. The cleanest fix is for `SwapAllowlistExtension.beforeSwap` to decode and verify a user-supplied address from `extensionData` when `sender` is a known intermediary, or for the router to encode the end-user address (`msg.sender` at the router level) into `extensionData` so the extension can decode and check it. Alternatively, restrict allowlisted pools to direct-call-only access (no router intermediary) by checking that `sender` is not a contract, or maintain a separate registry of trusted routers and require them to attest the real user address.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` and does **not** allowlist `bob`.
3. Admin calls `setAllowedToSwap(pool, router, true)` so that `alice` can use the router.
4. `bob` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` — `msg.sender` to the pool is the router.
6. The pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → **passes**.
7. `bob`'s swap executes despite being explicitly excluded from the allowlist.