Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end user, allowing any caller to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap()` sets to its own `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. A pool admin who allowlists the router to support router-mediated swaps for permitted users inadvertently opens the gate to every caller of the router, completely defeating the allowlist.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← always the immediate caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap()` forwards `sender` unchanged to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then checks that value against its per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument — the router address when routing through `MetricOmmSimpleRouter`.

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly:

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

The router is `msg.sender` of `pool.swap()`, so the extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. Once the router is allowlisted, the check passes for every caller of the router regardless of whether that caller is on the intended allowlist. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

Existing guards are insufficient: the only check in `beforeSwap` is the `allowedSwapper` mapping keyed on `sender`, and there is no mechanism to recover the originating user from the call context.

## Impact Explanation

Any user not on the pool's swap allowlist can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter`. The pool's LP positions are exposed to counterparties the pool admin explicitly intended to exclude. This breaks the core invariant that the allowlist gates the economically relevant actor, and depending on the pool's oracle spread and fee configuration, allows unauthorized extraction of value from LP reserves — a direct loss of LP principal above Sherlock thresholds.

## Likelihood Explanation

The scenario requires no privileged action by the attacker. The only precondition is that the pool admin has allowlisted the router — a natural and expected configuration for any pool that intends to support router-mediated swaps for its permitted users. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle` with the target pool address and bypass the allowlist in a single transaction, repeatably.

## Recommendation

The extension must check the identity of the economic actor, not the immediate caller of `pool.swap()`. The cleanest fix is to have the router encode `msg.sender` into `extensionData` before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` decode and check that value instead of `sender`, falling back to `sender` when `extensionData` is empty (for direct pool calls). Alternatively, a dedicated field in the pool's call path could propagate the originating user, analogous to how `DepositAllowlistExtension` checks `owner` (the position owner explicitly supplied by the caller) rather than `msg.sender`.

## Proof of Concept

```
Setup:
  - Pool configured with SwapAllowlistExtension
  - Pool admin allowlists router: allowedSwapper[pool][router] = true
  - Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amountIn, priceLimitX64, "", "")
     → msg.sender of pool.swap() = router
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap(sender=router, ...) is called
     → checks allowedSwapper[pool][router] → true → passes
  5. Swap executes; attacker receives output tokens
  6. Pool calls metricOmmSwapCallback on router; router pulls tokens from attacker

Result: attacker swapped on a pool they are not allowlisted for.
```

Foundry test outline: deploy pool with `SwapAllowlistExtension`, call `setAllowedToSwap(pool, router, true)` and confirm `setAllowedToSwap(pool, attacker, false)`, then call `exactInputSingle` from `attacker` and assert the swap succeeds and `attacker` receives output tokens. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
