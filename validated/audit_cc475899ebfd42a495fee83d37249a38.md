Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router to support router-mediated swaps for their curated users inadvertently opens the gate to every user, because the hook cannot distinguish which end user is behind the router call.

## Finding Description
In `MetricOmmPool.swap`, `msg.sender` is forwarded verbatim as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool: [3](#0-2) 

For multi-hop `exactInput`, intermediate hops use `address(this)` (the router) as payer, so the same substitution applies to every hop: [4](#0-3) 

**Exploit flow:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` and calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for their allowlisted users, and `setAllowedToSwap(pool, alice, true)` to allow alice directly.
2. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
3. The router calls `pool.swap()` — `msg.sender` at the pool is the router.
4. `_beforeSwap(sender = router, ...)` is called; `SwapAllowlistExtension` checks `allowedSwapper[pool][router]` → `true`.
5. The swap executes for Bob. The allowlist is completely bypassed.

There is no existing guard that recovers the original end user from the router call. The `extensionData` field passed by the router is empty (`""`) for both `exactInputSingle` and each hop of `exactInput`, so no user identity is forwarded. [5](#0-4) 

## Impact Explanation
Any user — including those the pool admin explicitly excluded — can bypass the swap allowlist by routing through `MetricOmmSimpleRouter`. The curated pool's access control is completely nullified for the router path. Disallowed users can execute swaps, consuming LP liquidity and extracting output tokens from a pool designed to restrict participation. This constitutes broken core pool functionality with direct fund-impacting consequences: LP assets are consumed by unauthorized traders, violating the pool's curation guarantee.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical periphery swap entry point. Pool admins who deploy curated pools and want their allowlisted users to benefit from multi-hop routing or the router's slippage/deadline helpers will naturally allowlist the router address. The bypass requires no special privileges — any user calls a public router function. The misconfiguration is the expected and obvious configuration for any pool that intends to support router-mediated swaps.

## Recommendation
The extension must recover the original end user rather than trusting the `sender` argument, which collapses to the router address for all router-mediated swaps. The preferred fix is to update `MetricOmmSimpleRouter` to embed `msg.sender` (the end user) in `extensionData` for every `pool.swap()` call, and update `SwapAllowlistExtension.beforeSwap` to decode the claimed user from `extensionData` when `sender` is a registered router, verifying against `allowedSwapper`. When `sender` is not a registered router, the existing `sender` check applies. This requires a router registry or a known-router flag in the extension's configuration.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin: setAllowedToSwap(pool, router, true)   // intends to allow router-mediated swaps
  pool admin: setAllowedToSwap(pool, alice, true)    // intends to allow alice
  pool admin: does NOT allowlist bob

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap()          // msg.sender = router
    → _beforeSwap(sender = router, ...) // sender is router, not bob
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
    → swap executes for bob             // allowlist bypassed
```

Foundry test: add a test to `FullMetricExtension.t.sol` that deploys `MetricOmmSimpleRouter`, allowlists the router address via `setAllowedToSwap`, then calls `router.exactInputSingle` from an address that is not individually allowlisted. The swap should succeed, demonstrating the bypass.

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
