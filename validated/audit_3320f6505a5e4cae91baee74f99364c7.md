Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any caller to bypass per-pool swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that caller is the router contract, not the end user. If the pool admin allowlists the router (the only way to let any user swap through it), every user — including those explicitly excluded — can bypass the restriction by routing through the router.

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. [1](#0-0) 

`MetricOmmPool.swap` passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← sender = whoever called pool.swap()
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The router stores the original `msg.sender` only in transient callback context for payment purposes — it is never forwarded to the pool or extension as the swap initiator. The `extensionData` passed to `pool.swap` is user-supplied and not router-injected. [4](#0-3) 

The resulting call chain is:

```
user → router.exactInputSingle() → pool.swap()  [msg.sender = router]
                                        ↓
                              beforeSwap(sender = router, ...)
                                        ↓
                         allowedSwapper[pool][router]  ← checked, not user
```

The pool admin faces an impossible choice: not allowlisting the router blocks all router-mediated swaps; allowlisting the router grants every user — including those explicitly excluded — the ability to bypass the restriction.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the second argument, the economically relevant party), not the payer/caller: [5](#0-4) 

This asymmetry creates a false expectation that the swap allowlist similarly gates on the end user.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC'd addresses, institutional market makers, or protocol-controlled bots) loses that restriction entirely for any user routing through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against a pool intended to be closed to them, draining liquidity at oracle-derived prices the LP only intended to offer to trusted counterparties. This constitutes a direct loss of LP principal through unauthorized swap execution — broken core pool functionality causing loss of funds.

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. The bypass requires only calling `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` — standard user-facing operations. Any user who discovers the allowlist restriction can trivially route around it. Pool admins are unlikely to anticipate this because `DepositAllowlistExtension` correctly gates on `owner`, creating a false expectation of consistent behavior.

## Recommendation

`SwapAllowlistExtension.beforeSwap` should check the end user rather than the immediate caller. The preferred fix is to define a standard interface that trusted routers implement to expose the originating user, and check that address instead of `sender`. Alternatively, require that `sender == tx.origin` or that `sender` is not a known router, and document that router-mediated swaps are incompatible with the allowlist. The fix should mirror `DepositAllowlistExtension`'s approach of gating on the economically relevant party.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // Alice is allowlisted
  allowedSwapper[pool][router] = true         // router allowlisted so Alice can use it
  allowedSwapper[pool][bob] = false           // Bob is NOT allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → pool.swap() called with msg.sender = router
    → beforeSwap(sender = router, ...)
    → allowedSwapper[pool][router] == true    ✓ passes
    → Bob's swap executes against the restricted pool
```

Bob successfully swaps against a pool he was explicitly excluded from, receiving tokens at oracle-derived prices the LP only intended to offer to allowlisted counterparties. A Foundry integration test can confirm this by deploying the pool with `SwapAllowlistExtension`, setting `allowedSwapper[pool][router] = true` and `allowedSwapper[pool][bob] = false`, then calling `router.exactInputSingle` from Bob's address and asserting the swap succeeds.

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
