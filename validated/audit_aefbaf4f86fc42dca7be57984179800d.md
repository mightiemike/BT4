Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool, which is the pool's own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the originating user. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted user can bypass the allowlist by routing through the router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the pool's `msg.sender` the router contract, not the originating user: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the pool sees `msg.sender = router`: [5](#0-4) 

The root cause is that the pool has no mechanism to propagate the originating user's address through the router call stack. The extension's `sender` parameter always reflects the immediate caller of `pool.swap`, not the end user. The existing `allowedSwapper` mapping is keyed on this proxied address, so the check is structurally incapable of distinguishing which end user initiated the router call.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, protocol-internal actors, or specific market makers) is fully bypassed by any unpermitted user who calls `MetricOmmSimpleRouter` instead of the pool directly. The router is the primary user-facing interface, so this is a reachable path for any on-chain actor. The allowlist provides zero protection against router-mediated swaps once the router is allowlisted, and the pool admin has no on-chain mechanism to distinguish which end user initiated the router call. This constitutes broken core pool functionality — the access control mechanism that the extension is designed to enforce is completely defeated.

## Likelihood Explanation

The pool admin must allowlist the router to allow any router-mediated swap for permitted users. This is the natural and expected configuration for any pool that intends to support the periphery router. Once that configuration is in place, the bypass is trivially reachable by any address with no special privileges, no capital requirement, and no timing constraint. The attacker simply calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting the restricted pool.

## Recommendation

The pool must receive the originating user's address rather than the router's address. Two standard approaches:

1. **Router passes caller in extensionData**: The router encodes `msg.sender` into `extensionData` before calling the pool, and `SwapAllowlistExtension` decodes and verifies it (requires a trusted-router assumption or a signature).
2. **Pool exposes a `senderOverride` parameter**: The pool accepts an explicit `senderOverride` address that the router populates with `msg.sender`, and the pool passes that to extensions instead of its own `msg.sender`. The pool must validate the override against a factory-registered router allowlist.

The simplest safe fix is option 1 with a factory-registered router allowlist: the extension trusts the encoded sender only when `msg.sender` (the pool's caller) is a factory-registered router.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps for permitted users
  pool admin calls setAllowedToSwap(pool, alice, true)    // alice is a permitted user
  bob is NOT allowlisted

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓ passes
    → bob's swap executes successfully despite not being allowlisted

Result:
  bob bypasses the curated allowlist and trades on a pool
  intended to be restricted to permitted counterparties only.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
