Audit Report

## Title
SwapAllowlistExtension checks router address instead of end user, allowing allowlist bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist, but `sender` is `msg.sender` of `pool.swap()` — which is the router contract when a user routes through `MetricOmmSimpleRouter`. A pool admin who allowlists the router to give approved users convenient access inadvertently grants universal swap access to every address, nullifying the per-user allowlist entirely.

## Finding Description

**Root cause — extension checks intermediary, not end user:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool: [1](#0-0) 

**What the pool passes as `sender`:**

`MetricOmmPool.swap()` passes `msg.sender` — the direct caller of `pool.swap()` — as the first argument to `_beforeSwap`: [2](#0-1) 

**What the router passes to the pool:**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router contract the `msg.sender` of that call — not the end user: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Bypass path:**

The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. Once the router is allowlisted (a natural operational step for any pool admin who wants approved users to access the router interface), `allowedSwapper[pool][router] == true` for every call arriving through the router — regardless of who the actual end user is.

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` (first arg) and checks `owner` (second arg), which is the economically relevant actor explicitly supplied by the caller: [4](#0-3) 

The swap extension has no equivalent mechanism; it relies solely on `sender`, which collapses to the router address on every router-mediated swap.

## Impact Explanation

A curated pool configured with `SwapAllowlistExtension` is intended to restrict trading to a specific set of addresses (e.g., KYC-verified counterparties, institutional participants). Once the pool admin allowlists the router, the allowlist is effectively nullified: any address can call the router and trade on the pool. Unauthorized users can execute trades the pool was designed to prevent, causing direct loss of LP principal through adverse selection and breaking the core access-control invariant of the curated pool. This constitutes a broken core pool functionality causing loss of funds, meeting the High/Critical impact threshold.

## Likelihood Explanation

The required condition — that the pool admin allowlists the router — is a natural and expected operational step. The router is the primary user-facing swap interface in the periphery. The admin has no on-chain signal that allowlisting the router grants universal access, because `isAllowedToSwap(pool, router)` returns `true` without revealing this grants access to all users. The trigger requires no special privilege; any EOA can call the router. The condition is highly likely to be met in production deployments.

## Recommendation

The extension must check the identity of the economic actor (the end user), not the identity of the intermediary contract. The preferred fix mirrors the deposit extension pattern: expose a dedicated `swapper` field (analogous to `owner` in `addLiquidity`) that the router fills with `msg.sender` before calling `pool.swap()`, and have the extension check that field instead of `sender`. Alternatively, the router can encode `msg.sender` into `extensionData`, and the extension decodes and checks it — requiring a coordinated convention between the router and the extension.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only approved swapper.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — to let Alice use the router conveniently.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. The pool calls `SwapAllowlistExtension.beforeSwap(router, ...)`.
7. The extension checks `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes on the curated pool despite never being allowlisted.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
