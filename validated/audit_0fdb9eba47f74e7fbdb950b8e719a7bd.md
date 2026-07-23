Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap` call. When `MetricOmmSimpleRouter` is used, `sender` resolves to the router contract address rather than the actual user. Any pool admin who allowlists the router (to let their allowlisted users access the pool via the official router) inadvertently opens the pool to all users, because any caller can route through the public router and pass the allowlist check as the router address.

## Finding Description
`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the value forwarded above: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` executes, it calls `pool.swap(...)` directly with no user identity argument: [4](#0-3) 

The pool's `msg.sender` is therefore the router contract, so the extension receives `sender = router_address` and evaluates `allowedSwapper[pool][router]`. The same substitution occurs in `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

There is no mechanism in the router to attest the real caller's identity to the extension. The extension's own NatSpec states it "Gates `swap` by swapper address, per pool": [6](#0-5) 

but the identity it actually gates is the intermediary contract, not the swapper.

## Impact Explanation
This is a direct admin-boundary break. A pool admin configuring `SwapAllowlistExtension` to restrict swaps to specific counterparties (KYC-gated, institutional, compliance-restricted) cannot simultaneously allow those counterparties to use the official router and block non-allowlisted users from doing the same. Allowlisting the router is equivalent to calling `setAllowAllSwappers(pool, true)`. LPs who deposited under the assumption that only vetted counterparties could trade against them are exposed to unrestricted public flow, which can drain favorable inventory at oracle-quoted prices. This meets the "Admin-boundary break" and "direct loss of LP assets" allowed impact criteria.

## Likelihood Explanation
Medium-to-High. The bypass requires the router to be allowlisted, which is the natural and expected admin action for any pool that wants its allowlisted users to access the protocol through the official periphery. The structural gap is undocumented: pool admins have no way to simultaneously permit allowlisted users to use the router and block non-allowlisted users from doing the same, because the router is a single shared address. The flaw is repeatable by any unprivileged address with no special capability required beyond calling the public router.

## Recommendation
1. **Check the actual user, not the intermediary.** The router should encode the real caller's identity in `extensionData`. The extension should decode and verify that address instead of trusting `sender`. This requires a convention between router and extension.
2. **Alternatively**, add an explicit `swapper` argument to `pool.swap` (separate from `recipient`) that the router populates with `msg.sender`, and the extension checks that field.
3. **At minimum**, document clearly that `sender` in `beforeSwap` is `msg.sender` of the `pool.swap` call (i.e., the router when the router is used), so pool admins understand that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)`.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls extension.setAllowedToSwap(pool, userA, true)   // allowlist userA
  admin calls extension.setAllowedToSwap(pool, router, true)  // allow userA to use the router

Attack:
  userB (not allowlisted) calls:
    router.exactInputSingle({pool: restricted_pool, recipient: userB, ...})

Execution trace:
  router → pool.swap(recipient=userB, ...)         [msg.sender = router]
  pool   → _beforeSwap(sender=router, ...)
  pool   → extension.beforeSwap(sender=router, ...)
  extension checks: allowedSwapper[pool][router] == true  ✓
  swap executes — userB trades in a pool they were never authorized to access

Result:
  userB receives tokens from a pool whose LPs expected only userA-class counterparties,
  extracting value at oracle-quoted prices with no authorization.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
