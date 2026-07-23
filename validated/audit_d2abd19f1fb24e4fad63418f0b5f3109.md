Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` rather than the actual user. If the pool admin allowlists the router (required for any router-based swaps to function), every user — including non-allowlisted ones — can bypass the swap allowlist by routing through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the first parameter forwarded by the pool. `MetricOmmPool.swap()` passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    ...
```

`ExtensionCalling._beforeSwap` encodes it verbatim into the extension call at [1](#0-0) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool therefore passes `sender = router` to the extension. The extension evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (the necessary operational step to enable any router-based swaps), this check passes for **every** user routing through the router, regardless of individual allowlist status.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` (first param unnamed) and checks `owner` (the actual position owner): [2](#0-1) 

The swap allowlist has the opposite, broken binding: it checks the intermediary (router) rather than the economic actor (user). [3](#0-2) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of users (e.g., KYC-verified addresses) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`, provided the router is allowlisted. The allowlist invariant — "only approved addresses may swap" — is silently violated. Non-allowlisted users can execute swaps in a pool designed to exclude them, constituting broken core pool functionality and a curation failure. This matches the allowed impact gate: broken core pool functionality causing loss of the intended access-control protection.

## Likelihood Explanation
The pool admin must allowlist the router for this bypass to be reachable. However, this is a forced operational step: without allowlisting the router, even individually allowlisted users cannot use the router (their swaps revert because `allowedSwapper[pool][router]` is false). The admin is therefore forced into a binary choice — block all router-based swaps, or open the pool to all router users — with no way to achieve the intended "allowlisted users may use the router" policy. The bypass is reachable through normal, expected pool administration and requires no special attacker capability beyond calling the public router. [4](#0-3) 

## Recommendation
The extension must check the actual economic actor, not the direct caller of `swap`. Two viable approaches:

1. **`extensionData` identity**: Have the router encode `msg.sender` (the actual user) into `extensionData`, and have the extension decode and check that address. The pool already threads `extensionData` through to the extension unchanged via [5](#0-4) 
2. **Mirror `DepositAllowlistExtension`**: Introduce a second parameter (analogous to `owner`) that carries the actual user identity even when a router is the direct caller, and check that instead of `sender`.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowlisted swapper.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes successfully in the allowlist-restricted pool.

Direct call by Bob (`pool.swap(...)` with `msg.sender = bob`) correctly reverts because `allowedSwapper[pool][bob]` is `false`. The bypass is exclusively reachable through the router path. [6](#0-5)

### Citations

**File:** metric-core/contracts/ExtensionCalling.sol (L162-165)
```text
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L173-174)
```text
          extensionData
        )
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-41)
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
