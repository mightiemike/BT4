Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass Per-User Swap Allowlist via Router - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` binds to `msg.sender` of the pool call — the router contract, not the end user, when swaps are routed through `MetricOmmSimpleRouter`. A pool admin who allowlists the router to enable standard periphery usage inadvertently grants every unpermissioned user the ability to bypass the per-user allowlist entirely.

## Finding Description

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (the extension's caller) and `sender` is the first argument passed by the pool: [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` (the direct caller of the pool) as `sender` to `_beforeSwap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router itself `msg.sender` of that call — the actual user's address is never forwarded: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [4](#0-3) 

The full broken call chain:
```
User → MetricOmmSimpleRouter.exactInputSingle()
     → pool.swap()          [msg.sender = router]
     → _beforeSwap(sender = router, ...)
     → SwapAllowlistExtension.beforeSwap(sender = router)
     → checks allowedSwapper[pool][router]  ← actual user never checked
```

There is no existing guard that recovers the original user's identity. The `extensionData` field is forwarded from the router to the pool unchanged, but `SwapAllowlistExtension` does not decode it — it only reads the `sender` argument. No mechanism in the current code propagates the end user's address through the router → pool → extension call chain.

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific addresses is completely bypassed. Any unpermissioned user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting the pool. The extension sees `sender = router`, which is allowlisted, and the swap executes. The unauthorized user trades against the pool's liquidity, violating the pool's intended access model and potentially extracting value from LP funds if the pool was designed to trade only with trusted counterparties. This constitutes broken core pool functionality causing direct loss of curation policy enforcement and potential LP fund exposure.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap interface for end users. Any pool admin who deploys a curated pool with `SwapAllowlistExtension` and also wants users to use the router faces a forced choice: either allowlist the router (breaking the allowlist for everyone) or do not allowlist the router (making the allowlist incompatible with the router). The bypass is reachable by any user with no special privileges, no malicious setup, and no non-standard tokens — just a standard router call.

## Recommendation

The `sender` argument passed to `beforeSwap` must represent the economic actor (the end user), not the intermediary contract. Two approaches:

1. **Pass the original user through `extensionData`:** Modify `MetricOmmSimpleRouter` to ABI-encode `msg.sender` (the user) into `extensionData` before forwarding to the pool, and update `SwapAllowlistExtension.beforeSwap` to decode and check that value when `sender` is a known router.

2. **Add an explicit `initiator` field to `pool.swap`:** The cleanest fix is for the pool's `swap` function to accept an explicit `initiator` address (analogous to how `addLiquidity` separates `msg.sender` payer from `owner`), and for the router to pass `msg.sender` (the user) in that field. The extension would then check that field instead of `sender`.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({
        pool: pool,
        recipient: attacker,
        zeroForOne: true,
        amountIn: X,
        ...
    })
  - Router calls pool.swap(attacker, true, X, ...)  [msg.sender = router]
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Swap executes; attacker receives output tokens

Result:
  - attacker successfully swapped against the curated pool
  - SwapAllowlistExtension never checked attacker's address
  - allowedSwapper[pool][attacker] == false was never consulted
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
