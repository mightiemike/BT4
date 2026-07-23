Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual user. Because the router must be allowlisted for router-mediated swaps to function on a restricted pool, any unprivileged user can bypass the per-user allowlist by routing through the router.

## Finding Description
In `SwapAllowlistExtension.beforeSwap`, the guard is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. [1](#0-0) 

In `MetricOmmPool.swap()`, the pool calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is whoever called `pool.swap()` directly — i.e., the router, not the end user. [2](#0-1) 

In `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap(params.recipient, ...)` with no mechanism to forward the original caller's identity. The actual user's address is stored only in transient callback context for payment purposes and is never forwarded to the pool or extension as the swap initiator. [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. For a restricted pool to support router-mediated swaps at all, the pool admin must allowlist the router. Once the router is allowlisted, every user who calls through the router bypasses the individual user allowlist check entirely.

The same issue applies to `exactOutputSingle` and multi-hop `exactInput`/`exactOutput` paths, all of which call `pool.swap()` with the router as `msg.sender`. [4](#0-3) 

## Impact Explanation
Any unprivileged user can bypass the swap allowlist on a restricted pool by routing through `MetricOmmSimpleRouter`. This breaks the core access-control invariant of the allowlist extension. For pools with compliance requirements (KYC/AML gating, institutional-only access), the bypass nullifies the entire purpose of the extension. Unauthorized users can execute swaps against LP positions that were deployed under the assumption that only authorized counterparties would interact, directly putting LP principal at risk through unauthorized swaps at oracle-derived prices.

## Likelihood Explanation
High. `MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it. For a restricted pool to support router-mediated swaps (the primary user-facing entry point), the router must be allowlisted. Once allowlisted, the bypass is trivially reachable by any user with no special privileges, no admin cooperation, and no unusual token behavior. The bypass is repeatable for any amount.

## Recommendation
The extension must gate the actual economic actor, not the intermediary. Concrete options:

1. **Router-level enforcement**: Have `MetricOmmSimpleRouter` encode the actual caller's address in `extensionData` and have the extension verify it against a trusted router registry (requires a trusted router design).
2. **Direct-pool-only restriction**: Document and enforce that allowlisted pools must not allowlist the router; users on restricted pools must call `pool.swap()` directly.
3. **Separate router allowlist**: Introduce a second allowlist tier at the router level that gates `msg.sender` before forwarding to the pool, so the pool-level check becomes redundant but the router enforces the real identity.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Unauthorized user Alice (not individually allowlisted) calls `router.exactInputSingle(...)`.
4. Router calls `pool.swap(params.recipient, ...)` — `msg.sender` of `pool.swap()` = router address.
5. Pool calls `_beforeSwap(router, recipient, ...)` — `sender` argument = router.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` = `true` → passes.
7. Alice's swap executes successfully despite never being individually allowlisted.
8. Alice can repeat this for any amount, draining LP liquidity on a pool intended to be restricted.

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
