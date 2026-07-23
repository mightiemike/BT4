Audit Report

## Title
`SwapAllowlistExtension` allowlist fully bypassed when router is allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the end user. If the pool admin allowlists the router to support router-mediated swaps for permitted users, every user — including non-allowlisted ones — can bypass the allowlist entirely by routing through the router.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, forwarding the direct caller of `pool.swap` as the `sender` argument: [1](#0-0) 

**Step 2 — Router calls the pool directly; the pool sees the router as `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` with no mechanism to forward the original caller's address. The original caller (`msg.sender`) is stored only in transient storage for the payment callback, never passed to the pool: [2](#0-1) 

The same applies to `exactInput` (all hops): [3](#0-2) 

And `exactOutputSingle` and `exactOutput` (all hops via `_exactOutputIterateCallback`): [4](#0-3) [5](#0-4) 

**Step 3 — The extension checks the router address, not the end user.**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [6](#0-5) 

There is no way for the extension to recover the actual end user's address — the router stores the payer in transient storage for the callback but never passes it to `pool.swap`. The two failure modes are mutually exclusive: if the router is not allowlisted, allowlisted users cannot use the router at all; if the router is allowlisted, every user bypasses the allowlist.

## Impact Explanation

A non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle` targeting a pool configured with `SwapAllowlistExtension`. The pool's `_beforeSwap` passes `msg.sender = router` as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`. If the router is allowlisted (the natural configuration for a pool that wants to support router-mediated swaps for permitted users), the check passes and the swap executes — the allowlist is completely bypassed. The user receives output tokens from a pool they were not permitted to access, violating KYC gates, sanctions screens, or restricted LP pool policies. This constitutes a broken core pool access-control mechanism causing unauthorized fund flows.

## Likelihood Explanation

The router is the canonical periphery entry point documented and expected to be used by end users. A pool admin who configures a `SwapAllowlistExtension` and also wants to support router-mediated swaps for their permitted users will naturally allowlist the router address — this is the expected, intuitive configuration. The bypass is reachable through normal, documented usage of the protocol by any unprivileged user with no special preconditions beyond the router being allowlisted.

## Recommendation

The extension must gate the actual end user, not the intermediary. The cleanest fix is to add an `originator` parameter to the pool's `swap` signature (or a separate periphery-level forwarding mechanism) so the router can pass `msg.sender` as the originator, and the extension checks `allowedSwapper[pool][originator]` instead of `allowedSwapper[pool][sender]`. Alternatively, document that the router must never be allowlisted and that allowlisted users must call the pool directly — but this breaks router usability for curated pools.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin enables router-mediated swaps
  allowedSwapper[pool][alice] = true    // alice is a permitted user
  allowedSwapper[pool][bob] = false     // bob is NOT permitted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient=bob, ...)           // msg.sender = router
  → pool calls _beforeSwap(sender=router, ...)
  → SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] == true
  → check passes, swap executes
  → bob receives output tokens from a pool he is not permitted to access

Result:
  The allowlist is bypassed. Bob swaps successfully despite not being allowlisted.
  Applies equally to exactInput, exactOutputSingle, and exactOutput.
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
