Audit Report

## Title
`SwapAllowlistExtension` Per-User Gate Bypassed via `MetricOmmSimpleRouter` When Router Is Allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool binds to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool receives `sender = router_address`. If the pool admin allowlists the router (a prerequisite for any router-mediated swap), every non-allowlisted user can bypass the per-user gate by routing through the public router contract, receiving output tokens from a restricted pool as if they were allowlisted.

## Finding Description

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`MetricOmmPool.swap()` binds `sender` to `msg.sender` of the pool call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,   // direct caller of pool.swap(), not the original user
    ...
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged to the extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router `msg.sender` at the pool level. The original user's identity is never forwarded:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // user-controlled bytes, extension does not decode them
    );
``` [4](#0-3) 

The same applies to `exactInput` multi-hop paths, where the router is always `msg.sender` for every pool hop: [5](#0-4) 

The extension therefore checks `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][original_user]`. The pool admin faces an impossible choice: not allowlisting the router blocks all router-mediated swaps for legitimate users; allowlisting the router opens the gate to every unprivileged address.

`DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner explicitly passed by the caller), not on `sender` (the direct pool caller): [6](#0-5) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to KYC'd, institutional, or otherwise vetted addresses can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker receives output tokens from the restricted pool and the pool's LP positions absorb the trade exactly as if the attacker were allowlisted. This breaks the core access-control invariant of the extension and constitutes unauthorized access to restricted pool liquidity — a direct policy bypass with fund-level impact on LP positions in curated pools.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges, no admin access, and no unusual token behavior. The attacker only needs to call `exactInputSingle` or `exactInput` on the public router.

## Recommendation

The extension must gate on the original user, not the direct caller of `pool.swap()`. Two complementary fixes:

1. **Pass the original user through the router.** The router should forward `msg.sender` (the original user) as an additional field in `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and check that identity instead of (or in addition to) `sender`.

2. **Alternatively, maintain a trusted-router registry in the extension.** When `sender` is a known router, require the original user identity to be present and verified in `extensionData`; when `sender` is not a router, check `sender` directly as today.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is KYC'd
  allowedSwapper[pool][router] = true         // router allowlisted so alice can use it
  allowedSwapper[pool][attacker] = false      // attacker is NOT allowlisted

Attack:
  attacker calls router.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, ...) with msg.sender = router
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → swap proceeds
    → attacker receives output tokens from the restricted pool

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds; attacker bypasses the allowlist
```

Foundry test plan: deploy a pool with `SwapAllowlistExtension`, allowlist `alice` and the router, confirm `attacker` is blocked on direct `pool.swap()`, then confirm `attacker` succeeds via `router.exactInputSingle()` — demonstrating the bypass.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
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
