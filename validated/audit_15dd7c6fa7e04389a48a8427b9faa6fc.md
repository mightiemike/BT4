Audit Report

## Title
SwapAllowlistExtension gates on router address instead of actual user, allowing allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router to enable router-mediated swaps for permitted users simultaneously grants unrestricted swap access to every unprivileged user who routes through the same router.

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist as follows:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by `_beforeSwap`. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The pool therefore sees `msg.sender = router`, and the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same applies to `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165). [4](#0-3) 

There is no mechanism in the router to forward the original `msg.sender` to the extension. The router stores the original caller only in transient storage for the payment callback (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`), which is inaccessible to the extension. [5](#0-4) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to enforce a curated or permissioned trading environment loses that guarantee entirely once the router is allowlisted. Any unprivileged user can execute swaps against the pool's liquidity by routing through `MetricOmmSimpleRouter`, defeating the curation policy. This is a direct admin-boundary break: an unprivileged path bypasses a factory-configured access control that the pool admin explicitly set, allowing unauthorized parties to trade against restricted pool liquidity. [6](#0-5) 

## Likelihood Explanation

The bypass requires the admin to allowlist the router. This is a necessary operational step: a pool admin who wants permitted users to trade through the standard periphery must allowlist the router, because the extension sees `sender = router` for all router-mediated swaps. There is no other way to enable router access for allowed users without also enabling it for everyone. The bypass is therefore reachable in any production deployment that supports router-mediated swaps on an allowlisted pool. The attacker requires no special privileges — any EOA or contract can call `router.exactInputSingle`. [7](#0-6) 

## Recommendation

The extension must gate on the actual economic actor, not the intermediary. The preferred fix is for the router to forward the original `msg.sender` as part of `extensionData`, and for the extension to decode and verify it. Alternatively, the router could expose an `originalSender()` view backed by transient storage (similar to the existing `_getPayer()` pattern), and the extension could call it. A simpler but more restrictive approach is to require that `sender` is never a known router address, forcing permitted users to call the pool directly. [8](#0-7) 

## Proof of Concept

```
// Setup (pool admin):
swapExtension.setAllowedToSwap(pool, userA, true);   // intended: only userA
swapExtension.setAllowedToSwap(pool, router, true);  // required for userA to use router

// Attack (userB, not allowlisted):
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    zeroForOne: false,
    amountIn: 1000,
    recipient: userB,
    ...
}));
// router calls pool.swap() with msg.sender = router
// extension checks allowedSwapper[pool][router] → true
// swap executes — allowlist bypassed
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist `userA` and `router`, call `router.exactInputSingle` from `userB`, assert swap succeeds and `userB` receives output tokens despite never being allowlisted. [9](#0-8)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L11-41)
```text
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```
