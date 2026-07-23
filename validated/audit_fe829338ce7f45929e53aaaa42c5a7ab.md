Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Caller, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address. If the pool admin allowlists the router (required for any allowlisted user to use the router), every non-allowlisted user can bypass the allowlist by routing through the router. The extension ignores `extensionData` entirely and has no mechanism to recover the real initiating user.

## Finding Description

`SwapAllowlistExtension.beforeSwap` checks `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The pool passes `msg.sender` of the `swap()` call as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← router address when user goes through router
    recipient, ...
);
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router `msg.sender` to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The `extensionData` parameter in `beforeSwap` is unnamed and completely ignored — the extension has no path to recover the real user identity. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

This creates an inescapable dilemma:
- **Do not allowlist the router** → allowlisted users cannot use the router at all
- **Allowlist the router** → every user bypasses the allowlist by routing through the router

## Impact Explanation

**High.** A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd users, institutional counterparties). Any unpermissioned user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` targeting the restricted pool. The allowlist guard silently fails open for all router-mediated swaps once the router is allowlisted, breaking the core access-control invariant of curated pools and allowing unauthorized fund flows through the pool.

## Likelihood Explanation

**Medium.** The router is the primary user-facing swap interface. Any pool admin who wants allowlisted users to use the standard router must allowlist the router address — a natural and expected administrative action. Once done, the bypass is immediately available to any address with no further preconditions, no special tokens, and no privileged access.

## Recommendation

The extension must identify the real initiating user, not the intermediary contract. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks this value only when `msg.sender` (the pool's caller) is a known trusted router.

2. **Trusted router registry in the extension**: The extension maintains a registry of trusted routers. When `sender` is a trusted router, it reads the actual user identity from `extensionData`; otherwise it checks `sender` directly.

Either approach requires the extension to be aware of the router layer — the allowlist must gate the economically relevant actor (the user), not the transport layer (the router).

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, userA, true)` and `setAllowedToSwap(pool, router, true)` (router allowlisted to enable legitimate use).
3. `userB` (not allowlisted) calls `router.exactInputSingle({pool: restrictedPool, ...})`.
4. The router calls `pool.swap(...)` — pool's `msg.sender` is the router.
5. `_beforeSwap(router, ...)` is called; extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `userB` successfully swaps in a pool they were never authorized to access. [1](#0-0) [2](#0-1) [3](#0-2)

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
