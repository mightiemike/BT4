Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller and token payer) and instead validates `owner` (the position beneficiary, a free parameter chosen by the caller). Because any caller can supply an allowlisted address as `owner`, the allowlist check always passes for that address regardless of who is actually depositing. This renders the deposit allowlist completely ineffective.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as distinct arguments into the extension hook: [1](#0-0) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and dropped. The allowlist check is performed only on `owner`: [2](#0-1) 

Since `owner` is a free parameter chosen by the caller, any address can pass an allowlisted address as `owner` and the guard approves the call, even though the actual depositor (`sender`) is not on the allowlist. By contrast, `SwapAllowlistExtension.beforeSwap` correctly validates `sender`: [3](#0-2) 

The asymmetry confirms that checking `sender` is the intended pattern for access control in this hook system.

## Impact Explanation
The deposit allowlist guard is completely ineffective. Any unprivileged address can inject liquidity into a restricted pool by nominating any allowlisted address as `owner`. The unauthorized depositor pays the tokens via the swap callback, the allowlisted address receives the position, and the pool accepts liquidity from an actor the admin explicitly excluded. This is a direct admin-boundary break via an unprivileged path, defeating regulatory gating, curated LP sets, or manipulation-prevention controls.

## Likelihood Explanation
The bypass requires only a single direct call to `pool.addLiquidity` with `owner` set to any known allowlisted address. No special privileges, flash loans, or oracle manipulation are needed. The allowlist state is publicly readable via `allowedDepositor`, so any observer can execute this immediately and repeatably.

## Recommendation
Change `beforeAddLiquidity` to validate `sender` (the actual depositor/payer) instead of `owner`:

```solidity
// Fixed:
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This mirrors the correct pattern already used in `SwapAllowlistExtension.beforeSwap`.

## Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension`. Admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
2. Bob calls `pool.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData)`.
3. The pool calls `_beforeAddLiquidity(sender=Bob, owner=alice, ...)`.
4. `DepositAllowlistExtension.beforeAddLiquidity` checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes and calls back to Bob (as `msg.sender`) to pay the tokens via the swap callback.
6. Bob pays the tokens; Alice receives the position. The pool has accepted liquidity from an address the admin explicitly excluded.
7. Alice (the position owner) can call `removeLiquidity` to withdraw — `removeLiquidity` enforces `msg.sender == owner`, so only Alice can withdraw, but the unauthorized deposit by Bob has already succeeded. [2](#0-1) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-206)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
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
