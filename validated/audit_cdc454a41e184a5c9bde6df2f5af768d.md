Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, the router is `msg.sender` to the pool, so the extension checks whether the **router address** is allowlisted rather than the actual end user. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the gate to every caller of the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender])
```
where `msg.sender` is the pool (correct) and `sender` is the first argument passed by the pool.

`MetricOmmPool.swap` passes its own `msg.sender` as `sender` to `_beforeSwap`:
```solidity
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
```

`MetricOmmSimpleRouter.exactInputSingle` stores the original caller only in transient storage for the payment callback, and calls `pool.swap()` directly without forwarding the original user:
```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The pool therefore sees `msg.sender = router`, and the extension receives `sender = router`. The allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router (required for any router-mediated swap to succeed for legitimate users), the check passes for **every** caller who routes through the router, regardless of whether that caller is individually allowlisted.

The same flaw exists in `exactOutputSingle` (L135-137) and `exactInput` (L103-112) for intermediate hops where `address(this)` (the router) is the payer.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise approved addresses is fully bypassed once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and trade on the pool as if they were allowlisted. The pool's LP assets are exposed to swaps from actors the pool admin explicitly intended to exclude, violating the core invariant that a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it. This constitutes a direct admin-boundary break with fund-impacting consequences.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard periphery swap path. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. The moment they do, the per-user gate collapses. The attacker requires no special privilege — only the ability to call the public router. The condition is met in the normal operational setup of any allowlisted pool that supports router-mediated swaps.

## Recommendation
The extension must resolve the actual end-user identity, not the intermediary. The cleanest fix is to have the router encode `msg.sender` into `extensionData` before forwarding to the pool. `SwapAllowlistExtension` then checks that `sender` is the allowlisted router, decodes the embedded original caller from `extensionData`, and verifies that caller is allowlisted. This requires a trust assumption that the router is the only allowed intermediary, enforced by also checking `sender == trustedRouter`. Alternatively, gate on `recipient` if the pool's policy intent is to control who receives output rather than who initiates the swap.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, userA, true)    // allowlist userA
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for router-mediated swaps

Attack:
  - userB (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., recipient: userB})
  - Router calls pool.swap(userB, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes for userB despite userB not being allowlisted

Result:
  - userB successfully swaps on a curated pool
  - The per-user allowlist is completely bypassed via the public router
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
