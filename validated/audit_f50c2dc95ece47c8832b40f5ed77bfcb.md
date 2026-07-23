Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user, allowing full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the original user. If the pool admin allowlists the router address (the natural action to support router-based swaps for permitted users), every caller of the router bypasses the per-pool allowlist regardless of their own allowlist status.

## Finding Description
In `MetricOmmPool.swap`, `_beforeSwap` is called with `msg.sender` as the `sender` argument: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` checks this `sender` value against the per-pool allowlist: [2](#0-1) 

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly, making itself `msg.sender` to the pool: [3](#0-2) 

The same pattern applies to `exactInput`: [4](#0-3) 

And to `exactOutputSingle` and `exactOutput`: [5](#0-4) [6](#0-5) 

The allowlist is keyed `allowedSwapper[pool][swapper]`: [7](#0-6) 

When the pool admin calls `setAllowedToSwap(pool, router, true)` to permit allowlisted users to trade via the router, the check `allowedSwapper[pool][router] == true` passes for every caller of the router. The extension has no mechanism to recover the original `msg.sender` from the router call; `extensionData` passed by the router is empty (`""`) in all four swap functions, and no router-identity registry exists in the extension.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC-verified counterparties, institutional traders, or protocol-internal actors) has its access control completely bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The pool admin's intended gate is rendered inoperative the moment the router is allowlisted. Any user can execute swaps in a pool designed to be closed to them, breaking the core access-control invariant and constituting broken core pool functionality with direct fund-flow consequences. Severity: **High**.

## Likelihood Explanation
The trigger requires only a single natural admin action: `setAllowedToSwap(pool, router, true)`. No special permissions, flash loans, or contract deployment are required by the attacker — a plain `exactInputSingle` call suffices. The condition is highly likely in any production deployment where permitted users are expected to use the standard router.

## Recommendation
Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should ABI-encode `msg.sender` (the original user) into `extensionData` on every `pool.swap` call so extensions can recover the true initiator.
2. **Extension-side (preferred)**: `SwapAllowlistExtension.beforeSwap` should maintain a registry of known router addresses; if `sender` is a registered router, decode the real initiator from `extensionData` and check that address against `allowedSwapper` instead. This preserves backward compatibility for direct pool calls.

Until fixed, pool admins must not allowlist the router address; permitted users must call `pool.swap` directly.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only alice is allowlisted.
// Admin also allowlists the router so alice can use it.
extension.setAllowedToSwap(pool, alice, true);
extension.setAllowedToSwap(pool, address(router), true); // natural admin action

// Attack: eve (not allowlisted) bypasses the gate via the router.
vm.prank(eve);
token0.approve(address(router), type(uint256).max);

vm.prank(eve);
// Succeeds: extension checks allowedSwapper[pool][router] == true
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:             address(pool),
    tokenIn:          address(token0),
    tokenOut:         address(token1),
    zeroForOne:       true,
    amountIn:         1000,
    amountOutMinimum: 0,
    recipient:        eve,
    deadline:         block.timestamp + 1,
    priceLimitX64:    0,
    extensionData:    ""
}));
// eve receives token1 despite never being allowlisted.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L165-181)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```
