Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual end-user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension sees the router address rather than the actual end-user. If the pool admin allowlists the router to enable router-mediated swaps, every user on the network can bypass the allowlist gate unconditionally.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value as the first positional argument in the ABI call to each configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`, the router calls `pool.swap()` on the user's behalf, making the router `msg.sender` to the pool: [4](#0-3) 

The extension never sees the actual end-user. This creates an irresolvable dilemma: allowlisting the router lets every user bypass the allowlist; not allowlisting the router blocks all router-mediated swaps including those from legitimately allowlisted users.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the economic beneficiary), not `sender` (the operator/router): [5](#0-4) 

The asymmetry is the root cause: the deposit guard checks the right identity; the swap guard checks the wrong one.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise privileged addresses is fully bypassable. Any unprivileged user can call `MetricOmmSimpleRouter.exactInput()` (or any other router entry point) targeting the restricted pool. The router becomes the `sender` seen by the extension. If the router is allowlisted (the only way to make router-mediated swaps work at all), the allowlist check passes unconditionally for every user. The attacker receives pool output tokens and the pool receives input tokens — a complete bypass of the intended access control with direct fund-flow consequences (unauthorized parties execute swaps at oracle-anchored prices against a pool intended to be restricted).

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. The bypass requires only that the pool admin has allowlisted the router — a necessary operational step for the router to be usable at all on a restricted pool. No privileged access, no special setup, and no malicious token behavior is required. The trigger is a normal router swap call, reachable by any address on the network.

## Recommendation

The extension must gate on the identity that represents the economic actor, not the intermediary. Two options:

1. **Check `recipient` instead of `sender`** — the recipient is the address that receives output tokens and is typically the actual user. This mirrors how `DepositAllowlistExtension` gates on `owner`.

2. **Read the actual user from `extensionData`** — require the router to embed the original `msg.sender` in `extensionData`, and have the extension decode and verify it.

Option 1 is the minimal fix:

```solidity
// Current (wrong): gates on the direct pool caller (router)
function beforeSwap(address sender, address, ...)
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {

// Fixed: gate on the recipient (actual economic actor)
function beforeSwap(address, address recipient, ...)
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
```

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool with himself as `recipient`.
5. The router calls `pool.swap(bob, ...)` — the pool's `msg.sender` is the router.
6. `_beforeSwap(router, bob, ...)` is called; the extension checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite not being on the allowlist.

Foundry test plan: deploy pool with `SwapAllowlistExtension` as `beforeSwap` extension; allowlist only the router; call `exactInputSingle` from an unallowlisted EOA; assert the swap succeeds and the EOA receives output tokens. [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
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
