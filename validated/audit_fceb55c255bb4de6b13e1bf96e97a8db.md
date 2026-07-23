Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the end-user's address, making the allowlist bypassable via the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which the pool sets to `msg.sender` of the `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This produces two causally linked failures: allowlisted users cannot swap through the router, and if the admin allowlists the router to fix that, every user — including non-allowlisted ones — can bypass the curated-pool gate.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that value against the allowlist: [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract: [3](#0-2) 

So `sender` in `beforeSwap` is the router address, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]` — the wrong actor. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the economically relevant actor explicitly passed by the caller), not `sender` (the immediate pool caller): [4](#0-3) 

The swap extension has no equivalent "end-user" parameter, so it falls back to the router's address. No existing guard compensates for this: the allowlist mapping is keyed solely on the `sender` argument, and there is no mechanism to recover the originating user's address from the call chain.

## Impact Explanation
Two concrete impacts, both reachable by an unprivileged trader:

1. **Broken core swap functionality**: Allowlisted users cannot swap through `MetricOmmSimpleRouter` at all. The extension sees `sender = router`, which is not allowlisted, and reverts `NotAllowedToSwap`. The supported periphery swap path is completely unusable for the intended user set — a direct loss of core pool functionality.

2. **Allowlist bypass / admin-boundary break**: The natural corrective action — allowlisting the router — causes the extension to pass for *any* caller who routes through the router, including non-allowlisted users. A curated pool intended to restrict counterparties is fully open to arbitrary traders, violating the pool's access-control invariant and exposing LP positions sized for a controlled set of counterparties to unrestricted trading.

Both impacts fall within the allowed gate: broken core pool functionality and admin-boundary bypass by an unprivileged path.

## Likelihood Explanation
The broken-path consequence is immediate and requires no admin error — it is a direct, deterministic result of the wrong-actor binding whenever any allowlisted user attempts to swap via the router. The bypass path requires the admin to allowlist the router, which is the natural and expected corrective action after discovering the broken path. The two outcomes are causally linked: fixing the usability problem creates the security failure. No special attacker capability is required beyond calling the public router.

## Recommendation
Pass the originating user's address through the swap path so the extension can check the correct actor. One approach: add a `swapper` field to `extensionData` that the router populates with `msg.sender` before calling the pool, and have the extension decode and verify it (with a fallback to `sender` for direct pool calls). A cleaner alternative is to add a dedicated `originator` parameter to `beforeSwap` (analogous to `owner` in `beforeAddLiquidity`) that the pool sets to the address the admin intends to gate. Until a fix is deployed, pool admins must be warned that `SwapAllowlistExtension` cannot safely coexist with the router on a curated pool.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true).
   → alice is the only allowlisted swapper.

3. alice calls router.exactInputSingle({pool: pool, ...}).
   → router calls pool.swap(recipient, ...) — msg.sender at pool = router.
   → _beforeSwap(msg.sender=router, ...) is called.
   → extension checks allowedSwapper[pool][router] → false → REVERT NotAllowedToSwap.
   → alice cannot use the router despite being allowlisted. (broken functionality)

4. Admin, to fix alice's problem, calls setAllowedToSwap(pool, router, true).

5. charlie (not allowlisted) calls router.exactInputSingle({pool: pool, ...}).
   → router calls pool.swap(recipient, ...) — msg.sender at pool = router.
   → extension checks allowedSwapper[pool][router] → true → PASS.
   → charlie swaps successfully in a pool that was supposed to be restricted. (bypass)
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
