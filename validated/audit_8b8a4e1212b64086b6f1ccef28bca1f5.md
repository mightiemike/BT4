Audit Report

## Title
Pool Pause Mechanism Does Not Guard `addLiquidity` and `removeLiquidity` — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary
`MetricOmmPool` implements a two-level pause system via `pauseLevel` and the `whenNotPaused` modifier, but only `swap` is decorated with this modifier. Both `addLiquidity` and `removeLiquidity` execute freely regardless of `pauseLevel`, meaning an administrator cannot halt LP principal flows during an emergency. This directly contradicts the stated intent of the two-level pause design.

## Finding Description
`pauseLevel` is declared at line 72 and the `whenNotPaused` modifier is defined at lines 174–177, delegating to `_checkNotPaused` (lines 643–645) which reverts with `PoolPaused` when `pauseLevel != 0`. [1](#0-0) [2](#0-1) [3](#0-2) 

`swap` correctly applies the guard: [4](#0-3) 

`addLiquidity` (lines 182–196) carries only `nonReentrant(PoolActions.ADD_LIQUIDITY)` — no `whenNotPaused`: [5](#0-4) 

`removeLiquidity` (lines 199–212) carries only `nonReentrant(PoolActions.REMOVE_LIQUIDITY)` — no `whenNotPaused`: [6](#0-5) 

`setPause` is callable by the factory at any time and sets `pauseLevel` to 1 or 2: [7](#0-6) 

The exploit path: admin calls `setPause(1)` to freeze the pool mid-exploit → swaps revert correctly → any LP calls `removeLiquidity` directly, which succeeds unconditionally, draining their proportional share of the already-corrupted `binTotals.scaledToken0`/`scaledToken1`. The pool's asset composition changes while the admin believes it is frozen, invalidating any remediation targeting the paused state.

## Impact Explanation
When `pauseLevel != 0`, `removeLiquidity` allows any LP to withdraw their proportional share of every bin's token balances, altering `binTotals` and per-bin share accounting while the pool is supposed to be inert. If the pool was paused mid-exploit (e.g., after swaps at a manipulated oracle price skewed bin balances), exiting LPs receive the corrupted distribution rather than the fair one the admin intended to preserve. `addLiquidity` similarly continues to alter `binTotals` and per-bin share accounting during the pause. The administrator's ability to freeze asset composition — a prerequisite for on-chain remediation — is broken. This constitutes broken core pool functionality causing potential loss of LP assets and meets the "broken core pool functionality causing loss of funds" allowed impact.

## Likelihood Explanation
Any LP (unprivileged) can call `removeLiquidity` at any time, including while `pauseLevel != 0`. The only precondition is that the admin has already paused the pool; once paused, the bypass is automatic and requires no special knowledge. The `MetricOmmPoolLiquidityAdder` periphery also calls `pool.addLiquidity` directly with no pause check of its own, making the bypass reachable through the standard user-facing periphery as well.

## Recommendation
Add `whenNotPaused` to both functions, mirroring the pattern already applied to `swap`:

```solidity
function addLiquidity(...) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) ...

function removeLiquidity(...) external whenNotPaused nonReentrant(PoolActions.REMOVE_LIQUIDITY) ...
```

If the protocol intentionally allows LP exits during a swap-only pause (level 1) but not a full pause (level 2), introduce a separate modifier (e.g., `whenNotFullyPaused`) that only reverts when `pauseLevel == 2`, and document the distinction explicitly.

## Proof of Concept
1. Deploy pool with any extension configuration. Oracle price is manipulated above fair value.
2. Attacker executes `swap` calls draining token1 from bins. Admin detects the drain and calls `factory.setPause(pool, 1)` → `pauseLevel = 1`. Subsequent `swap` calls revert with `PoolPaused`.
3. Before admin can remediate, any LP calls `pool.removeLiquidity(owner, salt, deltas, "")`. The call succeeds — `whenNotPaused` is absent — and the LP withdraws their proportional share of the already-drained bins.
4. Pool's `binTotals` are now different from the state at pause time. Admin's remediation (e.g., token injection) targets a different pool state than intended, over- or under-compensating remaining LPs.
5. Foundry test: assert that `removeLiquidity` reverts with `PoolPaused` after `setPause(1)` — it will not revert, confirming the missing guard.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L72-72)
```text
  uint8 internal pauseLevel;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L174-177)
```text
  modifier whenNotPaused() {
    _checkNotPaused();
    _;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-188)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L199-202)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
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
