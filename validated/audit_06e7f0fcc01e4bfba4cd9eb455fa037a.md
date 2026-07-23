Audit Report

## Title
SwapAllowlistExtension.beforeSwap gates the router address instead of the end user, allowing allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool — the immediate caller, not the end user. When swaps are routed through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address. A pool admin who allowlists the router (a natural step to let approved users access slippage protection and multi-hop routing) inadvertently grants every on-chain address the ability to bypass the swap allowlist entirely.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // pool's msg.sender — the router, not the end user
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that value:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, passing `""` as `extensionData` with no mechanism to forward the end user's identity:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., "", params.extensionData);
```

The call chain is: `User → router.exactInputSingle → pool.swap(msg.sender=router) → _beforeSwap(sender=router) → allowedSwapper[pool][router]`. The check passes for any caller as long as the router is allowlisted, regardless of who the end user is.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates `owner` (the second argument — the economically relevant actor), not `sender` (the immediate caller):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

This asymmetry confirms the design intent was to check the economically relevant actor; the swap extension deviates from this pattern.

## Impact Explanation

Once the router is allowlisted in `allowedSwapper[pool]`, the `beforeSwap` guard passes unconditionally for any address that calls `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` against that pool. The swap allowlist — the sole mechanism for restricting who may trade in the pool — is fully neutralized. Any unauthorized actor can execute swaps against a pool intended to be access-controlled, enabling unauthorized trading, potential draining of liquidity at oracle-derived prices, or front-running of LP positions. This constitutes a broken core pool functionality causing loss of funds and a broken access-control invariant.

## Likelihood Explanation

Pool admins who deploy a `SwapAllowlistExtension` pool and also want their approved users to benefit from the router's slippage protection and multi-hop routing will naturally call `setAllowedToSwap(pool, address(router), true)`. There is no documentation warning against this. The pattern of allowlisting a trusted router rather than every individual user is standard in DeFi. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges, no flash loans, and no multi-block setup — a single call to the router suffices.

## Recommendation

The extension must gate the actual end user, not the intermediate caller. Two sound approaches:

1. **Pass the originating user through `extensionData`**: Require the router to encode `msg.sender` (the end user) into `extensionData` and have the extension decode and verify that address. The pool admin configures the router as a trusted forwarder, and the extension verifies the forwarded identity.

2. **Check `tx.origin` as a fallback for router-mediated calls**: When `sender` is a known trusted router, fall back to `tx.origin`. This is acceptable in a non-meta-transaction context and is consistent with how Uniswap v3 periphery handles identity forwarding.

Either way, `SwapAllowlistExtension` must not treat the router address as the identity to gate.

## Proof of Concept

```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)           // alice is approved
  pool admin calls setAllowedToSwap(pool, address(router), true) // router added so alice can use it

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: restrictedPool,
        tokenIn: token0,
        ...
    })

  router calls pool.swap(...)  →  msg.sender = router
  pool calls _beforeSwap(sender=router, ...)
  SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  bob's swap executes successfully — allowlist bypassed
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
