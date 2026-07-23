Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks the router address — not the originating user. Any pool that allowlists the router (required for router-mediated swaps) is fully open to all users, defeating the curated allowlist.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient, zeroForOne, amountSpecified,
    priceLimitX64, packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension. `SwapAllowlistExtension.beforeSwap` then checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` is called, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The real user (`msg.sender`) is stored only in transient callback context for payment settlement and is never forwarded to the pool or extension. The pool's `msg.sender` is the **router**, so `sender` delivered to `beforeSwap` is the **router address**.

**Bypass path:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists only Alice: `allowedSwapper[pool][alice] = true`.
2. To allow Alice to swap via the router, admin must also add the router: `allowedSwapper[pool][router] = true`.
3. Non-allowlisted Bob calls `router.exactInputSingle(...)`. The extension sees `sender = router`, which is allowlisted → check passes → Bob's swap executes.

No configuration simultaneously allows router-mediated swaps for allowlisted users and blocks non-allowlisted users.

## Impact Explanation

Any user can trade on a curated pool designed to restrict swaps to a specific set of addresses. The pool admin's access control policy is silently bypassed, enabling unauthorized swaps against pool liquidity. This directly impacts LPs if the pool was curated to exclude certain counterparties (e.g., arbitrageurs, sanctioned addresses, or non-KYC'd users). This is a High severity direct policy bypass with fund-impacting consequences on curated pools.

## Likelihood Explanation

High. The router is the standard periphery entrypoint for swaps. Any pool admin who wants allowlisted users to swap via the router must add the router to the allowlist — at which point the bypass is immediately and unconditionally available to all users. The condition is inherent to the design, not an edge case.

## Recommendation

The extension must gate on the economically relevant actor — the originating user — not the direct caller of `pool.swap`. Two viable approaches:

1. **Pass the original user through the pool**: Modify the pool's `swap` interface to accept an explicit `swapper` address (verified against `msg.sender` or a trusted router), and forward it as `sender` to extensions.
2. **Router-level identity forwarding**: Have the router encode the real user's address in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it — but this requires the extension to trust the router, reintroducing a trust assumption that must be carefully managed.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin: allowedSwapper[pool][alice] = true
  - Pool admin: allowedSwapper[pool][router] = true  ← required for Alice to use router

Attack (Bob, not allowlisted):
  1. Bob calls router.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, ...)
     → pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. Extension checks: allowedSwapper[pool][router] == true → PASSES
  5. Bob's swap executes on the curated pool

Result: Bob bypasses the allowlist entirely by routing through the router.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
