Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing any user to bypass the per-pool swap allowlist via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist, where `sender` is `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router address, not the originating user. Any pool that allowlists the router to support normal UX inadvertently grants every user — including explicitly excluded ones — the ability to bypass the allowlist gate.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the allowlist check as follows:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. [1](#0-0) 

`MetricOmmPool.swap()` passes its own `msg.sender` directly as the `sender` argument to `_beforeSwap()`: [2](#0-1) 

In `MetricOmmSimpleRouter.exactInputSingle()`, the original user's address (`msg.sender`) is stored only in transient storage for the payment callback via `_setNextCallbackContext`. It is never forwarded to `pool.swap()`. The pool therefore receives `msg.sender = router`: [3](#0-2) 

The same substitution occurs in `exactInput` (all hops after the first use `address(this)` as payer, and every hop calls `pool.swap()` with `msg.sender = router`): [4](#0-3) 

And in `exactOutputSingle` and `exactOutput`, the router similarly calls `pool.swap()` directly, so the pool always sees `msg.sender = router`. [5](#0-4) 

The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. No existing guard in the extension or pool recovers the original caller's identity.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses. To allow those allowlisted users to use the standard router interface, the admin must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, every user — including those explicitly excluded — can bypass the gate by calling `router.exactInputSingle()`. The extension sees `sender = router` (allowlisted) and permits the swap. The pool admin faces an impossible choice: either allowlist the router (breaking the allowlist for everyone) or do not allowlist the router (making the router unusable for legitimate users). There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users. This directly exposes LP principal to unintended swap flows from unauthorized counterparties, constituting an admin-boundary break reachable by any unprivileged caller.

## Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` and expects users to interact via `MetricOmmSimpleRouter` is affected. The router is the primary supported periphery path. A pool admin who allowlists the router to enable normal UX inadvertently opens the gate to all users. The attacker requires no special privileges — a single `exactInputSingle` call suffices, and the attack is repeatable indefinitely.

## Recommendation
The `SwapAllowlistExtension` must gate on the economically relevant actor (the end user), not the immediate caller of `pool.swap()`. Concrete options:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension verifies it (requires a trust assumption on the router or a signature scheme).
2. **Separate identity proof for router calls**: The extension detects router-mediated calls and applies a different check (e.g., verifying a signed message from the actual user).
3. **Simplest safe fix**: Document and enforce that `SwapAllowlistExtension` is incompatible with the router; add a guard in the extension that reverts if `sender` is a known router address, forcing direct pool interaction only.

## Proof of Concept
```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin allowlists alice:  allowedSwapper[pool][alice] = true
3. Pool admin allowlists router: allowedSwapper[pool][router] = true
   (required so alice can use the standard router interface)
4. bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender to pool = router.
6. Pool calls extension.beforeSwap(router, ...) — msg.sender to extension = pool.
7. Extension checks: allowedSwapper[pool][router] == true → passes.
8. bob's swap executes successfully, bypassing the allowlist.
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
