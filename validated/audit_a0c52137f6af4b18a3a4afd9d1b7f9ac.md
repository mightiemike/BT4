Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass Per-User Swap Allowlists via the Router - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which resolves to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router address, not the actual user. A pool admin who allowlists the router to enable periphery access inadvertently opens the allowlist to every user who routes through the public router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without encoding the original caller into `extensionData`: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) [6](#0-5) 

The call chain collapses all users into a single identity:

```
user → MetricOmmSimpleRouter.exact*() → pool.swap(msg.sender = router)
  → extension.beforeSwap(sender = router)
  → checks allowedSwapper[pool][router] == true  ✓
```

The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Any user routing through the public router passes the check as long as the router is allowlisted, regardless of whether that user is individually allowlisted.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely for any user who routes through the public router. Non-allowlisted users receive oracle-priced output from LP funds intended to be restricted to vetted counterparties. This is a direct loss of LP assets and a broken core pool invariant (curated access control). The corrupted value is the `allowedSwapper[pool][sender]` decision: it evaluates `true` for the router when it should evaluate against the actual user's entry.

## Likelihood Explanation

The router is a public, permissionless contract. Any user who observes that a pool has a swap allowlist can trivially attempt a router-mediated swap. The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration step for any pool that wants to support the standard periphery path. This precondition is met by design for any production curated pool that also supports router access.

## Recommendation

`SwapAllowlistExtension.beforeSwap` must gate on the original user, not the intermediate caller. The cleanest fix: `MetricOmmSimpleRouter` encodes `msg.sender` into `extensionData` before calling `pool.swap()`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router. Alternatively, document clearly that allowlisting the router opens the gate to all users and provide a separate router-aware extension that decodes the real user from `extensionData`.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only allowed user
  allowedSwapper[pool][router] = true         // admin adds router to support periphery

Attack:
  bob (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle(pool, ...)

  Router calls:
    pool.swap(recipient=bob, ...)             // msg.sender to pool = router

  Pool calls:
    extension.beforeSwap(sender=router, ...)
    → checks allowedSwapper[pool][router] == true  ✓

  Result:
    bob's swap executes successfully despite not being in the allowlist.
    The allowlist invariant is broken; bob receives oracle-priced output
    from LP funds intended to be restricted to alice only.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
