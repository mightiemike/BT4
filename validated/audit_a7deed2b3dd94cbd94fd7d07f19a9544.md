Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on the router address instead of the actual end user, allowing any unpermissioned caller to bypass a curated pool's allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `sender` is the immediate caller of `pool.swap`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the originating user. A pool admin who allowlists the router (required for any router-mediated swap to work) inadvertently grants every unpermissioned user the ability to bypass the curated allowlist, exposing LP principal to adversarial flow the allowlist was designed to exclude.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- immediate caller of pool.swap
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap`. When `MetricOmmSimpleRouter.exactInputSingle` is used, it calls `pool.swap` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

At this point `msg.sender` of `pool.swap` is the router, so `sender` delivered to the extension is the router address. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The same applies to multi-hop `exactInput` (L99-112) and `exactOutput` paths. There is no existing guard that unwraps the originating user from the router call.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, protocol-owned accounts, or whitelisted market makers) provides zero protection once the router is allowlisted. Any unpermissioned address can trade against the pool's LP assets at oracle-derived prices, directly exposing LP principal to adversarial flow the allowlist was designed to exclude. This constitutes broken core pool functionality and a direct loss of LP assets above Sherlock thresholds, matching the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" allowed impacts.

## Likelihood Explanation
The bypass requires no special privilege, no flash loan, and no multi-block setup. Any user who knows the pool address can call `MetricOmmSimpleRouter.exactInputSingle` with that pool. The router is a canonical, publicly deployed periphery contract. The attack path is a single standard router call, making likelihood **High**.

## Recommendation
**Short term:** Do not rely on the `sender` argument in `SwapAllowlistExtension.beforeSwap` as a proxy for the end user. Instead, require callers to embed the actual end-user identity in `extensionData` and verify it, or maintain a `trustedRouter` registry in the extension that, when `sender` is a trusted router, reads the originating user from a standardized interface on the router.

**Long term:** Define a protocol-level convention (e.g., a standard `IMetricOmmRouter` interface that exposes the originating user) so that all allowlist extensions can gate on the economically relevant actor regardless of which supported periphery path is used.

## Proof of Concept
```
Setup
─────
1. Pool admin deploys a MetricOmmPool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
3. Admin calls setAllowedToSwap(pool, router, true)  // required so alice can use the router

Attack
──────
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:          <curated pool>,
           recipient:     bob,
           zeroForOne:    true,
           amountIn:      X,
           ...
       })

5. Router calls pool.swap(...) — msg.sender of pool.swap = router.

6. pool._beforeSwap(sender=router, ...) → SwapAllowlistExtension.beforeSwap(sender=router, ...)
       allowedSwapper[pool][router] == true  →  check passes

7. Bob's swap executes at oracle price against LP principal.
   The allowlist provided zero protection.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
