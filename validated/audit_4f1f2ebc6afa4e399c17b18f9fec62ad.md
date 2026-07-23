The code confirms all cited facts. The finding is valid.

Audit Report

## Title
`removeLiquidity()` and `addLiquidity()` Bypass `whenNotPaused` Guard, Enabling LP Fund Extraction During Emergency Pause — (File: `metric-core/contracts/MetricOmmPool.sol`)

## Summary
`swap()` is protected by the `whenNotPaused` modifier, which reverts whenever `pauseLevel != 0`. Neither `addLiquidity()` nor `removeLiquidity()` carry this guard — only `nonReentrant`. Any LP can call `removeLiquidity()` while the pool is paused, extracting their proportional share of `binTotals.scaledToken0`/`scaledToken1` while swappers and other LPs are locked out. If the pause was triggered because a prior swap corrupted bin accounting, early withdrawers extract a disproportionate share, leaving the pool insolvent for remaining LPs.

## Finding Description
`_checkNotPaused()` reverts on any non-zero `pauseLevel`: [1](#0-0) 

The `whenNotPaused` modifier wraps this check: [2](#0-1) 

`swap()` applies `whenNotPaused`: [3](#0-2) 

`addLiquidity()` carries only `nonReentrant` — no pause check: [4](#0-3) 

`removeLiquidity()` carries only `nonReentrant` — no pause check: [5](#0-4) 

`pauseLevel` is set exclusively by the factory via `setPause()`: [6](#0-5) 

The exploit path: (1) a swap executes at a manipulated oracle price, corrupting per-bin `token0BalanceScaled`/`token1BalanceScaled` and the aggregate `binTotals`; (2) the admin observes the anomaly and calls `setPause(1)`, emitting `PauseLevelUpdated`; (3) a monitoring bot or MEV searcher observing the event immediately calls `removeLiquidity(owner, salt, deltas, "")` — the call succeeds because no `whenNotPaused` check exists; (4) `LiquidityLib.removeLiquidity()` computes the caller's share of the now-corrupted `binTotals` and transfers tokens out; (5) remaining LPs attempt withdrawal after the pause is lifted and find `binTotals` no longer covers their claims — pool insolvency.

The `msg.sender != owner` check at L206 only prevents one address from withdrawing on behalf of another; it does not restrict withdrawal during a pause. No other guard in `removeLiquidity()` or `LiquidityLib.removeLiquidity()` checks `pauseLevel`.

## Impact Explanation
Direct loss of LP principal and pool insolvency: `binTotals.scaledToken0`/`scaledToken1` no longer cover remaining LP claims after a first-mover extracts during the pause window. This matches the allowed impact gate: **pool insolvency — balances fail to cover LP claims**, and **broken core pool functionality causing loss of funds**.

## Likelihood Explanation
The trigger is an on-chain `PauseLevelUpdated` event, immediately visible to any monitoring bot or MEV searcher. No special privilege is required — any address holding LP shares in any bin can call `removeLiquidity()` with `owner == msg.sender`. The attack window spans from the pause transaction until remediation, which can be many blocks. Likelihood: **Medium** (requires a prior pause event, but the extraction is trivially executable by any LP once the pause is observed).

## Recommendation
Add `whenNotPaused` to both `addLiquidity()` and `removeLiquidity()`, consistent with `swap()`:

```solidity
function addLiquidity(...) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) ...

function removeLiquidity(...) external whenNotPaused nonReentrant(PoolActions.REMOVE_LIQUIDITY) ...
```

This ensures all pool-state-mutating operations are frozen when `pauseLevel != 0`, matching the documented intent of the pause mechanism.

## Proof of Concept
1. Deploy pool with a mutable price provider. Manipulate the oracle to return an extreme bid/ask; execute a swap that moves tokens between bins at incorrect prices, corrupting `binTotals`.
2. Admin calls `setPause(1)` via factory. `PauseLevelUpdated(0, 1)` is emitted.
3. In the same or next block, call `removeLiquidity(owner, salt, deltas, "")` where `owner == msg.sender` and `deltas` covers all bins where the LP holds shares. The call succeeds — no `whenNotPaused` check.
4. `LiquidityLib.removeLiquidity()` computes the LP's share of corrupted `binTotals` and transfers tokens out.
5. After the pause is lifted, remaining LPs call `removeLiquidity()`; `binTotals` no longer covers their claims — revert or under-payment, confirming pool insolvency.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L174-177)
```text
  modifier whenNotPaused() {
    _checkNotPaused();
    _;
  }
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L455-461)
```text
  function setPause(uint8 newLevel) external onlyFactory {
    if (newLevel > 2) revert InvalidPauseLevel();
    if (newLevel == pauseLevel) return;
    uint8 prev = pauseLevel;
    pauseLevel = newLevel;
    emit PauseLevelUpdated(prev, newLevel);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
  }
```
