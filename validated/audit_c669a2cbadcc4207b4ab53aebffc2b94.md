Audit Report

## Title
Stop-Loss Guard Bypassed at Exact Drawdown Floor Due to Strict Inequality — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary
`_applyWatermark` uses a strict `<` comparison at line 334 to detect a drawdown breach. When the per-share metric equals exactly the configured floor (`hwm * floorMultiplier / E6`), `breached` evaluates to `false`, `_checkAndUpdateWatermarks` does not revert, and the swap settles. The LP's per-share value has fallen by the full configured drawdown with no protective halt.

## Finding Description
`floorMultiplier` is set to `E6 - drawdown` at line 234 of `_afterSwapOracleStopLoss`. The floor for a given watermark is therefore `hwm * (E6 - drawdownE6) / E6`. Inside `_applyWatermark`:

```solidity
// Line 334
breached = metric < (hwm * floorMultiplier) / E6;
```

When `metric == floor` exactly, `metric < floor` is `false`, so `breached = false` and the function returns `(hwm, false)`. Back in `_checkAndUpdateWatermarks` (lines 271–278), neither `breach0 && zeroForOne` nor `breach1 && !zeroForOne` is true, so no `OracleStopLossTriggered` revert is issued. The watermarks are then written back with the unchanged `hwm` values (lines 281–284), meaning the next swap still compares against the same high-water mark. If the metric stays at exactly the floor, the stop-loss never fires across any number of swaps.

The `afterSwap` hook (lines 185–204) is the last line of defence: a revert there rolls back the entire swap. Failing to revert at the exact floor means the swap settles with the LP's per-share value already at the maximum permitted drawdown.

## Impact Explanation
Direct loss of LP principal. When `metric == floor`, the LP has lost exactly `drawdownE6 / 1e6` of their per-share value in that bin — the full configured drawdown — with no revert and no breach event. This is a concrete, quantifiable loss of user principal that meets Sherlock High/Medium thresholds depending on drawdown magnitude and pool TVL.

## Likelihood Explanation
All relevant state is public: `highWatermarks`, `oracleStopLossConfig`, and bin balances. An attacker can compute off-chain the exact `amountSpecified` that lands `metricT0` or `metricT1` on the floor integer. The oracle mid price (`(bidPriceX64 + askPriceX64) / 2`, line 218) is observable from the mempool or a pending oracle update. The attacker monitors for a block where the oracle price is known, computes the exact swap size, and submits. The attack is repeatable for any pool using this extension with a non-zero `drawdownE6`.

## Recommendation
Change the strict inequality to non-strict in `_applyWatermark` (line 334):

```diff
- breached = metric < (hwm * floorMultiplier) / E6;
+ breached = metric <= (hwm * floorMultiplier) / E6;
```

This ensures the stop-loss triggers when the metric reaches **or** falls below the floor, consistent with the drawdown guarantee stated in the contract NatSpec.

## Proof of Concept
1. Deploy a pool with `drawdownE6 = 100_000` (10%). Watermark `hwm0 = 1_000_000` for bin 0.
2. Floor = `1_000_000 × 900_000 / 1_000_000 = 900_000`.
3. Attacker computes off-chain the `zeroForOne` swap amount that sets `metricT0 = 900_000` exactly given the current oracle mid.
4. Swap executes; `afterSwap` calls `_applyWatermark(900_000, 1_000_000, 900_000)`.
5. `metric >= hwm` → false (900_000 < 1_000_000). `breached = (900_000 < 900_000)` → `false`.
6. `_checkAndUpdateWatermarks` writes `hwmS.token0 = 1_000_000` (unchanged), no revert.
7. Swap settles. LP's token0-denominated value per share in bin 0 has fallen by 10% — the full configured drawdown — with no protective halt.

A Foundry unit test can reproduce this by mocking `highWatermarks` with `hwm0 = 1_000_000`, setting `drawdownE6 = 100_000`, and calling `afterSwap` with crafted bin balances and oracle price such that `metricT0 = 900_000`, then asserting no revert occurs. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L234-234)
```text
    uint256 floorMultiplier = E6 - drawdown;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L258-285)
```text
  function _checkAndUpdateWatermarks(
    address pool_,
    int8 binIdx,
    uint256 metricT0,
    uint256 metricT1,
    uint256 floorMultiplier,
    uint256 decayRate,
    bool zeroForOne
  ) private {
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }

    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L328-336)
```text
  function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private
    pure
    returns (uint256 newHwm, bool breached)
  {
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
  }
```
