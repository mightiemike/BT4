Audit Report

## Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Non-Allowlisted Users Can Swap on Restricted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is the pool's `msg.sender` at the time `swap()` is called. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router to enable router-based swaps for legitimate users simultaneously opens the allowlist to every caller of the public, permissionless router.

## Finding Description

`SwapAllowlistExtension.beforeSwap` gates swaps using:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is whatever address the pool received as `msg.sender` when `swap()` was invoked. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` directly as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly without encoding the original caller's identity anywhere the extension can verify:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // extension data is caller-supplied; not authenticated
    );
``` [3](#0-2) 

The pool therefore receives `msg.sender = router`, and the extension evaluates `allowedSwapper[pool][router]`. The `extensionData` field is passed through but `SwapAllowlistExtension.beforeSwap` ignores it entirely — the function signature accepts `bytes calldata` but never decodes it. [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` with `msg.sender = router`. [5](#0-4) 

## Impact Explanation

`SwapAllowlistExtension` is the sole on-chain mechanism for restricting swap access to a pool. Once the router is allowlisted, the guard is entirely ineffective: any address can call the public router and execute swaps against restricted liquidity. This constitutes broken core pool functionality — the allowlist invariant the admin configured is silently violated, LP funds are consumed by swappers the pool was explicitly designed to exclude, and any compliance or counterparty-restriction guarantees the pool was meant to enforce are nullified. This matches the "Broken core pool functionality causing loss of funds or unusable swap flows" impact gate.

## Likelihood Explanation

No special privileges are required. `MetricOmmSimpleRouter` is a public, permissionless contract. The only precondition is that the pool admin has allowlisted the router address, which is the natural and expected admin action to enable router-based swaps for legitimate users. The admin has no alternative: there is no mechanism in the extension to simultaneously permit allowlisted users to use the router and block non-allowlisted users from doing the same. The condition is therefore expected to be met in any production deployment that intends to support router-based swaps on a restricted pool.

## Recommendation

The extension must verify the actual end-user identity, not the intermediary. The cleanest fix is to have the router encode `msg.sender` into `extensionData` before calling the pool, and have `SwapAllowlistExtension.beforeSwap` decode and check it when the caller (`sender`) is a known, trusted router address. This requires the extension to maintain a registry of trusted routers. Alternatively, document that allowlisted pools must not allowlist the router and that allowlisted users must call `pool.swap()` directly — but this is a severe usability restriction and does not scale.

## Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     // allowlist the router so legitimate users can use it
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  4. attacker calls router.exactInputSingle({pool: pool, ...})
  5. router calls pool.swap(recipient, ...)   [msg.sender = router]
  6. pool calls _beforeSwap(router, ...)
  7. extension evaluates: allowedSwapper[pool][router] → true ✓
  8. swap executes; attacker is not individually allowlisted but bypassed the guard

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```

A Foundry integration test can reproduce this by deploying the pool with `SwapAllowlistExtension`, calling `setAllowedToSwap(pool, address(router), true)`, then calling `router.exactInputSingle` from an address not in the allowlist and asserting the swap succeeds rather than reverting.

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
