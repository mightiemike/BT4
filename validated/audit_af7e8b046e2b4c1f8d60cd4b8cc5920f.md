Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the End-User, Allowing Any User to Bypass the Swap Allowlist via the Router — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. A pool admin who allowlists the router to enable router-mediated swaps for curated pools inadvertently grants every router caller — including non-allowlisted users — the ability to bypass the per-user gate entirely.

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces the allowlist by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the calling pool and `sender` is the first argument forwarded by the pool: [1](#0-0) 

`MetricOmmPool.swap` populates `sender` with its own `msg.sender` before calling `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this `sender` verbatim to every configured extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same applies to `exactOutputSingle`, `exactInput`, and `exactOutput`. In every case the pool receives `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end-user]`. Once the router is allowlisted, the check passes for every caller of the router regardless of individual allowlist status. The project's own audit-target specification explicitly identifies this exact path as a known risk vector: [5](#0-4) 

## Impact Explanation

Any user who is not individually allowlisted can swap on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) once the router address is allowlisted. The allowlist — the sole access-control mechanism for curated pools — is completely bypassed. This is an admin-boundary break: an unprivileged actor reaches a swap path the pool admin explicitly intended to restrict. For institutional-only, KYC-gated, or private LP pools, this exposes LP funds to unauthorized counterparties and violates the pool's economic invariants.

## Likelihood Explanation

Two routine operational conditions trigger the bypass: (1) a pool is deployed with `SwapAllowlistExtension` configured as a `beforeSwap` hook — a supported, documented production configuration — and (2) the pool admin allowlists the router address, which is the natural and expected step for any curated pool that intends to support standard periphery tooling. No malicious setup, non-standard token, or privileged attacker capability is required. Any unprivileged user can then exploit the bypass by calling the public router.

## Recommendation

The extension must gate the end-user, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router**: `MetricOmmSimpleRouter` already stores the original `msg.sender` as the payer in transient storage. The router could encode the real user in `extensionData` for the extension to decode and verify.
2. **Router-aware extension**: The extension could maintain a registry of trusted routers and, when `sender` is a router, require the real user identity to be supplied in `extensionData` and verified there.

The simplest safe interim fix is to explicitly document that the router must never be allowlisted on a curated pool, and that allowlisted users must call the pool directly — but this constraint must be enforced at the code level, not just in documentation.

## Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin calls swapExtension.setAllowedToSwap(pool, router, true)   // allowlist the router
  admin calls swapExtension.setAllowedToSwap(pool, alice, true)    // alice is individually allowed
  bob is NOT individually allowlisted

Attack:
  bob calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...)   // msg.sender to pool = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
    → swap executes successfully for bob

Direct call (control):
  bob calls pool.swap(...) directly
    → pool calls _beforeSwap(sender=bob, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][bob] → false
    → revert NotAllowedToSwap ✓

Result: bob bypasses the per-user allowlist by routing through the router.
```

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

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
