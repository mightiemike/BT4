Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which is the pool's `msg.sender`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the originating user. This makes it impossible to simultaneously allow allowlisted users to trade through the router and block non-allowlisted users: either the router is allowlisted (any user bypasses the gate) or it is not (all router users, including legitimate ones, are blocked).

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` uses that value (`sender`) as the identity to gate, checking `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the pool's caller: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [3](#0-2) 

The originating user's address (`msg.sender` to the router, stored in transient storage via `_setNextCallbackContext`) is never forwarded to the pool or the extension. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][user]`. No existing guard in the extension or pool recovers the true caller.

The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap(...)` directly from the router. [4](#0-3) 

## Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd users, institutional market-makers, whitelisted addresses) loses that protection entirely for router-mediated swaps. If the admin allowlists the router so that legitimate users can trade through it, every non-allowlisted user can call `exactInputSingle` and bypass the gate. The allowlist invariant — "only approved addresses may swap" — is broken for the standard periphery entry point. This constitutes a broken core pool functionality causing direct policy bypass on curated pools, matching the allowed impact gate for broken core pool functionality.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented periphery contract for swaps. No privileged access, special tokens, or multi-step setup is required. A single `exactInputSingle` call from any EOA suffices. Any user who observes that a pool uses `SwapAllowlistExtension` and that the router is allowlisted can trivially exploit this.

## Recommendation

The extension must check the originating user, not the intermediary. The cleanest fix is for the router to encode `msg.sender` into `extensionData` before forwarding to the pool, and for `SwapAllowlistExtension.beforeSwap` to decode and gate on that address rather than the raw `sender` parameter. This requires a convention between the router and the extension. Alternatively, document and enforce that `SwapAllowlistExtension` is incompatible with router-mediated swaps (e.g., a pool flag that prevents the router from calling the pool), but this is a breaking UX constraint.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension`. Only `alice` is allowlisted: `allowedSwapper[pool][alice] = true`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that `alice` can use the router (otherwise `alice`'s router swaps would also revert).
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. The router calls `pool.swap(recipient, ...)` — `msg.sender` to the pool is the router contract.
5. The pool calls `_beforeSwap(router, ...)`.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. `bob`'s swap executes on the supposedly curated pool, bypassing the allowlist entirely.

A Foundry integration test can reproduce this by: deploying the pool with the extension, allowlisting `alice` and the router, then calling `exactInputSingle` from `bob`'s address and asserting the swap succeeds (no `NotAllowedToSwap` revert).

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
