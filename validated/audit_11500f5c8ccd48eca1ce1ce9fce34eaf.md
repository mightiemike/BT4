Audit Report

## Title
SwapAllowlistExtension checks router address instead of original user, enabling allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the immediate caller of `pool.swap`. When `MetricOmmSimpleRouter` executes any swap, it is the direct caller of `pool.swap`, so `sender = router` for every hop. A pool admin who allowlists the router to support router-based swaps inadvertently grants every user — including individually blocked ones — the ability to bypass the allowlist entirely.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this unchanged to every configured extension as `sender`. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap`. In `MetricOmmSimpleRouter.exactInput`, the router calls `pool.swap` directly for every hop:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:104-112
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
  .swap(
    i == last ? params.recipient : address(this),
    zeroForOne,
    amount,
    ...
  );
```

The pool therefore sees `msg.sender = router` for every hop. The original `msg.sender` of `exactInput` (the end user) is never forwarded to the extension. The `_setNextCallbackContext` payer tracking is separate and only used for payment settlement, not for the allowlist check.

A pool admin who wants to support router-based swaps while still gating individual users will call `setAllowedToSwap(pool, router, true)`. Once set, `allowedSwapper[pool][router] = true` causes the check to pass for every user who routes through the router, regardless of whether that user is individually blocked or never allowlisted.

## Impact Explanation

The `SwapAllowlistExtension` is the sole mechanism for pool-level swap curation. When the router is allowlisted, the extension provides zero protection against unauthorized swappers who route through the router. Any address — including one explicitly excluded from the allowlist — can swap through every allowlist-gated pool in a multi-hop path in a single transaction. This breaks the core pool access-control functionality the extension is designed to enforce, allowing unauthorized parties to execute swaps in restricted pools.

## Likelihood Explanation

Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants to support router-based swaps will allowlist the router — this is the natural and expected configuration. The bypass requires no special privileges: the attacker only needs to call the public `router.exactInput` function. The condition is reachable by any unprivileged trader the moment the router is allowlisted on any pool.

## Recommendation

Two options:

1. **Document the limitation clearly**: State that `SwapAllowlistExtension` gates the immediate caller of `pool.swap`, not the end user. Pool admins must allowlist individual users and require them to call the pool directly, not through the router. Do not allowlist the router if per-user gating is required.

2. **Pass original user identity through `extensionData`**: Have the router encode `msg.sender` into `extensionData` for each hop, and have the extension decode and verify it. This requires coordinated changes to both `MetricOmmSimpleRouter` and `SwapAllowlistExtension`, and the extension must authenticate that the encoded identity was set by a trusted router.

## Proof of Concept

```solidity
// Setup:
// poolA and poolB both have SwapAllowlistExtension configured
// allowedSwapper[poolA][router] = true   (admin allowlists router to support routing)
// allowedSwapper[poolB][router] = true
// allowedSwapper[poolA][attacker] = false  (attacker never individually allowlisted)
// allowedSwapper[poolB][attacker] = false

// Attacker calls:
router.exactInput(ExactInputParams({
    tokens: [tokenA, tokenB, tokenC],
    pools: [poolA, poolB],
    ...
}));
// Hop 0: poolA.swap called with msg.sender = router
//   → _beforeSwap(sender=router, ...) → allowedSwapper[poolA][router] = true → PASSES
// Hop 1: poolB.swap called with msg.sender = router
//   → _beforeSwap(sender=router, ...) → allowedSwapper[poolB][router] = true → PASSES
// Attacker completes the swap despite being individually blocked on both pools.
```

The same bypass applies to `exactInputSingle`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` directly, so `sender = router` in every case. [1](#0-0) [2](#0-1) [3](#0-2)

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
