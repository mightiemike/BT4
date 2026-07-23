Audit Report

## Title
SwapAllowlistExtension Bypassed via Router: Any User Can Swap on Curated Pools When Router Is Allowlisted - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates access on the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If the pool admin allowlists the router to support legitimate router-based swaps, every user — including non-allowlisted ones — can bypass the swap allowlist by routing through the public router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` (the direct caller) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards this `sender` value directly to the extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same substitution occurs in `exactInput` (all hops): [5](#0-4) 

And in `exactOutputSingle` and the recursive `exactOutput` callback path: [6](#0-5) [7](#0-6) 

This creates an irreconcilable dilemma: if the admin does **not** allowlist the router, allowlisted users cannot swap through the router (broken UX). If the admin **does** allowlist the router, any user bypasses the allowlist entirely by routing through the public router.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional participants, or whitelisted market makers) loses that protection entirely once the router is allowlisted. Any unprivileged user can execute swaps against the pool's liquidity by calling the public router, exposing LP funds to trades the pool admin explicitly intended to block. This constitutes broken core pool functionality and unauthorized access to LP-owned assets.

## Likelihood Explanation

The attack requires no special privileges. Any user who observes that a pool uses `SwapAllowlistExtension` and that the router is allowlisted (both facts are on-chain and verifiable) can immediately exploit it. The pool admin is forced into this configuration the moment they want to support the standard periphery swap path. The router is a public, permissionless contract — there is no way to restrict who calls it.

## Recommendation

The extension must resolve the originating user, not the direct pool caller. The preferred fix is to have the router encode the originating `msg.sender` into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and verify that address when `sender` is a known router. Alternatively, document and enforce at the factory level that `SwapAllowlistExtension` is incompatible with router-mediated swaps.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so alice can use the router)

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient=bob, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes; bob receives tokens from the curated pool

Result:
  - bob bypassed the allowlist entirely
  - alice's exclusive access to the pool is broken
  - LP funds are exposed to any public user
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-165)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
