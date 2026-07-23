Audit Report

## Title
Missing Deadline Check in `MetricOmmPoolLiquidityAdder` Allows Stale Oracle-Driven Composition on Liquidity Additions — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary

`MetricOmmPoolLiquidityAdder` exposes four public entry points (`addLiquidityExactShares` ×2, `addLiquidityWeighted` ×2) with no `deadline` parameter and no `_checkDeadline` call. A block proposer can delay a pending `addLiquidityWeighted` transaction until the oracle price has moved, causing the probe to return a composition heavily skewed toward the depreciated token. The LP then deposits up to `maxAmountToken0`/`maxAmountToken1` of that token at the new, lower oracle price, suffering a direct, unrecoverable loss of principal.

## Finding Description

`MetricOmmSwapRouterBase._checkDeadline()` is defined at lines 91–94 and called at every swap entry point in `MetricOmmSimpleRouter` (lines 68, 93, 131, 155). No equivalent guard exists anywhere in `MetricOmmPoolLiquidityAdder`.

The `addLiquidityWeighted` path (lines 88–116 and 123–149) executes a probe `addLiquidity` call that reverts with `LiquidityProbe(need0, need1)`, where `need0`/`need1` are entirely determined by the pool cursor at execution time, which is driven by the oracle price at that moment. `_scaleWeightsToShares` (lines 226–243) then scales the user's weight vector by `min(max0/need0, max1/need1)`, so a cursor shift toward token0 causes the full `maxAmountToken0` cap to be consumed.

The only existing guard, `_validateBinAndBinPosition` (lines 263–286), checks the cursor bin index and intra-bin position against user-supplied `minimalCurBin`/`maximalCurBin`/`minimalPosition`/`maximalPosition` bounds. This is not equivalent to a deadline: (1) users routinely set loose bounds for usability; (2) even with tight bounds, the oracle price can move within the allowed cursor range, changing the probe composition without triggering a revert; (3) `addLiquidityExactShares` has no cursor bounds check at all.

The `maxAmountToken0`/`maxAmountToken1` caps are absolute ceilings on token pull amounts, not price-composition guards. They do not prevent the LP from depositing the full cap of a token that has depreciated since broadcast.

## Impact Explanation

Direct loss of LP principal. A validator withholds `addLiquidityWeighted` until the oracle price of token0 drops. The probe returns `need0` proportionally larger; `scaleWad = max0/need0` is the binding constraint; the LP deposits up to `maxAmountToken0` of the now-cheaper token. The LP receives shares priced at the new oracle mid but paid tokens at the old (higher) value they approved. The difference is unrecoverable. This matches the "direct loss of user principal" allowed impact at High severity under Sherlock thresholds given the magnitude (up to the full `maxAmountToken0` cap).

## Likelihood Explanation

No special privilege beyond block-production ordering is required. On Ethereum and Base, validators observe the public mempool and can selectively delay transactions. On HyperEVM the same applies to block proposers. Liquidity additions are typically large transactions; the expected profit is positive whenever the oracle price moves enough between broadcast and inclusion to exceed gas cost. The attack is repeatable and requires no cooperation from the LP.

## Recommendation

Add a `uint256 deadline` parameter to all four public entry points and call `_checkDeadline` (or an equivalent inline check) before any pool interaction:

```solidity
function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    uint256 deadline,   // ← add
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    if (block.timestamp > deadline) revert TransactionExpired(deadline, block.timestamp);
    ...
}
```

Apply the same change to both overloads of `addLiquidityExactShares` and both overloads of `addLiquidityWeighted`.

## Proof of Concept

1. LP broadcasts `addLiquidityWeighted` with `maxAmountToken0 = 10_000e18`, `maxAmountToken1 = 10_000e6`, loose cursor bounds (e.g., `minimalCurBin = -128`, `maximalCurBin = 127`), targeting a 50/50 deposit at oracle price $1.00/token0.
2. Block proposer withholds the transaction.
3. Oracle price of token0 drops to $0.50. Pool cursor shifts; probe returns `need0 = 20_000e18`, `need1 = 1e6`.
4. `scaleWad0 = 10_000e18 * WAD / 20_000e18 = 0.5 WAD`; `scaleWad1 = 10_000e6 * WAD / 1e6 = 10_000 WAD`; `scaleWad = 0.5 WAD`.
5. Block proposer includes the transaction. LP deposits `~10_000e18` token0 (worth $5,000 at new price) and `~0.5e6` USDC — total ~$5,000.50 instead of the intended ~$10,000.
6. LP has lost ~$5,000 of principal with no recourse.

Foundry test: fork the target chain, deploy `MetricOmmPoolLiquidityAdder`, push an adverse oracle update via the oracle admin (trusted, so no bypass needed for PoC), then call `addLiquidityWeighted` and assert `amount0Added * newPrice + amount1Added < expectedDepositValue * 0.95`.