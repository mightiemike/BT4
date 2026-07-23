Audit Report

## Title
`addLiquidity` missing `whenNotPaused` guard allows LP deposits into paused pool, exposing principal to loss on unpause — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary

`MetricOmmPool` defines a `pauseLevel` state variable and a `whenNotPaused` modifier, but applies the modifier only to `swap`. `addLiquidity` carries only `nonReentrant`, so any caller can deposit tokens into a paused pool. When the pool is unpaused, those deposits are immediately exposed to swaps at whatever price state triggered the pause, resulting in direct loss of LP principal.

## Finding Description

`pauseLevel` is declared at L71–72 and `whenNotPaused` is defined at L174–177. The modifier calls `_checkNotPaused()` (L643–645), which reverts when `pauseLevel != 0`.

`swap` (L217–224) is correctly guarded:
```solidity
) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

`addLiquidity` (L182–196) is not:
```solidity
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

`removeLiquidity` (L199–212) is likewise unguarded.

The extension hooks `_beforeAddLiquidity` / `_afterAddLiquidity` still execute inside `addLiquidity`, so any extension-level allowlist check runs — but the pool-level pause invariant is never enforced. The full deposit path (`LiquidityLib.addLiquidity`, updating `binTotals`, `_binStates`, `_binTotalShares`, `_positionBinShares`) completes successfully regardless of `pauseLevel`.

Exploit path:
1. Oracle anomaly detected; factory calls `pool.setPause(1)` → `pauseLevel = 1`.
2. `swap` now reverts with `PoolPaused`; `addLiquidity` does not.
3. Attacker (or unsuspecting LP) calls `addLiquidity` — tokens enter bins priced at the compromised oracle mid-price; pool accounting is updated normally.
4. Protocol calls `pool.setPause(0)` → pool unpauses.
5. Attacker immediately calls `swap`, buying out the newly deposited bins at the stale/manipulated ask price.
6. LP's deposited tokens are drained; pool accounting correctly reflects the swap — no recourse.

## Impact Explanation

This is a direct loss of LP principal. The pause mechanism's stated purpose is to prevent fund-impacting actions during a compromised oracle state. `addLiquidity` bypasses that boundary entirely: tokens enter the pool at a price the protocol itself declared unsafe, and those tokens are immediately drainable via `swap` the moment the pool unpauses. The corrupted values are `binTotals`, `_binStates`, and `_positionBinShares`, which record the deposit at the manipulated price. This meets the "direct loss of user principal" criterion at Critical/High severity.

## Likelihood Explanation

- Pausing is a real operational event; both admin (level 1) and protocol (level 2) can trigger it via `setPause`.
- No special privilege is required for the attacker — only a standard `addLiquidity` call.
- LPs interacting through a router or UI are unlikely to inspect `pauseLevel` before depositing.
- An attacker watching the mempool for `setPause` can front-run with a large `addLiquidity`, then back-run the `setPause(0)` unpause with a `swap` — a two-transaction sandwich requiring no privileged access.
- The condition (pool paused, LP deposits, pool unpauses) is repeatable across every pause/unpause cycle.

## Recommendation

Add `whenNotPaused` to `addLiquidity`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

`removeLiquidity` should remain unguarded (or guarded only at `pauseLevel == 2`) so existing LPs can always exit — preserving the principle that token withdrawals must remain possible even in a restricted state.

## Proof of Concept

```
1. Deploy pool; set oracle to report fair bid/ask.
2. Simulate oracle manipulation: shift bid/ask 10% below market.
3. Call factory.setPause(pool, 1) → pauseLevel = 1.
4. Assert: pool.swap(...) reverts with PoolPaused. ✓
5. Assert: pool.addLiquidity(attacker, salt, deltas, ...) succeeds. ✓
   - binTotals updated; _binStates updated; tokens transferred in.
6. Call factory.setPause(pool, 0) → pauseLevel = 0.
7. Call pool.swap(attacker, true, largeAmount, ...) — buys token0 from
   newly deposited bins at the manipulated (below-market) ask price.
8. Assert: attacker token0 balance increased; LP position value decreased
   by the manipulation spread × deposited amount.
```

Foundry test: fork at the block of the manipulated oracle observation, call `setPause(1)`, `addLiquidity`, `setPause(0)`, `swap` in sequence, and assert LP share redemption value is less than deposited value.