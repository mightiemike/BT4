Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of its own `swap()` call. When `MetricOmmSimpleRouter` routes a swap, the router contract becomes `msg.sender` to the pool, so the extension checks whether the router is allowlisted rather than the actual end-user. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, the allowlist is completely bypassed for every user on-chain.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool (correct) and `sender` is the address the pool received as `msg.sender` when `swap()` was called.

`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:
```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` to the pool:
```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The pool admin faces two equally broken outcomes: (1) do not allowlist the router → legitimate allowlisted users cannot swap through the router at all; (2) allowlist the router → the allowlist is completely bypassed for every user. There is no mechanism in the current design to thread the original end-user address through the router to the extension.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers, or protocol-controlled addresses) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. Once the pool admin allowlists the router, the allowlist provides zero protection. Unauthorized users can execute swaps against the pool's LP positions, violating the pool admin's access control intent and exposing LP positions to unauthorized counterparties. This constitutes a broken core pool access-control functionality and an admin-boundary break via an unprivileged path.

## Likelihood Explanation
The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who deploys a restricted pool and then tries to make it usable via the router will naturally add the router to the allowlist. The misconfiguration is not obvious from the extension's interface or documentation, and the unit tests for `SwapAllowlistExtension` only test direct pool calls (not router-mediated calls), so the bypass is not caught by the existing test suite. The likelihood is medium-high.

## Recommendation
The `beforeSwap` hook should receive and check the original end-user address, not the direct caller of `pool.swap()`. Two approaches:
1. **Pass the original payer through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` before calling the pool. `SwapAllowlistExtension` decodes and checks this address.
2. **Add an `originator` field to the swap interface**: Extend `IMetricOmmPoolActions.swap()` with an explicit `originator` parameter that the router sets to `msg.sender` (the end user). The pool passes this to `_beforeSwap` alongside `sender`. The extension checks `originator` instead of `sender`.

Option 2 is cleaner and avoids relying on `extensionData` conventions. Until fixed, pools using `SwapAllowlistExtension` should not allowlist the router and should document that router-mediated swaps are incompatible with the allowlist guard.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow legitimate users to swap via the router).
  - Pool admin does NOT add attacker's address to the allowlist.

Attack:
  1. Attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(
       pool=restrictedPool, tokenIn=..., amountIn=..., ...
     ).
  2. Router calls restrictedPool.swap(recipient, ...) with msg.sender = router.
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Attacker receives output tokens.

Result:
  Attacker bypasses the allowlist and swaps on a restricted pool
  without authorization.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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
