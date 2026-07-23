Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via Router: Any User Can Swap on Restricted Pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `MetricOmmPool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. Allowlisting the router — required for router-mediated swaps to function — grants every user on the network unrestricted access to the pool, completely defeating the per-user allowlist.

## Finding Description

`MetricOmmPool.swap` unconditionally passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` from the pool's perspective. The original end-user address is stored in transient storage only for the payment callback (`_setNextCallbackContext`), and is never forwarded to the extension: [4](#0-3) 

The call chain when a user routes through the router:
```
user → router.exactInputSingle() → pool.swap(msg.sender=router, ...)
                                         ↓
                              _beforeSwap(sender=router, ...)
                                         ↓
                              allowedSwapper[pool][router]  ← checked, NOT end user
```

For the router to be usable on an allowlisted pool, the admin must call `setAllowedToSwap(pool, router, true)`. Once done, `allowedSwapper[pool][router] == true` for every call through the router, regardless of who the end user is. There is no existing guard that recovers the original caller — `extensionData` is passed through but the extension does not decode any user address from it. [3](#0-2) 

## Impact Explanation

A pool deployer who configures `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, whitelisted market makers) loses that restriction entirely for any user routing through the public `MetricOmmSimpleRouter`. The allowlist — the sole access-control boundary on the swap path — is rendered inoperative. Unauthorized users can execute swaps and interact with pools explicitly designed to be closed to them. This is an admin-boundary break: an unprivileged path (the public router) bypasses a pool-admin-configured access control.

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user who discovers the router address can route through it. The pool admin faces an unavoidable dilemma: allowlist the router (breaking per-user access control for all users) or do not allowlist it (making the router entirely unusable on the pool). No configuration simultaneously supports router-mediated swaps and per-user allowlisting. Likelihood is high for any pool intending to use both the allowlist extension and the router.

## Recommendation

The extension must verify the economically relevant actor — the end user — not the intermediary. The most targeted fix is to have the router embed the original `msg.sender` in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that value. The pool should enforce that the embedded address matches the callback payer (already stored in transient storage via `_setNextCallbackContext`) to prevent spoofing. Alternatively, `MetricOmmPool.swap` can be modified to accept an explicit `swapper` parameter that routers populate with the original caller.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // required for router to work

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(msg.sender=router, ...)
  - pool calls extension.beforeSwap(sender=router, ...)
  - extension checks allowedSwapper[pool][router] == true  → passes
  - bob's swap executes on a pool he is not allowlisted for

Result:
  - SwapAllowlistExtension is bypassed for all users via the public router
  - Pool admin's per-user access control is completely defeated
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
