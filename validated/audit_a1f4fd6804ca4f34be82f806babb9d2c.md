Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`. When `MetricOmmSimpleRouter` is the caller, `sender` equals the router's address, not the end-user's. A pool admin who allowlists the router to enable standard periphery usage inadvertently opens the gate to every user, completely defeating the per-user allowlist. Conversely, if the router is not allowlisted, allowlisted users cannot use the supported periphery at all.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as `sender` to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` = pool): [2](#0-1) 

Every public entry point of `MetricOmmSimpleRouter` calls `pool.swap()` directly, making the router the `msg.sender` of the pool call:

- `exactInputSingle`: [3](#0-2) 
- `exactInput` (all hops): [4](#0-3) 
- `exactOutputSingle`: [5](#0-4) 
- `exactOutput` (outer hop): [6](#0-5) 
- `_exactOutputIterateCallback` (inner hops): [7](#0-6) 

In all cases the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual economic actor is never checked. No existing guard corrects this: the extension has no mechanism to decode or receive the originating user address, and the pool passes only `msg.sender` with no additional context.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner`, an explicit parameter set by the caller that identifies the LP position owner, not the intermediary: [8](#0-7) 

## Impact Explanation
Two mutually exclusive fund-impacting failure modes:

**Mode A — Allowlist bypass (High):** Pool admin allowlists the router so that allowlisted users can trade through the standard periphery. Because the extension checks `sender` = router (allowlisted), every user — including those explicitly excluded — can call any router entry point and swap freely. The curated pool's access control is completely defeated; disallowed users trade against LP assets they were never meant to access, which can drain liquidity or violate the pool's curation invariant. This is a direct loss of LP principal protection and a broken core pool invariant.

**Mode B — Broken core functionality (Medium):** Pool admin does not allowlist the router. Allowlisted users cannot use `MetricOmmSimpleRouter` at all; they must call `pool.swap()` directly. The supported public periphery path is unusable for the pool's intended participants.

## Likelihood Explanation
Any pool deploying `SwapAllowlistExtension` and expecting users to interact through `MetricOmmSimpleRouter` (the documented periphery) is affected. A pool admin wanting to restrict swaps to a whitelist while supporting the standard router will naturally allowlist the router address, triggering Mode A. This requires no special privileges from the attacker — only a standard router call. The path is predictable, low-friction, and repeatable by any address.

## Recommendation
Pass the original end-user address through the swap call chain so the extension can check it. Two approaches:

1. **Preferred:** The router encodes `msg.sender` (the user) into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires a coordinated interface change between router and extension.

2. **Simpler:** Check `recipient` instead of `sender` in `beforeSwap`. `recipient` is the address that receives output tokens and is set by the user, not the router. This is not a perfect proxy (recipient can differ from payer) but is closer to the economic actor than the router address.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router so users can trade
  pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is not individually allowlisted

Attack:
  alice calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)          // msg.sender = router
    → pool calls _beforeSwap(router, ...)             // sender = router
    → extension checks allowedSwapper[pool][router]  // true → passes
    → alice's swap executes despite not being allowlisted

Expected: revert NotAllowedToSwap
Actual:   swap succeeds; alice trades against curated LP assets
```

Foundry test plan: deploy a pool with `SwapAllowlistExtension`, call `setAllowedToSwap(pool, router, true)` without allowlisting a test address, then call `router.exactInputSingle` from that address and assert the swap succeeds (demonstrating the bypass). Separately, call `pool.swap` directly from the same address and assert it reverts with `NotAllowedToSwap` (demonstrating the inconsistency).

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
