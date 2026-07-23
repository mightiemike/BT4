Audit Report

## Title
`addLiquidity()` Bypasses `whenNotPaused` Guard, Allowing Deposits Into a Paused Pool — (File: `metric-core/contracts/MetricOmmPool.sol`)

## Summary
`MetricOmmPool` defines a `whenNotPaused` modifier backed by `_checkNotPaused()` and a `pauseLevel` state variable, but applies the modifier exclusively to `swap()`. The `addLiquidity()` function carries no pause guard, so any user can deposit principal into a pool that has been paused due to an oracle failure, price manipulation, or active exploit, exposing those tokens to the exact condition that triggered the pause.

## Finding Description
`pauseLevel` is declared at L72 and `_checkNotPaused()` reverts when it is non-zero (L643–645). The `whenNotPaused` modifier wraps that check (L174–177). `swap()` at L224 is correctly decorated with `whenNotPaused nonReentrant(PoolActions.SWAP)`. However, `addLiquidity()` at L182–196 carries only `nonReentrant(PoolActions.ADD_LIQUIDITY)` — the pause guard is absent. `setPause()` at L455–461 is callable by the factory (which the pool admin can trigger) and sets `pauseLevel` to 1 or 2. Once set, every `swap()` call reverts with `PoolPaused`, but `addLiquidity()` proceeds normally, transferring tokens into the pool. The `_beforeAddLiquidity` extension hook (e.g., a deposit allowlist) is still invoked, but it does not substitute for the pause check and is not guaranteed to be configured on every pool.

## Impact Explanation
A user who calls `addLiquidity()` during a pause window transfers tokens into a pool that is in a state the admin has declared unsafe. Those tokens are immediately subject to the same condition that triggered the pause (e.g., a manipulated oracle price shifting bin accounting, or an active drain). This constitutes a direct loss of user principal — the pool accepted the deposit without reverting, and the deposited tokens are at risk from the compromised pool state. This matches the "direct loss of user principal" impact gate at Critical/High severity.

## Likelihood Explanation
The pause is activated precisely when the pool is most dangerous. A user or integrator observing that `swap()` reverts with `PoolPaused` has no on-chain signal that `addLiquidity()` is still open. Any LP or router that calls `addLiquidity()` during the pause window — whether innocently or via an automated strategy — will have their deposit accepted and their tokens placed at risk. No privilege is required; the call path is fully public.

## Recommendation
Apply `whenNotPaused` to `addLiquidity()`:

```solidity
function addLiquidity(
    address owner, uint80 salt, LiquidityDelta calldata deltas,
    bytes calldata callbackData, bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

If the design intent is to allow LP exits during a pause, `removeLiquidity()` can remain unguarded, but new deposits must be blocked.

## Proof of Concept
1. Pool admin calls `factory.setPause(pool, 1)` — `pauseLevel` is set to 1.
2. Any call to `swap()` reverts with `PoolPaused` (L224, L643–645).
3. A user calls `pool.addLiquidity(owner, salt, deltas, callbackData, extensionData)` — succeeds, tokens transferred in (L182–196).
4. The pool is in a compromised state (e.g., oracle returning a manipulated price); the deposited tokens are immediately at risk.
5. Once the pause is lifted, an attacker executes a favorable swap against the newly deposited liquidity, or the oracle correction itself shifts bin accounting to drain the deposit. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L71-72)
```text
  /// @dev 0 = active, 1 = paused by admin, 2 = paused by protocol. Transitions enforced by factory.
  uint8 internal pauseLevel;
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L224-224)
```text
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
