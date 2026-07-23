Audit Report

## Title
Router-Mediated Swaps Pass Router Address as `sender` to `SwapAllowlistExtension::beforeSwap`, Breaking Allowlist Identity Gating — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension::beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the address the pool received as its `msg.sender`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the user. The hook therefore checks whether the router is allowlisted rather than the actual user, making the per-address allowlist unenforceable for all router-mediated swaps.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook.**

`MetricOmmPool::swap` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling::_beforeSwap` encodes that value directly as the `sender` argument forwarded to every registered extension: [2](#0-1) 

**Step 2 — The router is the pool's `msg.sender`.**

`MetricOmmSimpleRouter::exactInputSingle` calls `pool.swap(...)` directly with no indirection, so the pool sees `msg.sender = router`: [3](#0-2) 

**Step 3 — The hook checks the wrong identity.**

`SwapAllowlistExtension::beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is the router (wrong — should be the originating user): [4](#0-3) 

This produces two concrete failure modes:

| Scenario | Result |
|---|---|
| Pool admin allowlists the router to enable router swaps | Every user bypasses the allowlist by routing through the router |
| Pool admin does not allowlist the router | Allowlisted users cannot use the router at all |

## Impact Explanation

`SwapAllowlistExtension` is designed to restrict swaps to a specific set of addresses per pool. When `MetricOmmSimpleRouter` is used — the canonical public swap interface — that restriction is either completely bypassed (router allowlisted) or incorrectly blocks all allowlisted users (router not allowlisted). Pool designers who deploy this extension to gate permissioned pools cannot enforce the intended access control for any router-mediated swap. This constitutes broken core functionality of the extension, with a direct path to unauthorized swap execution.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard UX entry point for swaps. Any allowlisted user who uses the router will have their swap reverted. Any pool admin who allowlists the router to restore functionality inadvertently opens the pool to all users. The misconfiguration is a near-certain operational outcome requiring no special attacker capability — a normal user calling `exactInputSingle` triggers it.

## Recommendation

The `beforeSwap` hook must not rely solely on the `sender` parameter to identify the actual swapper when a router intermediary is involved. Concrete options:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension reads and verifies it, gating on a trusted-router check.
2. **Use `tx.origin` with caution**: Only acceptable if the extension explicitly documents and accepts the associated risks.
3. **Document incompatibility**: Warn pool admins that `SwapAllowlistExtension` must not be used on pools accessible via `MetricOmmSimpleRouter` until the sender-forwarding mechanism is fixed.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; allowlist only alice.
2. Pool admin also allowlists the router (to let alice use it).
3. Bob (not allowlisted) calls MetricOmmSimpleRouter::exactInputSingle targeting the pool.
4. Router calls pool.swap(...); pool's msg.sender = router.
5. beforeSwap receives sender = router; allowedSwapper[pool][router] = true → passes.
6. Bob's swap executes despite not being allowlisted.

Alternatively (without step 2):
- Alice calls the router; allowedSwapper[pool][router] = false → reverts,
  even though alice is explicitly allowlisted.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
