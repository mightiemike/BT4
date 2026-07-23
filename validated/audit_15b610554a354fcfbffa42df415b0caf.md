Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Per-User Swap Restrictions via Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address, not the end user's address. If the pool admin allowlists the router (a natural action when they want allowlisted users to be able to use the router), every unprivileged user can bypass the per-user restriction by routing through the router, executing swaps on a pool intended to be private.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check: [1](#0-0) 

Here `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`.

In `MetricOmmPool.swap()`, `msg.sender` (whoever called `pool.swap()`) is passed directly as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes this `sender` value and dispatches it to the extension unchanged: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle()` is called by an end user, the router calls `pool.swap()` with itself as `msg.sender`: [4](#0-3) 

The pool receives `msg.sender = router`, passes `sender = router` to `_beforeSwap`, and the extension checks `allowedSwapper[pool][router]`. The original end user's address is never visible to the extension — it is not forwarded anywhere in the call chain. The same issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

## Impact Explanation

Any unprivileged user can swap on a pool restricted to specific counterparties by calling `MetricOmmSimpleRouter.exactInputSingle()` (or any other router entry point). This constitutes direct LP fund loss: unauthorized traders extract value from LP bins at oracle-derived prices. Every swap the pool admin did not intend to permit drains LP principal through the spread and notional fee mechanism. The allowlist guard — the only mechanism for restricting swap access — is rendered ineffective for any pool that needs to support router-mediated swaps. This is a broken core pool functionality causing direct loss of user principal.

## Likelihood Explanation

The bypass requires the router to be allowlisted. This is a natural and expected admin action: a pool admin who wants to allow their allowlisted users to use the router will call `setAllowedToSwap(pool, router, true)`. The admin has no way to know this simultaneously opens the pool to all router users. The router is a public, permissionless contract deployed by the protocol itself, so allowlisting it is a reasonable and foreseeable configuration choice. No special attacker capability is required beyond calling a public router function.

## Recommendation

The `SwapAllowlistExtension` must check the original end user's address, not the router's address. Two approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` in `extensionData` and have the extension decode and verify it. The extension would then check `allowedSwapper[pool][decodedUser]` instead of `allowedSwapper[pool][sender]`.

2. **Dedicated router integration**: Have the router expose a `swapper()` view that returns the current end user (stored in transient storage at entry), and have the extension call `IMetricOmmSimpleRouter(sender).swapper()` when `sender` is a known router address.

Either approach ensures the allowlist gates the economically relevant actor, not the intermediary.

## Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin calls setAllowedToSwap(pool, userA, true)      // intended allowlisted user
  admin calls setAllowedToSwap(pool, router, true)     // to let userA use the router
  admin adds liquidity to the pool

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({
        pool: pool,
        zeroForOne: true,
        amountIn: 1000,
        recipient: userB,
        ...
    })

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=userB, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ← check passes
        → swap executes, userB receives tokens from LP bins

Result:
  userB successfully swaps on a pool they are not allowlisted for.
  LP funds are drained by an unauthorized counterparty.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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
