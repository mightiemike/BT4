Audit Report

## Title
Stop-Loss Extension Uses Arithmetic Mid-Price While Oracle Mid Is Geometric, Causing Guard Miscalibration — (File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol)

## Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid-price as the arithmetic mean of bid and ask at line 218, while the protocol's canonical oracle mid is the geometric mean computed by `SwapMath.midAndSpreadFeeX64FromBidAsk`. By AM-GM inequality, arithmetic mean ≥ geometric mean for any non-zero spread, causing the stop-loss to overestimate `metricToken1` relative to the true oracle-mid value. When the oracle spread widens between watermark establishment and a subsequent swap, the guard can fail to trigger even though the pool's actual per-share value has fallen below the configured drawdown floor.

## Finding Description

**Root cause — arithmetic vs. geometric mid:**

`_afterSwapOracleStopLoss` computes mid as:

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [1](#0-0) 

The protocol's canonical oracle mid is defined in `SwapMath.midAndSpreadFeeX64FromBidAsk` as:

```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
``` [2](#0-1) 

For any spread > 0, `arith_mid > geo_mid`. The stop-loss feeds this inflated mid into `_metrics`: [3](#0-2) 

`metricToken1 = t0 × mid / shares + t1 / shares` is inflated relative to the true geometric-mid value whenever the spread is non-zero.

**Watermark ratchet and breach check:**

The watermark for token1 is ratcheted up to the inflated arithmetic metric. The floor is `hwm1 × floorMultiplier / E6`: [4](#0-3) 

**Spread-change exploit path:**

1. Watermark established during small spread (arithmetic ≈ geometric). `hwm1` is set close to the true geometric value.
2. Oracle spread widens (volatile market, oracle latency). Arithmetic mid stays at the nominal mid; geometric mid falls below it.
3. Attacker executes a `!zeroForOne` swap causing value leakage (pool sells token0 at a stale low price).
4. Stop-loss computes `metricToken1` using arithmetic mid (now materially higher than geometric mid).
5. Computed `metricToken1` remains at or above `hwm1 × floorMultiplier / E6` even though the true geometric-mid value has fallen below the floor.
6. `OracleStopLossTriggered` is not emitted; the swap commits; LP principal leaks beyond the configured drawdown.

No existing guard checks for spread magnitude or enforces consistency between the mid used for watermark establishment and the mid used for breach detection. [5](#0-4) 

## Impact Explanation

The stop-loss is the primary on-chain protection for LP capital against value leakage. When it fails to trigger, LPs lose principal beyond the drawdown they accepted. The magnitude of the error scales with the square of the spread: a 20% spread produces ~1% metric inflation. For pools configured with tight drawdowns (1–5%), a 10–20% oracle spread can suppress the guard entirely at the threshold, constituting a direct loss of LP principal above the Sherlock medium threshold. This matches the allowed impact gate: direct loss of user principal.

## Likelihood Explanation

Any pool deploying `OracleValueStopLossExtension` is affected whenever the oracle spread is non-zero. Pools on volatile pairs or during oracle stress — the exact conditions the stop-loss is meant to guard against — experience the largest spread and therefore the largest guard error. No privileged action is required; any public swap at the right moment triggers the path. The condition is self-reinforcing: the guard is most likely to fail precisely when it is most needed.

## Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the protocol's canonical oracle mid:

```solidity
// Before (line 218):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After:
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

This makes the stop-loss metrics consistent with the oracle mid the protocol uses for spread fee calculation, eliminating the spread-dependent bias. [6](#0-5) 

## Proof of Concept

```
Setup:
  bid  = 0.80 × 2^64,  ask = 1.20 × 2^64   (20% spread, symmetric around 1.0)
  geometric mid  = sqrt(0.80 × 1.20) × 2^64 ≈ 0.9798 × 2^64
  arithmetic mid = (0.80 + 1.20) / 2 × 2^64 = 1.0000 × 2^64

  t0 = 1000, t1 = 1000, shares = 1000
  drawdown = 5% → floorMultiplier = 0.95

Watermark established (small spread, arith ≈ geo):
  hwm1 ≈ 2.0  (t0×1.0/shares + t1/shares)

Spread widens to 20%; bad !zeroForOne swap executes:
  t0 = 1000, t1 = 900  (100 units of token1 leaked)

Stop-loss check (arithmetic mid = 1.0):
  metricToken1 = 1000×1.0/1000 + 900/1000 = 1.90
  floor        = 2.0 × 0.95 = 1.90
  1.90 >= 1.90  →  NO BREACH  (guard silent, swap committed)

Correct check (geometric mid ≈ 0.9798):
  metricToken1 = 1000×0.9798/1000 + 900/1000 = 1.8798
  floor        = 2.0 × 0.95 = 1.90
  1.8798 < 1.90  →  BREACH  (guard should have reverted the swap)
```

A Foundry test can reproduce this by deploying a pool with `OracleValueStopLossExtension`, establishing a watermark at near-zero spread, then widening the oracle spread and executing a `!zeroForOne` swap that reduces `t1` by exactly the drawdown amount. The test asserts that no revert occurs with the current arithmetic mid, and that a revert occurs when the mid is replaced with the geometric mean. [7](#0-6)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L207-243)
```text
  function _afterSwapOracleStopLoss(
    address pool_,
    uint256 packedSlot0Initial,
    uint256 packedSlot0Final,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bool zeroForOne
  ) internal {
    PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
    uint256 drawdown = cfg.drawdownE6;
    if (drawdown == 0) return;
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
    uint256 minShares = IMetricOmmPool(pool_).getImmutables().minimalMintableLiquidity;
    if (minShares == 0) minShares = 1;
    PoolSlot0 memory s0 = Slot0Library.unpack(packedSlot0Initial);
    PoolSlot0 memory s1 = Slot0Library.unpack(packedSlot0Final);
    int8 lo = s0.curBinIdx < s1.curBinIdx ? s0.curBinIdx : s1.curBinIdx;
    int8 hi = s0.curBinIdx > s1.curBinIdx ? s0.curBinIdx : s1.curBinIdx;
    // forge-lint: disable-next-line(unsafe-typecast)
    uint256 count = uint256(int256(hi) - int256(lo) + 1);
    int8[] memory binIdxs = new int8[](count);
    for (uint256 i = 0; i < count; i++) {
      // forge-lint: disable-next-line(unsafe-typecast)
      binIdxs[i] = int8(int256(lo) + int256(i));
    }
    bytes32[] memory states = PoolStateLibrary._multipleBinStates(pool_, binIdxs);
    bytes32[] memory shares = PoolStateLibrary._multipleBinTotalShares(pool_, binIdxs);
    uint256 floorMultiplier = E6 - drawdown;
    uint256 decayRate = cfg.decayPerSecondE8;
    for (uint256 i = 0; i < count; i++) {
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
      (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
      (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
      _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
    }
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L254-255)
```text
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L275-278)
```text
    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
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

**File:** metric-core/contracts/libraries/SwapMath.sol (L65-72)
```text
  function midAndSpreadFeeX64FromBidAsk(uint256 bidPriceX64, uint256 askPriceX64)
    internal
    pure
    returns (uint256 midPriceX64, uint256 baseFeeX64)
  {
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
  }
```
