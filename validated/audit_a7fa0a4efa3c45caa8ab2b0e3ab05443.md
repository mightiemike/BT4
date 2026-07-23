All four cited files are confirmed in the repository. Let me verify the exact code matches the claims.

**`SwapAllowlistExtension.beforeSwap`** (L31-41): Confirmed — checks `allowedSwapper[msg.sender][sender]` where `sender` is the first argument. [1](#0-0) 

**`MetricOmmPool.swap`** (L230-231): Confirmed — passes `msg.sender` (the direct caller of `pool.swap()`) as the `sender` argument to `_beforeSwap`. [2](#0-1) 

**`MetricOmmSimpleRouter.exactInputSingle`** (L71-80): Confirmed — the router calls `pool.swap()` directly; the actual end-user (`msg.sender` of `exactInputSingle`) is stored only in transient storage via `_setNextCallbackContext`, never surfaced to the pool or extension. [3](#0-2) 

**`DepositAllowlistExtension.beforeAddLiquidity`** (L32-42): Confirmed — correctly checks `owner` (the actual economic actor), not the caller. [4](#0-3) 

All code references are accurate. The exploit path is fully reachable with no malicious setup required — the admin allowlisting the router is a necessary operational step, not an error. The finding is valid.

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Binds to Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` parameter, which is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual end-user. Any pool admin who allowlists the router (required for router-based swaps to function) simultaneously grants every user on the network the ability to bypass the per-user restriction by routing through the router. The `DepositAllowlistExtension` correctly gates on `owner` (the actual economic actor), making the two allowlist extensions inconsistent and creating a predictable misconfiguration trap.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

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

`MetricOmmPool.swap` passes `msg.sender` (the direct caller of `pool.swap()`) as `sender` to the extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // ← this is the router, not the end-user
  ...
```

When `MetricOmmSimpleRouter.exactInputSingle` is used, the router calls `pool.swap()`, so `msg.sender` seen by the pool — and forwarded as `sender` to the extension — is the router's address. The actual end-user is stored only in transient storage as the payer via `_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)` and is never surfaced to the pool or extension.

The forced dilemma for pool admins:
- Do NOT allowlist the router → router is completely unusable for this pool
- Allowlist the router → every user on the network can bypass per-user restrictions

There is no configuration that simultaneously supports router-based swaps AND enforces per-user restrictions.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the actual position owner), not on the caller of `pool.addLiquidity()`. An admin who observes this behavior will naturally assume the swap allowlist works the same way — it does not.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., trusted market makers, KYC-verified users, or whitelisted counterparties) provides zero protection once the router is allowlisted. Any unprivileged user can call `router.exactInputSingle()` and the extension will see `allowedSwapper[pool][router] == true`, granting the swap. The core access-control invariant of the extension — that only allowlisted addresses may swap — is broken. LP funds in a curated pool are exposed to the full universe of traders, defeating the protection the extension was deployed to provide. This constitutes broken core pool functionality (the allowlist extension's primary purpose) with direct fund-exposure impact on LP assets.

## Likelihood Explanation
Medium. The admin must allowlist the router for the router to function with the pool. Any pool that intends to support both router-based UX and per-user restrictions will inevitably allowlist the router, triggering the bypass. The inconsistency with `DepositAllowlistExtension` (which correctly checks `owner`) makes this a predictable misconfiguration. No special attacker capability is required — any user can call `router.exactInputSingle()`.

## Recommendation
The `beforeSwap` hook should gate on the actual economic actor — the address whose tokens are being spent — not the intermediary. Two approaches:

1. **Pass the payer through `extensionData`**: The router encodes the actual user address into `extensionData`; the extension decodes and checks it. This requires a coordinated change to the router and extension.

2. **Align with the deposit pattern**: Introduce a dedicated `swapper` or `payer` parameter in the `beforeSwap` hook signature (analogous to `owner` in `beforeAddLiquidity`) that the pool populates from a trusted source (e.g., the transient payer context set by the router), so the extension always sees the real economic actor regardless of the call path.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — intending only Alice to trade.
3. Admin calls `setAllowedToSwap(pool, router, true)` — to allow router-based swaps.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes successfully, bypassing the per-user allowlist entirely.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
