Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` receives `sender` as the address that called `pool.swap()`. When users interact through `MetricOmmSimpleRouter`, that address is the router contract, not the end user. Because the router must be allowlisted for any router-mediated swap to succeed, allowlisting the router grants every user on the network unrestricted access to a pool intended to be restricted, completely defeating per-user access control.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap() — the router when routed
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap()` then evaluates that `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

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

At this point `msg.sender` of `pool.swap()` is the router contract address. The extension therefore evaluates `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][end_user]`. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` from the router's context.

This creates a binary outcome for pool admins:
- **Router not allowlisted**: No user can swap through the router, even individually allowlisted ones — core swap functionality is broken for all router users.
- **Router allowlisted**: Every user on the network can swap through the router, completely defeating per-user access control.

There is no existing guard that recovers the original caller's identity. The `extensionData` field is passed through unchanged from the user's call, but the extension does not decode it to recover the real user — it only reads `sender` directly.

## Impact Explanation
A pool admin who deploys with `SwapAllowlistExtension` to create a restricted pool (e.g., institutional-only, KYC-gated, or counterparty-specific) cannot enforce per-user access control when users interact through `MetricOmmSimpleRouter`. Any non-allowlisted user can swap against the restricted pool by routing through the router, exposing LPs to unintended counterparties. Since the pool's liquidity is priced by an external oracle, adversarial flow from non-allowlisted users can extract value from LPs who believed they were protected by the allowlist. This constitutes a direct loss of LP principal through unauthorized swap execution against a pool designed to be restricted — matching the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" allowed impacts.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing interface for swaps. Any user who wants to swap without implementing `IMetricOmmSwapCallback` themselves must use the router. The bypass requires no special privileges, no malicious setup, and no non-standard tokens — only calling the router's standard `exactInputSingle` function with any pool that has `SwapAllowlistExtension` configured. Every non-allowlisted user who discovers the pool can exploit this immediately and repeatably.

## Recommendation
The extension must check the actual end user, not the intermediary router. The cleanest fix is to have the router encode `msg.sender` into `extensionData` in a standardized field, and have `SwapAllowlistExtension.beforeSwap()` decode and verify the real user from `extensionData` when `sender` is a known router address. Alternatively, require direct pool interaction (no router) for allowlisted pools and document this incompatibility, though this is fragile as new routers can be deployed.

## Proof of Concept
**Setup**: Pool deployed with `SwapAllowlistExtension`. Pool admin calls `setAllowedToSwap(pool, router_address, true)` (required for any user to swap via router). Alice (`0xAlice`) is NOT individually allowlisted.

**Attack**:
1. Alice calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ..., extensionData: ""})`.
2. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", "")` — `msg.sender` = router address.
3. Pool calls `_beforeSwap(router_address, ...)` at `MetricOmmPool.sol` L230.
4. Extension evaluates `allowedSwapper[pool][router_address]` → `true` → swap proceeds.
5. Alice successfully swaps against the restricted pool despite not being individually allowlisted.

**Foundry test plan**: Deploy pool with `SwapAllowlistExtension`, allowlist only the router address, attempt `exactInputSingle` from an address not in the allowlist, assert the swap succeeds (demonstrating the bypass). [1](#0-0) [2](#0-1) [3](#0-2)

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
