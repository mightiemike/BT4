Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual End-User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the immediate `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, that `sender` is the router contract address, not the actual end-user. A pool admin who allowlists the router to enable router-mediated swaps for their restricted pool inadvertently grants every user—including those not individually allowlisted—the ability to bypass the per-user access control, breaking the extension's core invariant.

## Finding Description
`SwapAllowlistExtension.beforeSwap()` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()`. In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
  msg.sender,
  recipient,
  ...
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router directly calls `pool.swap()`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    ...
``` [3](#0-2) 

So `msg.sender` to the pool is the router contract, making `sender` = router address in `beforeSwap`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The router passes no end-user identity into `extensionData`—it passes `""` (empty bytes): [4](#0-3) 

Once a pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps, the check `allowedSwapper[pool][router]` returns `true` for every call through the router, regardless of who the actual end-user is. The per-user allowlist is completely bypassed for all router-mediated swaps.

## Impact Explanation
The pool admin's configured access-control boundary is bypassed by an unprivileged path (the public router). Any user can swap in a pool intended to be restricted to specific addresses. This is a direct admin-boundary break: the allowlist invariant ("only allowlisted addresses may swap") is violated for every router-mediated swap once the router itself is allowlisted. Pools designed for restricted participant sets (e.g., institutional-only, KYC-gated, or partner-only liquidity) are exposed to unrestricted public trading. The extension is documented as "Gates `swap` by swapper address, per pool" [5](#0-4) 
but this invariant is silently broken when the router is allowlisted.

## Likelihood Explanation
Medium. The bypass requires the pool admin to allowlist the router—a natural and expected configuration for any pool that wants to support the primary user-facing swap interface. A pool admin who configures a per-user allowlist and also wants to support `MetricOmmSimpleRouter` will unknowingly enable the bypass, because the extension gives no indication that allowlisting the router grants universal access. The router is a public, permissionless contract callable by any address.

## Recommendation
The extension must gate on the actual end-user identity, not the immediate caller of `pool.swap()`. Concrete options:

1. **Router-populated `extensionData`**: Have `MetricOmmSimpleRouter` prepend `msg.sender` (the real user) into `extensionData` before forwarding to the pool. The extension decodes and checks that value instead of `sender`, with a fallback to `sender` when `extensionData` is empty (direct calls).
2. **Separate allowlist for intermediaries**: Distinguish between "allowed direct swappers" and "allowed intermediaries," and require intermediaries to attest the real user identity in `extensionData`.
3. **Documentation gate**: At minimum, document clearly that allowlisting the router grants universal swap access and that per-user allowlisting is only enforceable for direct `pool.swap()` calls.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin: setAllowedToSwap(pool, alice, true)   // only alice is allowlisted
3. Pool admin: setAllowedToSwap(pool, router, true)  // enable router-mediated swaps
4. Bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
5. Router calls pool.swap(recipient, ...) — msg.sender to pool = router
6. Pool calls extension.beforeSwap(router, recipient, ...)
7. Extension checks: allowedSwapper[pool][router] → true → PASSES
8. Bob's swap executes in a pool he was never individually allowlisted for.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
``` [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
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
