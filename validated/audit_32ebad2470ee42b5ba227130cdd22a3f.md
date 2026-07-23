All four code references check out exactly as claimed. Let me confirm each link in the exploit chain:

1. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the first argument — the immediate caller of `pool.swap()`. [1](#0-0) 

2. `MetricOmmPool.swap()` passes `msg.sender` (the direct caller) as `sender` to `_beforeSwap`. [2](#0-1) 

3. `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension. [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender` in the pool context — not the end user. [4](#0-3) 

The dilemma is real and confirmed: if the admin does not allowlist the router, even allowlisted users cannot swap through it; if the admin does allowlist the router, every user bypasses the per-user restriction.

---

Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of the pool's `swap()` call — the immediate caller, not the originating user. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user — including those explicitly excluded — can bypass the per-user allowlist by calling the router. There is no configuration that simultaneously permits router-mediated swaps and enforces per-user restrictions.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmPool.swap()` passes `msg.sender` (the direct caller of `swap()`) as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-232
_beforeSwap(
    msg.sender,   // immediate caller of pool.swap()
    ...
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension via `abi.encodeCall`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

So the extension sees `sender = address(router)`, not the end user. The pool admin faces an impossible choice: (a) not allowlist the router — allowlisted users cannot swap through the router; (b) allowlist the router — every user, including those explicitly excluded, can bypass the per-user allowlist. Existing guards (`allowedSwapper`, `allowAllSwappers`) are insufficient because they operate on the wrong identity.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners) is fully bypassed by any user who calls `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The disallowed user trades against LP liquidity at oracle-derived prices, extracting value from LP positions deposited under the assumption that only approved counterparties could trade. This constitutes a direct loss of LP principal and owed fees — the exact impact class the allowlist was designed to prevent. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard public periphery entrypoint for swaps. Any user who discovers a curated pool can call the router directly with no privileged access, no special setup, and no non-standard token behavior. The trigger is a single public transaction. The only precondition is that the pool admin has allowlisted the router (which is required for any legitimate router-mediated swap to work), making this condition likely in any real deployment.

## Recommendation
The `beforeSwap` hook must gate the end user, not the immediate caller. Two viable approaches:

1. **Trusted-router registry with `extensionData` user field**: The extension reads a registry of trusted routers; when `sender` is a trusted router, it extracts and checks the real user from `extensionData` (encoded by the router). This is the correct production fix.
2. **Document incompatibility**: Restrict the allowlist to direct pool callers only and document that router-mediated swaps are incompatible with `SwapAllowlistExtension`. This is the simplest safe fix but limits usability.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, address(router), true)` — required so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `_beforeSwap` passes `sender = router` to `SwapAllowlistExtension.beforeSwap`.
7. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob successfully trades on the curated pool, bypassing the per-user allowlist.

If the admin omits step 3, Alice also cannot use the router — confirming the extension is incompatible with the standard periphery in either configuration.

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
