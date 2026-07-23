Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the originating user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the immediate `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router contract becomes `msg.sender` of `pool.swap()`, so the extension checks whether the **router** is allowlisted rather than the originating user. If the pool admin allowlists the router (required for any user to use it), every unprivileged user can bypass the curated swap allowlist by routing through the router, gaining unauthorized access to a pool intended to restrict swaps to a curated set of addresses.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
    extensionData
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the allowlist for the calling pool (`msg.sender` inside the extension = the pool):

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly with itself as `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...,
        params.extensionData
    );
```

At this call site `msg.sender` = router contract. The pool therefore passes `sender = router` to the extension. The extension checks `allowedSwapper[pool][router]`. The actual originating user's address is never consulted. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**The dilemma this creates for pool admins:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | No user can reach the pool through the router |
| **Allowlist the router** | Every user — including those explicitly excluded — can bypass the gate by routing through the router |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

## Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers). Any unprivileged user can bypass this restriction entirely by calling `MetricOmmSimpleRouter.exactInputSingle` (or the multi-hop variants). The bypass requires no special privilege, no admin action, and no non-standard token behavior — only the publicly deployed router. The result is direct unauthorized access to a pool whose core invariant is restricted swap access, which can cause loss of LP principal if the pool's liquidity was sized or priced for a controlled counterparty set. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" allowed impact criteria.

## Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the standard, publicly deployed periphery swap entry point. Any user who discovers the allowlist restriction on a direct `pool.swap()` call will naturally try the router as an alternative path. No privileged access, no special setup, and no malicious initial configuration is required. The attack is repeatable and requires only knowledge of the router's address and the pool's address.

## Recommendation

The `SwapAllowlistExtension` must gate the **originating user**, not the immediate caller of `pool.swap()`. The cleanest fix is to have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` before forwarding to the pool, and have `SwapAllowlistExtension.beforeSwap` decode and verify it from `extensionData` when the immediate `sender` is a known router. This ensures the allowlist always gates the originating EOA regardless of routing path. Alternatively, document that pools using `SwapAllowlistExtension` must not allowlist the router and that allowlisted users must call `pool.swap()` directly — though this is operationally fragile and breaks the standard periphery flow.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in beforeSwap order
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router must be allowed for alice to use it
  - bob is NOT on the allowlist

Attack:
  1. bob calls pool.swap(...) directly
     → SwapAllowlistExtension checks allowedSwapper[pool][bob] → false → reverts NotAllowedToSwap ✓

  2. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
     → router calls pool.swap() with msg.sender = router
     → pool calls _beforeSwap(sender=router, ...)
     → SwapAllowlistExtension checks allowedSwapper[pool][router] → true → PASSES ✗
     → bob's swap executes on the curated pool without authorization
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
