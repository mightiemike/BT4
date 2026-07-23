Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently grants unrestricted swap access to every user, completely defeating the allowlist.

## Finding Description

**Extension check:** `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by the pool: [1](#0-0) 

**Pool binds `sender` to its own `msg.sender`:** `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, so `sender` in the extension is always the direct caller of `pool.swap`: [2](#0-1) 

**ExtensionCalling passes it verbatim:** `_beforeSwap` in `ExtensionCalling` encodes `sender` unchanged into the extension call: [3](#0-2) 

**Router does not forward the user:** `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The pool's `msg.sender` is the router, not the end user. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

**The bypass:** The extension therefore evaluates `allowedSwapper[pool][router]`. A pool admin who wants allowlisted users to access the pool via the standard router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every user — including those never individually allowlisted — can call `router.exactInputSingle(pool, ...)` and the extension passes unconditionally, since the check resolves to `allowedSwapper[pool][router] == true` regardless of who the actual end user is. [5](#0-4) 

## Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to specific participants (e.g., KYC-verified addresses, institutional counterparties) is fully bypassed for all router-mediated swaps. Any unprivileged user can route through `MetricOmmSimpleRouter` and trade on the pool as if they were allowlisted. This is a direct admin-boundary break: the pool admin's intended access control is silently removed by an unprivileged path, and the router is the primary production swap path for end users.

## Likelihood Explanation

Likelihood is high. Pool admins who deploy a curated pool and want their allowlisted users to access the standard router will naturally call `setAllowedToSwap(pool, router, true)`. There is no mechanism or documentation warning that doing so opens the pool to all users. The bypass requires no special knowledge or funds — any user needs only to call a standard router function.

## Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediary router. Two viable approaches:

1. **Pass the original user through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and verifies it, trusting only pre-registered routers as the `sender`.

2. **Trusted router registry:** Maintain a registry of trusted routers in the extension. When `sender` is a trusted router, decode the real user from `extensionData` and check `allowedSwapper[pool][realUser]` instead.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, user1, true)   // allowlist user1
  - Pool admin calls setAllowedToSwap(pool, router, true)  // enable router for user1

Attack:
  - userX (never allowlisted) calls:
      router.exactInputSingle({pool: pool, recipient: userX, ...})
  - Router calls pool.swap(...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - ExtensionCalling encodes sender=router and calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → TRUE
  - Swap succeeds for userX despite never being allowlisted

Result:
  - userX trades on a curated pool without authorization
  - The allowlist is completely bypassed for all router-mediated swaps
```

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
