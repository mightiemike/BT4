Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any EOA to bypass per-user swap allowlists via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` = `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, that value is the router address, not the originating EOA. Allowlisting the router therefore grants unrestricted swap access to every user of the public router, silently breaking the per-user gate the extension is designed to enforce.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then evaluates: [2](#0-1) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is whatever was passed as `msg.sender` into `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [3](#0-2) 

So `sender` = router address. The check resolves to `allowedSwapper[pool][router]`. Any EOA calling `exactInputSingle` has its identity collapsed to the router address before the allowlist is consulted. The originating EOA is stored only in the callback context (`_setNextCallbackContext` at line 71) for payment purposes, never surfaced to the extension.

## Impact Explanation
A pool admin who intends to restrict swaps to a specific set of EOAs (e.g., KYC'd addresses) and also allowlists the public router inadvertently opens the pool to all users. Non-allowlisted EOAs execute swaps at live oracle prices, draining LP-gated liquidity. This constitutes broken core pool functionality (`SwapAllowlistExtension` NatSpec: "Gates `swap` by swapper address, per pool") and potential direct loss of LP assets — matching the allowed impact of broken core pool functionality causing loss of funds. [4](#0-3) 

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical public swap entry point. Any pool operator who allowlists the router — a natural configuration for "allow router-based swaps" — triggers the bypass. No privileged access, malicious setup, or non-standard token behavior is required: only a standard `exactInputSingle` call from an unlisted EOA suffices. The condition is reachable by any unprivileged trader.

## Recommendation
Pass the originating user through the router to the pool. One concrete fix: have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and verify it (with the pool authenticating the router as the trusted source). Alternatively, document that allowlisting the router is equivalent to `setAllowAllSwappers = true` and enforce that invariant in the admin setter to prevent silent bypass.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Call `swapExtension.setAllowedToSwap(pool, address(router), true)` — allowlist only the router.
3. From a non-allowlisted EOA, call `router.exactInputSingle(...)` targeting the pool.
4. Observe the swap succeeds: `allowedSwapper[pool][router]` is `true`, so `beforeSwap` does not revert, and the non-allowlisted EOA receives output tokens.
5. Confirm that calling `pool.swap(...)` directly from the same EOA reverts with `NotAllowedToSwap` — proving the bypass is router-specific.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-13)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
