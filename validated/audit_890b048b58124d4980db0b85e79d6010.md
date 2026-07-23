Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual end user, making the allowlist bypassable for all router-mediated swaps — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the pool to every user who routes through the same public router, defeating the allowlist's purpose entirely.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value just described: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same holds for `exactInput` (all hops): [5](#0-4) 

And for `exactOutputSingle` and the recursive `exactOutput` hops: [6](#0-5) [7](#0-6) 

There is no `originSender` field, no trusted-router mechanism, and no on-chain mitigation anywhere in the codebase. The design creates an irreconcilable conflict: allowlisting specific users blocks them from using the router (router not listed), while allowlisting the router opens the pool to every user who calls through it.

The existing integration test exercises the allowlist only through direct pool calls via `TestCaller.swap` → `pool.swap()`, never through the router, so the bypass is not covered: [8](#0-7) 

## Impact Explanation

**High.** A pool admin who allowlists the router address to enable router-mediated swaps for curated users opens the pool to every user who calls through the same public `MetricOmmSimpleRouter`. Non-allowlisted users can execute live swaps against LP funds on a pool designed to restrict trading to a specific set of addresses. LP principal is directly at risk from trades the pool's curation policy was intended to prevent. This is a direct loss of LP assets above Sherlock thresholds, matching the "broken core pool functionality causing loss of funds" and "admin-boundary break" allowed impact categories.

## Likelihood Explanation

**Medium.** `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who deploys a `SwapAllowlistExtension` and wants allowlisted users to use the router will naturally allowlist the router address. The misconfiguration is a predictable consequence of the design: there is no documented warning, no on-chain enforcement preventing this configuration, and `isAllowedToSwap` does not expose the distinction between direct and router-mediated paths.

## Recommendation

Pass the actual end user's address through the swap path so the allowlist can gate the economically relevant actor. One approach: add an optional `originSender` field to `extensionData` that the router populates with `msg.sender` before calling the pool. The extension can then verify that field, with the pool enforcing that only a registered router may set it. Alternatively, document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level by rejecting pools that configure both.

## Proof of Concept

```
1. Deploy a pool with SwapAllowlistExtension as a beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — intent: let allowlisted users reach the pool via the router.
3. charlie (not individually allowlisted) calls:
     MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
4. Router calls pool.swap(recipient, ...) — msg.sender = router.
5. Pool calls _beforeSwap(sender=router, ...).
6. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
7. charlie's swap executes against LP funds despite never being allowlisted.
```

Foundry test plan: fork the existing `FullMetricExtensionTest`, deploy `MetricOmmSimpleRouter`, call `swapExtension.setAllowedToSwap(address(pool), address(router), true)`, then call `router.exactInputSingle` from an address that is not individually allowlisted and assert the swap succeeds (demonstrating the bypass). Confirm that calling `pool.swap` directly from the same non-allowlisted address reverts with `NotAllowedToSwap`.

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-74)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }

  function test_blocksDepositWhenDepositorNotAllowed() public {
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
    _addLiquidity(0, -5, 4, 10_000, EXTENSION_TEST_SALT);
  }

  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
