Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, enabling allowlist bypass and permanent loss of caller's deposited tokens — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual token payer, i.e., `msg.sender` of `addLiquidity`) and gates access on the caller-controlled `owner` parameter (the LP share recipient). Any unprivileged caller can bypass the allowlist by supplying an allowlisted address as `owner`. The pool collects tokens from the unauthorized caller via the liquidity callback and mints LP shares to `owner`, leaving the caller with zero shares and no recovery path.

## Finding Description
`DepositAllowlistExtension.beforeAddLiquidity` ignores its first parameter and checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

Because `owner` is a free parameter in `addLiquidity` with no restriction, any caller can set it to any allowlisted address. The extension then evaluates `allowedDepositor[pool][trustedLP]` → `true` and does not revert. `LiquidityLib.addLiquidity` proceeds, calling back on `msg.sender` (the unauthorized caller) to collect tokens and minting LP shares to `owner`.

Recovery is blocked because `removeLiquidity` enforces `msg.sender == owner`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L206
if (msg.sender != owner) revert NotPositionOwner();
```

The caller's tokens are permanently locked from their perspective. The correct pattern is already used in `SwapAllowlistExtension.beforeSwap`, which checks `sender` (the actual caller of `pool.swap`), not the `recipient`.

## Impact Explanation
**Direct loss of user principal:** The unauthorized caller pays tokens via the `addLiquidity` callback but receives zero LP shares. `removeLiquidity` requires `msg.sender == owner`, so the caller has no path to recover their principal — 100% of deposited tokens are permanently inaccessible to them.

**Admin-boundary break:** The pool admin's deposit allowlist is completely defeated. Any address can inject liquidity into a restricted pool by naming an allowlisted address as `owner`, altering pool depth and bin state without authorization. This satisfies the contest's "Admin-boundary break" and "direct loss of user principal" impact criteria at Medium/High severity.

## Likelihood Explanation
- Requires zero special permissions; any EOA or contract can call `pool.addLiquidity` directly.
- Allowlisted addresses are publicly readable from the `allowedDepositor` mapping.
- Can be triggered accidentally (wrong `owner` address) or deliberately (griefing, pool manipulation).
- No router intermediary prevents direct pool access; `MetricOmmSimpleRouter` does not call `addLiquidity`.

## Recommendation
Check `sender` (the actual token payer) instead of `owner` (the LP share recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

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
1. Pool is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
2. Admin calls `setAllowedToDeposit(pool, trustedLP, true)` — only `trustedLP` is authorized.
3. Unauthorized `attacker` reads `allowedDepositor[pool]` storage, finds `trustedLP`.
4. `attacker` calls `pool.addLiquidity(owner=trustedLP, salt=0, deltas=..., callbackData=..., extensionData=...)`.
5. Pool calls `_beforeAddLiquidity(sender=attacker, owner=trustedLP, ...)`.
6. Extension evaluates `allowedDepositor[pool][trustedLP]` → `true` → no revert.
7. `LiquidityLib.addLiquidity` runs: pool calls back on `attacker` to collect tokens; LP shares are minted to `trustedLP`.
8. `attacker` has paid tokens and holds zero LP shares. `removeLiquidity` requires `msg.sender == owner`, so `attacker` cannot recover funds.
9. `trustedLP` holds unexpected LP shares they did not request. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
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
