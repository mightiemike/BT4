Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks caller (`sender`) instead of economic actor, enabling full allowlist bypass via router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` enforces the per-pool allowlist against `sender`, which is `msg.sender` of the pool's `swap` call — the router address when a user routes through `MetricOmmSimpleRouter`. If the pool admin allowlists the router (the necessary step to allow legitimate users to use the router), every unprivileged address can bypass the allowlist entirely by routing through the public router. `DepositAllowlistExtension.beforeAddLiquidity` handles the analogous guard correctly by checking `owner` (the economic actor) rather than the caller.

## Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`msg.sender` here is the pool (the extension caller). `sender` is whatever `msg.sender` the pool saw when `swap` was called. In `MetricOmmPool.sol`, the pool passes `msg.sender` verbatim as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address, not end user
    recipient,
    ...
);
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, so the pool sees `msg.sender = router`: [3](#0-2) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly elides `sender` (first param, `address,`) and checks `owner` — the economic actor who will hold the LP position:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [4](#0-3) 

The two parallel allowlist guards bind to different actors: `DepositAllowlistExtension` correctly binds to the economic actor; `SwapAllowlistExtension` incorrectly binds to the caller/router.

## Impact Explanation

A pool admin deploying a curated pool with `SwapAllowlistExtension` has no configuration that simultaneously allows legitimate allowlisted users to use `MetricOmmSimpleRouter` and blocks non-allowlisted users. Allowlisting the router (the natural production step) opens the pool to every unprivileged address. Unauthorized traders can execute swaps on a curated pool, draining LP value through adverse selection or violating the pool's intended access policy — a direct loss of LP principal and a broken core pool access-control invariant.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point. A pool admin who deploys a curated pool and wants allowlisted users to access the standard router will allowlist the router as a routine configuration step. The bypass is then reachable by any unprivileged address with no special preconditions, no privileged role, and no unusual token behavior required.

## Recommendation

Mirror the `DepositAllowlistExtension` pattern: gate on the economic actor, not the caller. For swaps the economic actor is the end user. The router should forward the originating user address in `extensionData`, and the extension should decode it when `sender` is a known router. Alternatively, redesign the `beforeSwap` interface to receive the true initiating user (analogous to how `beforeAddLiquidity` already receives `owner`), consistent with the existing deposit guard.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension`; sets `allowAllSwappers[pool] = false`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the intended curated user.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to let Alice use `MetricOmmSimpleRouter`.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)` — extension checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes on the curated pool with no revert, bypassing the allowlist entirely. [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
