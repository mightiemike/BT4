Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of the `pool.swap` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently opens the pool to every caller of the router, nullifying the per-user access control entirely.

## Finding Description

`SwapAllowlistExtension.beforeSwap` enforces its check against the `sender` parameter:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

`MetricOmmPool.swap` populates `sender` with `msg.sender` of the pool call: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` is the entity that calls `pool.swap`, making the router the `msg.sender` from the pool's perspective: [4](#0-3) 

Therefore, `sender` seen by the extension is always the **router address**, not the end user. If the pool admin has allowlisted the router (a necessary step to permit any router-mediated swap), `allowedSwapper[pool][router] == true` causes the check to pass for every caller of the router regardless of whether that caller is individually allowlisted.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` (first argument is `_`) and checks `owner` — the economic beneficiary — which the liquidity adder always sets to the actual depositor: [5](#0-4) 

The two guards are structurally parallel but the swap guard checks the wrong actor.

## Impact Explanation

Any user not on the per-user allowlist can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter`, provided the router address is allowlisted. Because pool prices are oracle-anchored and the pool holds real LP assets, an unauthorized swapper can drain value from the pool at the oracle-quoted price, causing direct loss of LP principal. The allowlist's entire purpose — restricting who may trade — is nullified. This constitutes a Critical/High direct loss of user principal.

## Likelihood Explanation

A pool admin who deploys `SwapAllowlistExtension` and wants their allowlisted users to trade through the standard router **must** allowlist the router address; there is no other mechanism to permit router-mediated swaps. This is a natural, non-malicious configuration step. Once taken, the bypass is immediately available to any public caller with no further preconditions. The triggering action is a routine admin call, not a malicious one, making exploitation highly likely once the configuration is in place.

## Recommendation

Mirror the design of `DepositAllowlistExtension`: check the economically relevant actor, not the immediate caller. One approach is to have the router encode the originating user (`msg.sender`) into `extensionData` using a standardized prefix, and have `SwapAllowlistExtension.beforeSwap` decode and verify that address. Alternatively, require the extension to check both `sender` and a user-supplied identity claim forwarded by the router. Until resolved, pool admins must be warned never to allowlist the router address on a pool using `SwapAllowlistExtension` for per-user gating.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — intending only Alice to swap.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — intending to let Alice use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle` with `pool` as the target.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap` receives `sender = router`, finds `allowedSwapper[pool][router] == true`, and returns without reverting.
7. Bob's swap executes at the oracle price, extracting value from LP positions that were supposed to be protected by the allowlist.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
