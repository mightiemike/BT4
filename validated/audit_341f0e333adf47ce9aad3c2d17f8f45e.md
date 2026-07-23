Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, enabling allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. This produces two fund-impacting failure modes: allowlisted users are blocked from using the router, and if the admin allowlists the router to restore access, every unprivileged user bypasses the per-user allowlist entirely.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [1](#0-0) 

`MetricOmmPool.swap()` populates `sender` with `msg.sender` of the pool call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // ← becomes `sender` in the extension
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
``` [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the explicit position owner parameter), not `sender` (the immediate caller), confirming the asymmetry is a defect in the swap extension: [4](#0-3) 

**Failure mode A:** Admin allowlists `user1`. `user1` calls `router.exactInputSingle(...)`. Extension checks `allowedSwapper[pool][router]` → `false` → `NotAllowedToSwap`. Allowlisted user cannot use the canonical periphery path.

**Failure mode B:** Admin allowlists the router to restore access: `setAllowedToSwap(pool, router, true)`. Now `allowedSwapper[pool][router]` is `true` for every call through the router. Any unprivileged user calls `router.exactInputSingle(...)` → extension passes unconditionally → the entire per-user allowlist is nullified for the router path.

## Impact Explanation
Failure mode A breaks core swap functionality for legitimate allowlisted users — a direct broken-swap impact. Failure mode B allows unprivileged actors to trade on a curated pool that was designed to be private, violating the pool's access-control invariant and enabling unrestricted arbitrage against LP positions on a pool intended to be restricted. Both outcomes meet the contest threshold: broken core pool functionality causing loss of funds or unusable swap flows, and admin-boundary break where an unprivileged path bypasses a pool admin's access control.

## Likelihood Explanation
The router is the canonical periphery entry point. Any pool admin who deploys `SwapAllowlistExtension` and wants allowlisted users to use the router will inevitably hit failure mode A, then attempt to fix it by allowlisting the router, triggering failure mode B. No privileged attacker is required — any public user with a standard router call reaches the bypass once the router is allowlisted. The path is fully reachable with unprivileged, standard transactions.

## Recommendation
The extension must check the economic actor, not the transport layer. The simplest correct fix mirrors `DepositAllowlistExtension`: require the real swapper identity to be passed as an explicit, verified parameter rather than relying on the transport-layer `msg.sender`. Two viable approaches:

1. **Pass the originating user through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks that address. Requires a convention between router and extension.
2. **Trusted-router registry:** The extension maintains a registry of trusted routers; for calls from a trusted router, it decodes the real user from `extensionData`; for direct calls, it checks `sender` as today.

## Proof of Concept
```solidity
// 1. Admin deploys pool with SwapAllowlistExtension
// 2. Admin allowlists the router so users can swap via periphery
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// 3. Attacker (never individually allowlisted) calls the router
router.exactInputSingle(ExactInputSingleParams({
    pool:             address(pool),
    tokenIn:          address(token0),
    recipient:        attacker,
    amountIn:         1_000e18,
    amountOutMinimum: 0,
    zeroForOne:       true,
    priceLimitX64:    type(uint128).max,
    deadline:         block.timestamp,
    extensionData:    ""
}));
// → pool.swap(msg.sender = router)
// → _beforeSwap(router, ...)
// → allowedSwapper[pool][router] == true → passes
// → attacker swaps on a pool they were never individually allowlisted for
```

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
