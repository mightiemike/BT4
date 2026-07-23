Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the Real User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` from the pool's perspective. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router to enable normal router-based trading inadvertently opens the allowlist to every unprivileged user who calls the router.

## Finding Description
The call chain is confirmed by the production code:

1. `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [1](#0-0) 

2. `ExtensionCalling._beforeSwap` encodes that value verbatim and forwards it to every configured extension: [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router — not the originating user: [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original caller's identity — `extensionData` is passed through verbatim from the user's params with no router-injected sender: [4](#0-3) 

5. The same structural problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — every router entry point calls `pool.swap()` as `msg.sender = router`: [5](#0-4) 

This creates an inescapable catch-22: if the router is NOT allowlisted, allowlisted users cannot use the router at all. If the router IS allowlisted, the allowlist is completely bypassed — any address can call `router.exactInputSingle()` and the extension passes because `allowedSwapper[pool][router] == true`.

`DepositAllowlistExtension` does not share this flaw because it ignores `sender` and checks `owner` (the position owner), which is correctly preserved through the liquidity adder path: [6](#0-5) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise curated addresses is fully open to any user who routes through `MetricOmmSimpleRouter`. The attacker receives the same swap output as an allowlisted user, paying the same fees, with no additional cost beyond gas. LP funds in the pool are exposed to unrestricted trading, defeating the entire purpose of the allowlist. This constitutes broken core pool functionality (allowlist-gated swap) with direct fund-impact consequences (LP assets exposed to disallowed counterparties).

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface deployed alongside the protocol. Any pool admin who wants allowlisted users to trade normally will allowlist the router — the exact configuration that opens the bypass. The attacker needs no special privileges, no flash loan, and no multi-step setup: a single call to `exactInputSingle` suffices.

## Recommendation
The `SwapAllowlistExtension` must gate on the original user, not the intermediary router. The router should encode `msg.sender` into `extensionData` before calling `pool.swap()`, and the extension should decode it:

```solidity
// In SwapAllowlistExtension.beforeSwap:
address effectiveSender = extensionData.length >= 20
    ? abi.decode(extensionData, (address))
    : sender;
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][effectiveSender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

This requires a coordinated change in the router (encode `msg.sender` into `extensionData`) and the extension (decode and check that address instead of `sender`).

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Pool admin allowlists the router so allowedUser can trade normally.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (not allowlisted) bypasses the allowlist via the router:
vm.prank(attacker);  // attacker is NOT in allowedSwapper[pool]
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Swap succeeds. SwapAllowlistExtension checked allowedSwapper[pool][router] == true.
// The attacker receives token1 output. Allowlist is fully bypassed.
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
