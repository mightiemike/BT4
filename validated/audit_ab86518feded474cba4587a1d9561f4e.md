Audit Report

## Title
SwapAllowlistExtension checks router address instead of original swapper, allowing allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` from the pool's perspective. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the original user. If the pool admin allowlists the router — a necessary operational step for any allowlisted user to reach the pool via the router — every unprivileged user can bypass the allowlist by calling the same router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the first argument to `_beforeSwap`: [1](#0-0) 

When the call originates from `MetricOmmSimpleRouter.exactInputSingle`, `msg.sender` inside the pool is the router contract. The router calls `pool.swap` with no mechanism to forward the original caller's identity to the pool or extension: [2](#0-1) 

The original caller (`msg.sender`) is stored only in transient storage for the payment callback context, not passed to the pool's swap function or extension hook.

`SwapAllowlistExtension.beforeSwap` then evaluates: [3](#0-2) 

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router so that their KYC'd users can reach the pool, this condition is satisfied for **every** caller of the router, regardless of individual allowlist status.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified users, institutional partners) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against LP liquidity, exposing LPs to adverse selection from counterparties the pool was explicitly designed to exclude. This constitutes direct loss of LP principal above contest thresholds for pools with meaningful TVL.

## Likelihood Explanation
Medium. The bypass requires the pool admin to allowlist the router — a natural and expected operational step for any pool that wants its allowlisted users to access the router. The admin is not acting maliciously; they are unaware that allowlisting the router is equivalent to opening the pool to all users. The extension's NatSpec states it "Gates `swap` by swapper address, per pool," which a reasonable admin would interpret as gating the original user, not the intermediary router. [4](#0-3) 

## Recommendation
The extension must check the identity of the economic actor, not the intermediary. Two options:

1. **Pass original sender through extension data**: The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a coordinated router+extension upgrade.
2. **Router registry with forwarded identity**: The extension maintains a mapping of trusted routers; when `sender` is a trusted router, it requires the original user's address to be supplied and verified via `extensionData`.

The simplest safe default is to treat any unrecognized `sender` (i.e., a router not individually allowlisted as a direct swapper) as blocked, and require pools that want router support to use a router that forwards the original caller identity.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` wired into `beforeSwap`.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — allowlisting a KYC'd user.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — allowlisting the router so `userA` can use it.
4. `userB` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`. [5](#0-4) 

6. Pool calls `_beforeSwap(router, ...)` and forwards to `extension.beforeSwap(sender=router, ...)`. [6](#0-5) 

7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `userB`'s swap executes against LP liquidity despite never being individually allowlisted. [7](#0-6)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-11)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
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
