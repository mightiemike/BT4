Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router address, not the end-user. If a pool admin allowlists the router to enable router-mediated swaps for their curated users, every unprivileged address can bypass the individual allowlist and swap against the restricted pool. The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner passed explicitly), not `sender`.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs its gate as:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the first argument forwarded by the pool from its own `msg.sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), NOT the end-user
    recipient,
    ...
);
```

When `MetricOmmSimpleRouter.exactInputSingle` is called, the router itself calls `pool.swap()`:

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

`msg.sender` inside `pool.swap()` is the router, so the extension evaluates `allowedSwapper[pool][router]` — never the actual end-user. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the position owner passed explicitly by the pool), not `sender`, correctly gating the economic actor regardless of who calls `addLiquidity`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The swap extension has no analogous `swapper` parameter decoupled from `msg.sender`, making the bypass irresolvable at the extension level.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties is fully open to any address that routes through `MetricOmmSimpleRouter` once the admin allowlists the router. Unauthorized swappers can execute oracle-priced swaps against the restricted pool, exposing LPs to adverse selection and potential principal loss. This constitutes broken core pool functionality — the allowlist extension fails entirely to gate the intended actors when the standard periphery router is used.

## Likelihood Explanation
Medium. The bypass requires the pool admin to allowlist the router — a natural and expected configuration step for any admin who wants their allowlisted users to be able to use the standard periphery. The admin has no on-chain signal that doing so opens the pool to all users. The condition is reachable by any unprivileged address once the router is allowlisted, and is repeatable indefinitely.

## Recommendation
Check the actual end-user identity rather than the direct `pool.swap()` caller. Two viable approaches:

1. **Mirror the deposit pattern**: Introduce an explicit `swapper` parameter (analogous to `owner` in `addLiquidity`) that the pool passes through from the original user, decoupled from `msg.sender`. The extension then checks this parameter.
2. **Router forwards user identity in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it (requires enforcing that the caller is the trusted router via `msg.sender == router` check inside the extension).

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router
4. bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
   → router calls pool.swap(...)         [msg.sender inside pool.swap = router]
   → pool calls _beforeSwap(router, ...) [sender = router]
   → extension checks allowedSwapper[pool][router] → true
   → bob's swap succeeds despite not being individually allowlisted
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
