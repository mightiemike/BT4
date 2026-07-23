Audit Report

## Title
SwapAllowlistExtension Bypassed via Router: `sender` Identity Mismatch Lets Any User Swap in Restricted Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against a per-pool allowlist, but `MetricOmmPool.swap` always passes its own `msg.sender` as `sender`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. Any pool admin who allowlists the router to support router-mediated swaps for legitimate users inadvertently opens the gate to all users, including those explicitly not on the allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that forwarded `sender` against the per-pool allowlist: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original `msg.sender` as the swapper identity: [4](#0-3) 

The pool's `msg.sender` is the router. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. A pool admin who wants allowlisted users to swap via the router must add the router to the allowlist. Once the router is allowlisted, the check passes for every call arriving through the router, regardless of who the actual user is. No existing guard in the extension or pool re-checks the real caller identity.

## Impact Explanation
Any user can swap in a pool the admin intended to restrict to a specific set of addresses. This breaks the core access-control invariant of `SwapAllowlistExtension`: the extension is designed to gate swaps by swapper identity, but when the router is allowlisted, it gates by router identity instead, silently permitting all users. For pools deployed in permissioned or KYC-gated environments, this constitutes a compliance failure and exposes LPs to counterparties they explicitly excluded, matching the "Broken core pool functionality causing loss of funds" and "Admin-boundary break: pool admin's restriction bypassed by an unprivileged path" allowed impacts.

## Likelihood Explanation
The trigger requires the pool admin to allowlist the router, which is the natural and expected configuration for any pool that wants to support router-mediated swaps for its allowlisted users. The admin cannot distinguish "router acting for an allowlisted user" from "router acting for anyone" because the extension only sees the router's address. The vulnerability is therefore reachable in every production deployment that uses `SwapAllowlistExtension` with router support enabled. The attack requires no special privileges — any address can call `exactInputSingle`, `exactInput`, or `exactOutput` on the router.

## Recommendation
The `sender` forwarded to extension hooks must represent the economic actor, not the immediate caller. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should write the original `msg.sender` into a verified transient-storage slot before calling `pool.swap()`, so extensions can read the real swapper identity from a trusted source.
2. **Extension-side**: `SwapAllowlistExtension` should read the real swapper from that verified transient-storage slot (written by the router, readable by the extension) rather than trusting the `sender` argument when the immediate caller is a known router.

Alternatively, document clearly that `SwapAllowlistExtension` gates the immediate caller of `pool.swap()`, not the end user, and that allowlisting the router opens the gate to all users — but this is not a fix, only a disclosure.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   (alice is the only intended swapper)
  allowedSwapper[pool][router] = true   (admin adds router to support alice's router swaps)

Attack:
  bob (not on allowlist) calls:
    router.exactInputSingle({pool: pool, tokenIn: token0, tokenOut: token1, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=bob, ...)   // msg.sender = router
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ← check passes
        → swap executes at oracle price
        → router.metricOmmSwapCallback() pulls token0 from bob
    bob receives token1

Result: bob swaps successfully despite not being on the allowlist.
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
