Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Making Per-User Allowlist Permanently Broken or Fully Bypassable for Router-Mediated Swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is used, it calls `pool.swap()` directly, making the router the `msg.sender` — not the end-user. The extension therefore gates the router's address, not the individual user's address, producing an irresolvable two-sided failure: either allowlisted users are blocked from the standard periphery, or the allowlist is fully bypassed by routing through the router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle` is used, it calls `pool.swap()` directly: [3](#0-2) 

So `sender` arriving at the extension is the **router's address**, not the end-user's address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same applies to `exactOutputSingle` and all multi-hop paths (`exactInput`, `exactOutput`). [4](#0-3) 

There is no mechanism in the router to encode the real end-user identity into `extensionData`, and the extension does not attempt to decode any such field. [5](#0-4) 

## Impact Explanation

**Broken core swap flow (router path):** Any pool deploying `SwapAllowlistExtension` without allowlisting the router will silently block all allowlisted users from using `MetricOmmSimpleRouter` — the primary user-facing swap entry point — with `NotAllowedToSwap`, even though they are individually permitted. The standard periphery is completely unusable for such pools.

**Allowlist bypass (router allowlisted):** If the pool admin allowlists the router to restore router functionality, the guard is fully neutralised. Any address — including those explicitly excluded — can call `MetricOmmSimpleRouter.exactInputSingle` and swap against the pool. LP providers are exposed to trades from counterparties the allowlist was designed to block. Both outcomes constitute broken core pool functionality and an admin-boundary break.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point; virtually all non-technical users route through it. Pool admins are not warned in the extension's NatSpec or interface that router-mediated swaps present the router address as the swapper identity. [6](#0-5) 

The `setAllowedToSwap` admin setter operates on individual swapper addresses with no indication that router intermediaries require separate treatment. [7](#0-6) 

Any pool that deploys this extension and expects per-user swap gating will be misconfigured by default. The exploit requires no special privileges — any unprivileged user can trigger Scenario A (blocked) or Scenario B (bypass) depending on admin configuration.

## Recommendation

The extension must receive the economic actor (the end-user), not the intermediary. Two complementary fixes:

1. **Router-side:** `MetricOmmSimpleRouter` should encode `msg.sender` (the real user) into `extensionData` so extensions can decode the true swapper identity.
2. **Extension-side:** `SwapAllowlistExtension.beforeSwap` should decode a `swapper` address from `extensionData` when present and fall back to `sender` only when absent, preserving backward compatibility for direct pool calls.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is individually permitted
  allowedSwapper[pool][router] = false // router not allowlisted

Scenario A — legitimate user blocked:
  alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  → router calls pool.swap(recipient, ...)   // router is msg.sender
  → pool calls _beforeSwap(sender=router, ...)
  → extension checks allowedSwapper[pool][router] == false
  → revert NotAllowedToSwap
  alice cannot use the standard periphery despite being allowlisted.

Scenario B — guard bypass (admin allowlists router to fix A):
  admin sets allowedSwapper[pool][router] = true
  bob (not individually allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...)
  → router calls pool.swap(recipient, ...)   // router is msg.sender
  → pool calls _beforeSwap(sender=router, ...)
  → extension checks allowedSwapper[pool][router] == true
  → passes; bob swaps successfully despite not being allowlisted.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-137)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```
