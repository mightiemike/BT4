Audit Report

## Title
`SwapAllowlistExtension` gates on router address instead of originating user, allowing any caller to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, `sender` equals the router contract address, not the originating user. If the pool admin allowlists the router (the only way to permit approved users to trade through the router), every user — including those explicitly excluded from the per-user allowlist — can bypass the restriction by routing through the same public contract.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` gates on that `sender`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly, making itself `msg.sender` to the pool: [4](#0-3) 

At that point, `sender` in `beforeSwap` is the router address, not the originating user. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Effect on allowlisted users | Effect on non-allowlisted users |
|---|---|---|
| Do **not** allowlist the router | Cannot use the router at all | Correctly blocked |
| **Allowlist the router** | Can use the router | **Also bypass the allowlist** |

By contrast, `DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position recipient), which is the economically relevant actor for deposits: [5](#0-4) 

## Impact Explanation

Any address excluded from the per-user swap allowlist can trade on a curated pool by routing through `MetricOmmSimpleRouter`. The pool's access-control invariant — that only explicitly approved addresses may swap — is broken for all router-mediated paths. Depending on the pool's purpose (institutional-only liquidity, regulatory KYC gating, MEV-protection allowlists), this exposes LP funds to uninvited counterparties and can result in direct loss of LP value or violation of the pool's intended economic model. The wrong value is `allowedSwapper[pool][router]` being evaluated instead of `allowedSwapper[pool][user]`, causing the extension to return `IMetricOmmExtensions.beforeSwap.selector` (allow) when it should revert with `NotAllowedToSwap`.

## Likelihood Explanation

The bypass is reachable whenever: (1) a pool is deployed with `SwapAllowlistExtension` in its `beforeSwap` hook order; (2) the pool admin has allowlisted the router — a natural operational step to let approved users trade through the standard periphery; (3) any non-allowlisted user calls any router entry point. All three conditions are realistic in production. The router is a public, permissionless contract, so step 3 requires no privilege. The bypass is repeatable by any address on every swap.

## Recommendation

The extension must gate on the economically relevant actor — the originating user — not the intermediary contract. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a trusted encoding convention between the router and the extension.
2. **Structural fix**: Add a `realSwapper` field to the `beforeSwap` hook signature so the pool can propagate both the immediate caller and the originating user, letting extensions choose which to gate on.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as the `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only approved swapper.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(params.recipient, ...)` with `msg.sender = router`.
6. `beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Bob successfully swaps on a pool he was explicitly excluded from.

The check that should have blocked Bob — `allowedSwapper[pool][bob]` — is never evaluated. [6](#0-5) [7](#0-6)

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
