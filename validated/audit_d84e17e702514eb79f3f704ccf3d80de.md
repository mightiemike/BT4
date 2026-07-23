Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity()` checks `owner` instead of `sender`, allowing any non-allowlisted address to bypass the deposit gate — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity()` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`, i.e., the token payer) and validates only `owner` (the LP-share recipient) against the allowlist. Because `MetricOmmPool.addLiquidity()` explicitly supports an operator pattern where `msg.sender` need not equal `owner`, any non-allowlisted address can call `pool.addLiquidity(owner = allowlisted_address, ...)` directly, pass the allowlist check, and deposit tokens into a KYC/compliance-gated pool without ever being allowlisted.

## Finding Description
`MetricOmmPool.addLiquidity()` calls `_beforeAddLiquidity(msg.sender, owner, ...)` at line 191, passing the actual caller as `sender` and the position recipient as `owner`. [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity()` then encodes and forwards both arguments to the extension: `abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))`. [2](#0-1) 

The `IMetricOmmExtensions` interface defines `beforeAddLiquidity(address sender, address owner, ...)` with `sender` as the first parameter. [3](#0-2) 

However, `DepositAllowlistExtension.beforeAddLiquidity()` declares the first parameter unnamed (discarded) and checks only `owner`: [4](#0-3) 

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The pool's own NatSpec explicitly documents the operator pattern: "`msg.sender` pays but need not equal `owner`". [5](#0-4) 

This means a non-allowlisted `alice` can call `pool.addLiquidity(owner = bob, ...)` where `bob` is allowlisted. The extension evaluates `allowedDepositor[pool][bob] == true` and passes, while `alice` (the actual token payer) is never checked. Alice's callback pays tokens into the pool; bob receives the LP shares.

By contrast, `SwapAllowlistExtension.beforeSwap()` correctly checks `sender` (the actual swap initiator), not `recipient`. [6](#0-5) 

## Impact Explanation
The `DepositAllowlistExtension` is the production KYC/compliance gate for pools requiring access control on who may add liquidity. With this bug, the gate is entirely ineffective: any address can deposit tokens into a gated pool by nominating any allowlisted address as `owner`. The non-allowlisted actor pays the tokens; the allowlisted address receives the LP position. Pool token balances grow from unauthorized sources, directly violating the pool admin's access-control invariant. This constitutes a broken core pool functionality / admin-boundary break with direct fund-flow consequences (tokens enter the pool from unauthorized sources).

## Likelihood Explanation
The pool is a standalone contract callable directly without going through `MetricOmmPoolLiquidityAdder`. The allowlist mappings (`allowedDepositor`) are public, so any attacker can trivially identify an allowlisted address. No privileged access, special setup, or non-standard tokens are required. The exploit is repeatable by any address at any time on any pool using this extension.

## Recommendation
Replace the `owner` check with the `sender` parameter (the actual caller of `addLiquidity`), mirroring the correct pattern in `SwapAllowlistExtension`:

```solidity
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
1. Deploy a pool with `DepositAllowlistExtension` as the `beforeAddLiquidity` hook.
2. Admin calls `setAllowedToDeposit(pool, bob, true)` — only `bob` is allowlisted.
3. `alice` (not allowlisted) calls `pool.addLiquidity(owner = bob, salt, deltas, callbackData, extensionData)` directly.
4. Pool calls `_beforeAddLiquidity(sender = alice, owner = bob, ...)` → extension receives `(alice, bob, ...)`.
5. Extension ignores `alice`, evaluates `allowedDepositor[pool][bob] == true` → no revert.
6. Pool proceeds: alice's `IMetricOmmModifyLiquidityCallback` pays tokens into the pool; bob receives LP shares.
7. Alice has deposited into a gated pool without being on the allowlist. [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
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
