Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` of the `pool.swap()` call. When end users route through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated access inadvertently opens the pool to every caller of the router, because the extension cannot distinguish between individual users.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly — the router is `msg.sender` of that call, so `sender` delivered to the extension is the router address, not the end user: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

When a pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated access for approved users, `allowedSwapper[pool][router] == true` satisfies the check for every caller of the router — including users who were never individually approved. No existing guard in the extension or pool distinguishes the original end user from the direct `pool.swap()` caller.

## Impact Explanation
**Allowlist bypass (primary):** Any unprivileged address can call `exactInputSingle` (or any other router swap function) on `MetricOmmSimpleRouter` and swap in a pool that was intended to be restricted. If the pool holds concentrated liquidity at oracle-anchored prices accessible only to approved counterparties, unauthorized swappers can extract value at those prices, directly harming LPs — a direct loss of LP principal meeting Critical/High Sherlock thresholds.

**DoS (secondary):** If the admin allowlists individual users but not the router, those users are blocked from using the router even though they are approved, breaking the core swap flow for all allowlisted users.

## Likelihood Explanation
The scenario is reachable by any unprivileged user with no special setup. `MetricOmmSimpleRouter` is a public periphery contract. A pool admin who configures a per-user allowlist and then enables router access — a natural operational step — triggers the bypass automatically. No privileged access, flash loans, or special conditions are required beyond calling a public router function.

## Recommendation
Pass the original end user through the call chain rather than the direct caller of `pool.swap()`. Preferred approach: add an optional `originator` field to the swap call that the pool passes to extensions alongside `sender`. The router sets `originator = msg.sender` (the end user); direct callers leave it as `address(0)` (falling back to `sender`). `SwapAllowlistExtension.beforeSwap` then checks `originator` when non-zero. Alternatively, document that `sender` is the direct pool caller and provide a separate per-user gating mechanism inside the router itself.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured on `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, userA, true)` — intending only `userA` to swap.
3. Admin calls `setAllowedToSwap(pool, router, true)` — intending to let `userA` use the router.
4. `userB` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. `userB`'s swap executes successfully despite never being allowlisted, extracting tokens at oracle-anchored prices at the expense of LPs.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
