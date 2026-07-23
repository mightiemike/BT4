Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the extension checks the router's address rather than the actual user's address. Any pool admin who allowlists the router to support router-mediated swaps for legitimate users simultaneously opens the gate to every non-allowlisted user.

## Finding Description

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the pool calls the extension) and `sender` is the first argument forwarded from the pool: [1](#0-0) 

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making itself `msg.sender` to the pool: [4](#0-3) 

The pool therefore passes `router` as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. The same identity mismatch applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The structural trap: a pool admin who wants allowlisted users to use the router must add the router to the allowlist. The moment they do, every non-allowlisted user can bypass the gate by routing through the same router. There is no mechanism to simultaneously allow router-mediated swaps for approved users and block router-mediated swaps for unapproved users.

## Impact Explanation

A curated pool (KYC-gated, institutional-only, or regulatory-restricted) that configures `SwapAllowlistExtension` and allowlists the router loses all per-user swap enforcement for router-mediated paths. Any non-allowlisted address can execute swaps against the pool's liquidity, draining LP value through trades the pool was designed to reject. This constitutes a direct loss of LP principal and a complete curation failure on the supported periphery path — matching the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" allowed impact categories.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool admin who configures a swap allowlist and also wants to support the standard router will naturally allowlist the router. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only a call to the public `exactInputSingle` (or any other router entry point). The condition is self-inflicted by correct, expected pool admin behavior.

## Recommendation

The extension must check the economically relevant actor, not the intermediary. Two sound approaches:

1. **Pass the real user through `extensionData`**: The router appends `msg.sender` to `extensionData` before calling `pool.swap`, and the extension decodes and checks it. The extension should also verify the caller is a trusted router when using this path.

2. **Require direct pool interaction for allowlisted pools**: Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router; allowlisted users must call `pool.swap()` directly. This breaks router UX but preserves correctness.

The simplest correct fix is option 2 combined with a NatSpec warning on `setAllowedToSwap`. The proper fix is option 1, extending the extension interface to carry real initiator identity through the router hop.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, alice, true)  — alice is the only allowed swapper.
3. Pool admin calls setAllowedToSwap(pool, router, true) — required so alice can use the router.
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...}).
5. Router calls pool.swap(...) with msg.sender = router.
6. Pool calls _beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[pool][router] == true → passes.
8. Bob's swap executes against the pool's liquidity despite not being on the allowlist.
9. Direct call: bob calls pool.swap() directly → allowedSwapper[pool][bob] == false → reverts.
   Router call: same bob, same pool, same block → succeeds.
```

The invariant "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it" is broken: the direct path enforces per-user identity; the router path enforces only router identity.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
