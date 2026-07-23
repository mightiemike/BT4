Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of the pool's `swap()` call — the immediate caller, not the end user. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract address. Any pool admin who allowlists the router to enable normal UX for their permitted users simultaneously grants unrestricted swap access to every unpermissioned user who calls the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs its check as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded from `ExtensionCalling._beforeSwap`, which passes `msg.sender` of the pool's own `swap()` invocation — i.e., whoever called `pool.swap()` directly.

In `MetricOmmPool.swap()` (L230–240), `_beforeSwap(msg.sender, ...)` is called, so `sender` inside the extension equals the immediate caller of `pool.swap()`.

In `MetricOmmSimpleRouter.exactInputSingle()` (L71–80), the router calls `IMetricOmmPoolActions(params.pool).swap(...)` directly. At that point `msg.sender` inside `pool.swap()` is the **router address**, not the end user. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same applies to `exactInput` (L103–112), `exactOutputSingle` (L135–137), and `exactOutput` (L165–181) — all call `pool.swap()` directly with the router as `msg.sender`.

There is no mechanism in the router to restrict which users it forwards, and the extension receives no information about the actual caller behind the router. Once the router is allowlisted, the allowlist is completely defeated for all router-mediated swaps.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to enforce restricted access (KYC-gated, whitelist-only) is rendered fully open to any user the moment the pool admin allowlists the router. The access control boundary set by the pool admin is silently broken: any non-allowlisted user can execute swaps against the pool via the router, bypassing the intended policy entirely. This is a direct admin-boundary break — the pool admin's configured access control is circumvented by an unprivileged path through a public, permissionless contract.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who deploy a restricted pool and want their allowlisted users to have normal UX will naturally allowlist the router. The router is public and permissionless with no user-level access controls. The misconfiguration is not apparent from the extension's interface or documentation, and the bypass requires only a standard router call from any EOA.

## Recommendation
The `beforeSwap` hook must receive the original user identity, not the intermediary caller. Two approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. Requires a coordinated convention between router and extension.
2. **Transient-storage `realSender`**: The router writes `msg.sender` to a transient storage slot before calling `pool.swap()`; the pool reads it and passes it alongside `sender` to the extension. The extension checks `realSender` when non-zero, falling back to `sender` for direct calls.

Either approach ensures the allowlist gates the economically relevant actor regardless of which periphery path is used.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // KYC'd user
  - Pool admin calls setAllowedToSwap(pool, router, true)  // so alice can use the router

Attack:
  1. bob (non-allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router executes: IMetricOmmPoolActions(pool).swap(recipient, ..., extensionData)
     → msg.sender inside pool.swap() == router address.
  3. MetricOmmPool._beforeSwap(sender=router, ...) is dispatched.
  4. SwapAllowlistExtension.beforeSwap checks:
       allowedSwapper[pool][router] == true  →  passes.
  5. bob's swap executes; allowlist is bypassed.

Result: bob, a non-allowlisted user, successfully swaps on a pool
        intended to be restricted to alice only.
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
