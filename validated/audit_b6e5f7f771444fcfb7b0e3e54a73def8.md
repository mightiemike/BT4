Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` gates the router intermediary instead of the end user, allowing any unprivileged caller to bypass per-pool swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. A pool admin who allowlists the router — the natural configuration for any allowlisted pool that wants users to access periphery conveniences — inadvertently grants every caller of the router unrestricted swap access, bypassing the individual allowlist entirely.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- always the immediate caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap()` forwards this value unchanged to the extension's `beforeSwap()` hook. The extension then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router when the call originates from `MetricOmmSimpleRouter.exactInputSingle()`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
  );
```

The pool records `msg.sender = router` and passes it as `sender` to the extension. The extension therefore evaluates `allowedSwapper[pool][router]`, not the identity of the end user.

**The bypass:** A pool admin who wants allowlisted users to use the router must call `setAllowedToSwap(pool, router, true)`. Once set, `allowedSwapper[pool][router] == true` for every call arriving through the router — regardless of who the actual caller is. Any unprivileged address can call `router.exactInputSingle()` and the extension passes.

**Contrast with `DepositAllowlistExtension`:** The deposit allowlist correctly gates the position `owner` (the economic beneficiary), not `sender` (the intermediary), because `addLiquidity()` accepts an explicit `owner` parameter that the `MetricOmmPoolLiquidityAdder` populates with the real user. No equivalent "on-behalf-of" field exists on the swap path — `pool.swap()` has no `swapper` parameter separate from `msg.sender`.

## Impact Explanation

A pool admin who deploys a permissioned pool (e.g., for KYC'd counterparties, institutional LPs, or a closed market-making arrangement) and allowlists the router loses all swap-side access control. Any address on the network can swap against the pool's liquidity at oracle-derived prices. This constitutes a direct loss of LP principal whenever the oracle price differs from the market price — which is always the case during normal operation — as unauthorized arbitrageurs can extract value the pool admin explicitly tried to prevent. This meets the threshold for a High-severity direct loss of LP assets.

## Likelihood Explanation

Allowlisting the router is the natural and expected configuration for any permissioned pool that wants allowlisted users to benefit from the router's slippage protection, deadline checks, and multi-hop routing. A pool admin who does not allowlist the router forces allowlisted users to call `pool.swap()` directly, losing all periphery conveniences. The bypass therefore activates under a common, well-motivated admin configuration and requires no special attacker capability beyond calling a public router function.

## Recommendation

The `SwapAllowlistExtension` should gate the `recipient` (the address receiving output tokens and the economic beneficiary of the swap) rather than `sender` (the intermediary). Alternatively, the pool interface should expose a dedicated "on-behalf-of" field for swaps — mirroring the `owner` parameter on `addLiquidity()` — so the extension always sees the real actor. A third option is for the router to encode the originating user's address in `extensionData` and for the extension to decode and verify it, though this requires the extension to trust the router's claim and is less clean than a protocol-level fix.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` attached to `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, userA, true)` and `setAllowedToSwap(pool, router, true)` so that `userA` can use the router.
3. `userB` (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: userB, ...})`.
4. Router calls `pool.swap(userB, zeroForOne, amount, ...)` — `msg.sender` at the pool is `router`.
5. Pool calls `_beforeSwap(sender=router, ...)` → extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
6. `userB`'s swap executes at oracle prices against the pool's LP liquidity, bypassing the allowlist entirely.

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist only `userA` and the router, then `vm.prank(userB)` call `router.exactInputSingle(...)` and assert no revert occurs and tokens transfer to `userB`.