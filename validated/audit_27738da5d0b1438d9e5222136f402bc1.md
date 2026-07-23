Audit Report

## Title
`SwapAllowlistExtension` allowlist bypass via router intermediary — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address. Allowlisting the router — the only way to enable router-based swaps for legitimate users — simultaneously grants unrestricted swap access to every address that calls the router, defeating the per-user allowlist entirely.

## Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool (correct — the pool calls the extension). `sender` is the first argument forwarded by the pool, which is `msg.sender` captured inside `MetricOmmPool.swap()`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // direct caller of pool.swap(), not the end-user
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly without encoding the originating user into `extensionData`:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // user's msg.sender is NOT encoded here
    );
```

At this point `msg.sender` inside the pool is the **router**, so `sender` forwarded to the extension is the **router address**, not the originating user. The allowlist check becomes `allowedSwapper[pool][router]`.

The structural problem: if the admin allowlists only individual users (A, B, C) but not the router, those users' router calls will revert because `allowedSwapper[pool][router]` is `false`. To enable router-based swaps for legitimate users, the admin must allowlist the router itself. Once the router is allowlisted, `allowedSwapper[pool][router] == true` for every caller of the router, including non-allowlisted users.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner explicitly passed to `addLiquidity`), which identifies the beneficiary regardless of who calls the pool:

```solidity
// DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

No analogous identity-preserving parameter exists in the swap path.

## Impact Explanation

`SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access to specific addresses (e.g., KYC'd, institutional, or whitelisted counterparties). Once the router is allowlisted — the only practical way to enable router-based swaps for legitimate users — the guard is rendered ineffective for all router callers. Any address can execute swaps in a pool that is supposed to be access-controlled. If the pool carries favorable pricing or deep liquidity intended only for restricted participants, unrestricted access enables unauthorized arbitrage or extraction of LP funds, constituting a direct loss of LP principal and a broken core pool invariant. This meets the "Broken core pool functionality causing loss of funds" and "Admin-boundary break bypassed by an unprivileged path" criteria.

## Likelihood Explanation

The required condition is that the pool admin allowlists the router, which is the natural and expected configuration for any pool that wants to support the standard periphery. There is no additional precondition: any user with knowledge of the pool address can call the router and bypass the guard. The condition is not adversarial — it is the normal operational setup. Likelihood is high whenever the router is allowlisted.

## Recommendation

The extension must check the actual end-user, not the intermediary caller.

**Option A — Pass the originating user through `extensionData`** (complete fix): Have the router encode `msg.sender` into `extensionData`, and have the extension decode and check it. This requires a coordinated change in both the router and the extension, and mirrors the `_msgSender()` pattern.

**Option B — Check `recipient` instead of `sender`** (partial fix; recipient may differ from payer in some flows):
```diff
- if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
+ if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
```

**Option C — Architectural fix**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and allowlisted users must call the pool directly. This is operationally fragile and not recommended.

Option A is the most robust fix.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is KYC'd
  allowedSwapper[pool][router] = true         // admin enables router-based swaps for alice

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: bob,
        ...
    })

  Router calls pool.swap(bob, ...) → msg.sender in pool = router
  Pool calls extension.beforeSwap(router, bob, ...)
  Check: allowedSwapper[pool][router] == true → PASSES
  Bob's swap executes in the restricted pool.

Foundry test outline:
  1. Deploy SwapAllowlistExtension, MetricOmmPool with extension configured.
  2. setAllowedToSwap(pool, alice, true)
  3. setAllowedToSwap(pool, router, true)
  4. vm.prank(bob); router.exactInputSingle({pool: pool, ...})
  5. Assert swap succeeds (no NotAllowedToSwap revert).
```