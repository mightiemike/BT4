Audit Report

## Title
`SwapAllowlistExtension` receives router address as `sender` instead of end user, enabling allowlist bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to all before-swap extensions. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, not the end user. `SwapAllowlistExtension.beforeSwap` checks this `sender` value against its per-pool allowlist, so any pool admin who adds the router to the allowlist (the natural action to support router-mediated swaps for approved users) inadvertently grants every user access to the curated pool.

## Finding Description
`MetricOmmPool.swap` captures `msg.sender` at entry and forwards it as `sender` to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded from the pool: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly. The router stores the actual caller in transient storage via `_setNextCallbackContext` for payment purposes only; it does not encode `msg.sender` into `extensionData`: [3](#0-2) 

At the point the pool executes, `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`. A pool admin who wants approved users to swap via the router must add the router to the allowlist. Once `allowedSwapper[pool][router] = true`, every caller of `exactInputSingle` — including users the admin never approved — passes the guard. There is no mechanism in the router or the extension to distinguish individual users behind the router.

The `setAllowedToSwap` setter operates on individual addresses with no concept of "this address is a router acting on behalf of others": [4](#0-3) 

## Impact Explanation
Any pool deploying `SwapAllowlistExtension` to create a permissioned trading venue loses its access-control guarantee the moment the router is added to the allowlist. Non-allowlisted users can trade against the pool at oracle-anchored prices. If those users hold informational advantages (e.g., stale oracle, imbalanced pool), they can extract value from LP positions. The allowlist — the pool's primary defense against adverse selection — fails open on the most common periphery path. This constitutes broken core pool functionality with potential LP principal loss, matching the allowed impact gate.

## Likelihood Explanation
Medium. The bypass requires the pool admin to add the router to the allowlist, which is the natural and expected action for any operator who wants approved users to use the standard periphery. The admin has no indication from the contract or its NatSpec that doing so opens the gate to all users. The scenario is a foreseeable misconfiguration, not an exotic edge case, and is repeatable by any user who calls `exactInputSingle` on an affected pool.

## Recommendation
The extension must check the identity of the economic actor, not the calling contract. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool; `SwapAllowlistExtension` decodes and checks that address. Pool admins allowlist individual users, not the router.
2. **Two-layer check**: Introduce a router allowlist and a per-user allowlist. The extension first verifies the calling contract is an approved router, then verifies the user address embedded in `extensionData` is on the per-user allowlist.

Either approach ensures the guard checks the same actor the pool admin intended to gate.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` as a before-swap hook.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` to allowlist user A.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for user A.
4. Non-allowlisted user B calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. The router calls `pool.swap(params.recipient, ...)` — `msg.sender` inside the pool is the router.
6. `_beforeSwap` encodes `sender = router` and calls the extension.
7. The extension evaluates `allowedSwapper[pool][router] == true` → no revert.
8. User B's swap executes against the curated pool, bypassing the per-user allowlist entirely. [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
