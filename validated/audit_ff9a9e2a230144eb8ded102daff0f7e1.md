Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool binds to `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, `msg.sender` inside the pool is the router contract, not the originating user. A pool admin who allowlists the router to enable standard periphery flows simultaneously grants every unprivileged address unrestricted access to the curated pool.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router itself `msg.sender` inside the pool: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. Since the router is a permissionless public contract with no access controls, any address can call it and inherit the router's allowlist status.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the position owner supplied by the caller), which is the economically relevant actor: [6](#0-5) 

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (the natural operational step to let allowlisted users trade via the standard periphery) simultaneously opens the pool to every unprivileged address. Any user can call `router.exactInputSingle` or `router.exactInput` targeting the restricted pool and receive oracle-priced output. The allowlist provides zero protection once the router is whitelisted, exposing LP assets to unrestricted counterparties — a direct loss of funds for LPs who deposited under the assumption of a curated counterparty set.

## Likelihood Explanation
The bypass is conditional on the pool admin having allowlisted the router address. This is the expected operational step for any curated pool that intends to support the standard periphery flow. A pool admin who allowlists only individual user addresses (not the router) avoids the bypass, but those users cannot use the router at all — a usability constraint that pushes admins toward allowlisting the router. The router is a deployed, immutable, public contract with no access controls of its own, making the bypass trivially repeatable by any address.

## Recommendation
The extension must gate the ultimate economic actor, not the immediate `pool.swap()` caller. Two sound approaches:

1. **Pass the originating user through `extensionData`**: the router encodes `msg.sender` into the per-hop `extensionData`; the extension decodes and verifies it. The pool admin allowlists user addresses, not the router. The extension must validate that the data is not spoofable (e.g., by requiring the pool to authenticate the router as the source).
2. **Separate `sender` from `swapper`**: add a dedicated `swapper` field to the swap interface that the router populates with the original `msg.sender`, distinct from the callback-payer identity already tracked in transient storage.

Either approach must ensure the extension-visible identity cannot be spoofed by the caller supplying arbitrary `extensionData`.

## Proof of Concept
```
Setup
─────
1. Pool deployed with SwapAllowlistExtension.
2. Pool admin allowlists two addresses:
     swapExtension.setAllowedToSwap(pool, alice, true);   // intended user
     swapExtension.setAllowedToSwap(pool, router, true);  // to enable router path for alice
3. Bob (not allowlisted) holds token0.

Attack
──────
4. Bob calls:
     router.exactInputSingle(ExactInputSingleParams{
         pool:       pool,
         recipient:  bob,
         zeroForOne: true,
         amountIn:   X,
         ...
     });
5. Router calls pool.swap(bob, true, X, ...).
   Inside pool.swap(): msg.sender == router.
6. Pool calls _beforeSwap(sender=router, ...).
7. Extension evaluates:
     allowedSwapper[pool][router]  →  true   ✓
   Swap proceeds; Bob receives oracle-priced token1.

Expected: revert NotAllowedToSwap (Bob is not allowlisted).
Actual:   swap executes; allowlist is bypassed.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
