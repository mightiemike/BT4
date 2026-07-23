Audit Report

## Title
Stop-Loss Watermark Not Ratcheted on Cross-Direction Breach Revert, Allowing Subsequent Guard Bypass — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

In `_checkAndUpdateWatermarks`, the watermark writes (`hwmS.token0`, `hwmS.token1`, `hwmS.lastDecayTs`) are placed after both direction-specific breach checks. When a `zeroForOne` swap triggers `breach0 && zeroForOne` and reverts, the EVM rolls back all state, leaving `hwmS.token1` un-ratcheted at its prior value and `hwmS.lastDecayTs` un-advanced. A subsequent `!zeroForOne` swap then compares `metricT1` against the stale, further-decayed token1 watermark instead of the higher ratcheted value, allowing the swap to proceed when it should be blocked.

## Finding Description

In `_checkAndUpdateWatermarks` (L258–285), the control flow is:

```solidity
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
if (breach0 && zeroForOne) {
    revert OracleStopLossTriggered(...);   // rolls back everything; hwmS never written
}

(uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
if (breach1 && !zeroForOne) { revert ...; }

hwmS.token0 = uint104(hwm0);        // never reached on breach0 revert
hwmS.token1 = uint104(hwm1);        // never reached on breach0 revert
hwmS.lastDecayTs = uint32(block.timestamp);  // never reached
``` [1](#0-0) 

The metric formulas confirm the directional relationship:

- `metricT0 = t0ps + (t1 * Q64 / midPriceX64) * SCALE / shares` — **drops** when mid rises
- `metricT1 = (t0 * midPriceX64 / Q64) * SCALE / shares + t1ps` — **rises** when mid rises [2](#0-1) 

When oracle mid spikes: `metricT0` drops (breach0 possible) while `metricT1` rises to a new high `C` that `_applyWatermark` would ratchet up to via `if (metric >= hwm) return (metric, false)`. [3](#0-2) 

The revert on `breach0 && zeroForOne` prevents `hwmS.token1` from being updated to `C` and prevents `hwmS.lastDecayTs` from advancing. When mid later normalizes, `metricT1` returns to `D ≈ B` (original level). The subsequent `!zeroForOne` swap computes `hwm1_decayed = _decayed(B, rate, T2-T0)` — a value ≤ B — and since `D ≈ B ≥ hwm1_decayed`, `_applyWatermark` returns no breach, and the swap executes. Had the first swap committed `hwmS.token1 = C`, the check would compare `D ≈ B` against `C * floorMultiplier / E6 ≈ 1.9B`, triggering a breach and revert.

## Impact Explanation

The token1 stop-loss guard is bypassed for `!zeroForOne` swaps following a reverted `zeroForOne` breach. The `!zeroForOne` direction drains token0 from the pool. The stop-loss is the LP's primary protection against value-per-share leakage; bypassing it allows the pool to be drained of token0 at a time when oracle-derived value per share has dropped significantly from the ratcheted high. This constitutes a direct loss of LP principal above Sherlock Medium thresholds, as the stop-loss mechanism — the sole on-chain guard — is rendered ineffective.

## Likelihood Explanation

The attack requires only natural market conditions: an oracle mid spike followed by reversion, and two sequential public swaps. No privileged access, oracle manipulation, or non-standard token behavior is needed. The attacker's first (reverted) swap costs only gas. The vulnerability window remains open until any successful swap in either direction updates the watermarks. This is a realistic, repeatable sequence on any pool using this extension.

## Recommendation

Separate the watermark write from the breach check. Compute both watermarks first, persist them unconditionally, then enforce the direction-specific reverts:

```solidity
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
(uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);

// Always persist — even if we are about to revert
hwmS.token0 = uint104(hwm0);
hwmS.token1 = uint104(hwm1);
hwmS.lastDecayTs = uint32(block.timestamp);

// Then enforce direction-specific stop-loss
if (breach0 && zeroForOne)  revert OracleStopLossTriggered(binIdx, true,  metricT0, (hwm0 * floorMultiplier) / E6);
if (breach1 && !zeroForOne) revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
```

This ensures the ratchet and decay clock are always advanced regardless of which direction triggers the stop-loss.

## Proof of Concept

1. Pool initialized with `drawdownE6 = 50_000` (5%), `decayPerSecondE8 > 0`. Initial watermarks: `hwmS.token0 = A`, `hwmS.token1 = B`, `hwmS.lastDecayTs = T0`.
2. Oracle mid doubles. `metricT0` drops below `A * 0.95` (breach0 = true). `metricT1` rises to `C ≈ 2B` (would ratchet up).
3. Attacker submits `zeroForOne = true` swap → `afterSwap` calls `_checkAndUpdateWatermarks` → `breach0 && zeroForOne` → `OracleStopLossTriggered` revert. All state rolled back. Cost: gas only.
4. Oracle mid returns to original. `metricT1` returns to `D ≈ B`.
5. Attacker submits `!zeroForOne` swap. `dt = T2 - T0` (large). `hwm1_decayed = _decayed(B, rate, T2-T0) ≤ B`. `D ≈ B ≥ hwm1_decayed` → `_applyWatermark` returns `(D, false)` → no breach → swap executes, draining token0.
6. Verification: had step 3 succeeded, `hwmS.token1 = C`; step 5 would compare `D ≈ B` against `C * 0.95 ≈ 1.9B` → breach → revert. The bypass is confirmed.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L254-255)
```text
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L270-284)
```text
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
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L333-333)
```text
    if (metric >= hwm) return (metric, false);
```
