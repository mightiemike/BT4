Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of real user, bypassing per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the direct caller of the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router to enable router-mediated swaps for approved users simultaneously grants every user on the router the ability to swap, rendering the per-user allowlist completely ineffective for the router path.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` gates on that `sender` argument: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly without forwarding the original user's address — `msg.sender` at the pool level is the router: [4](#0-3) 

The same applies to `exactOutputSingle`: [5](#0-4) 

And every hop of `exactInput`: [6](#0-5) 

And every hop of `exactOutput` (recursive callback path): [7](#0-6) 

The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][real_user]`. There is no mechanism in the router to encode the originating user into `extensionData` or any other field that the extension could verify. The `DepositAllowlistExtension` avoids this problem because `addLiquidity` takes an explicit `owner` parameter that is passed separately from `msg.sender`, so the extension can check the real LP owner: [8](#0-7) 

No analogous real-user field exists in the swap hook interface.

## Impact Explanation
A pool admin deploying a curated pool with `SwapAllowlistExtension` must allowlist the router address to permit any router-mediated swap for approved users. This single allowlist entry grants every user on the publicly deployed `MetricOmmSimpleRouter` the ability to swap on the pool, regardless of whether they are individually approved. Any non-allowlisted address can call `router.exactInputSingle(...)` and trade on a pool intended to be restricted, accessing oracle-anchored liquidity that the pool admin expected only approved counterparties to reach. This constitutes broken core pool access-control functionality causing potential direct loss of LP assets at oracle-anchored prices.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is the standard publicly deployed periphery entry point callable by any address. The vulnerable configuration — allowlisting the router — is the only way to permit router-mediated swaps for approved users, making it the expected and normal admin action. There is no in-protocol mechanism to simultaneously allowlist the router and restrict individual users through it. Any pool admin who follows the natural UX path triggers the bypass.

## Recommendation
The extension must check the economically relevant actor, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension and cannot be enforced at the protocol level.
2. **Add an `originator` field to the `beforeSwap` hook interface**: The pool records `tx.origin` or the router passes the real user as a dedicated argument, and the extension checks that field instead of `sender`. This is the more robust fix as it mirrors how `addLiquidity` separates `sender` from `owner`.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  pool admin calls setAllowedToSwap(pool, alice, true)    // alice is approved
  bob is NOT in the allowlist

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for bob despite bob not being allowlisted

Result:
  bob swaps on a curated pool he was never approved for.
  The per-user allowlist is bypassed entirely for any router-mediated swap.
  Applies equally to exactOutputSingle, exactInput (all hops), and exactOutput (all hops).
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L136-137)
```text
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
