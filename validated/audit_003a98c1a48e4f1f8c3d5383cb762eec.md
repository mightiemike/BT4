Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument passed by the pool, which is the pool's own `msg.sender` — the router contract when a swap is routed through `MetricOmmSimpleRouter`. If a pool admin allowlists the router so that legitimate users can swap via the standard periphery entry point, any unprivileged address can route through the same public router and pass the allowlist check, because the check sees the router address rather than the originating user. The per-user allowlist invariant is silently voided for every pool that allowlists the router.

## Finding Description
**Root cause — `MetricOmmPool.swap` passes its own `msg.sender` as `sender`:**

In `MetricOmmPool.swap`, the pool calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
_beforeSwap(
  msg.sender,   // ← this is the router when called via MetricOmmSimpleRouter
  recipient,
  ...
);
``` [1](#0-0) 

**`ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the router:**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` at the pool level, and never encodes the originating user into the call:**

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64,
    "",
    params.extensionData   // ← originating user address is NOT forwarded here
  );
``` [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — none of them encode the originating `msg.sender` into `extensionData` or any other field that the pool passes to extensions. [5](#0-4) 

**Exploit call chain:**
```
Bob (not allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(...)   [msg.sender = Bob]
      → MetricOmmPool.swap(recipient, ...)         [msg.sender = router]
          → _beforeSwap(sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  checks: allowedSwapper[pool][router] → true
                  Bob's swap executes despite never being allowlisted
```

No existing guard prevents this. The `setAllowedToSwap` setter correctly stores per-address permissions, but the check is applied to the wrong address (the router) at call time. [6](#0-5) 

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a named set of counterparties (e.g., KYC'd addresses, specific market makers). To allow those addresses to use the canonical periphery router, the admin must add the router to the allowlist. Once the router is allowlisted, **every** address — including addresses the admin explicitly never allowlisted — can call any of the router's swap functions and the extension check passes. The allowlist is completely voided for all router-mediated paths. Unauthorized traders can execute swaps against oracle-anchored prices configured for a restricted counterparty set, extracting value from LP positions deposited under the assumption of a curated trading environment. This constitutes a broken core pool functionality (the allowlist extension) causing potential loss of funds to LPs.

## Likelihood Explanation
The router (`MetricOmmSimpleRouter`) is the canonical, documented periphery entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router — rather than requiring direct pool calls — will add the router to the allowlist. This is the natural and expected operational pattern. The bypass activates under normal, non-adversarial admin configuration. No special attacker capability is required beyond calling a public router function.

## Recommendation
Pass the originating user through the swap path so the extension can gate on the economically relevant actor:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the originating `msg.sender` into `extensionData` (e.g., ABI-encode it as a prefix). Have `SwapAllowlistExtension.beforeSwap` decode and check it when present, falling back to `sender` for direct pool calls.
2. **Pool-side**: Add an optional `originator` field to the `swap` signature or use a transient-storage slot set by the router before calling the pool, so the pool can forward the true initiator to extensions.

Either way, the allowlist check must key on the address that controls the economic decision to swap, not the intermediate contract that relays the call.

## Proof of Concept
```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  5. Router calls pool.swap(...) — msg.sender at pool = router
  6. Pool calls _beforeSwap(sender=router, ...)
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  8. Bob's swap executes successfully despite never being allowlisted

Verification: call isAllowedToSwap(pool, bob) → false, yet Bob's swap succeeded.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, configure allowlist with only `alice` and `router`, then call `router.exactInputSingle` from `bob` and assert the swap succeeds (no `NotAllowedToSwap` revert).

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
