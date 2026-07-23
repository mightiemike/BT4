Audit Report

## Title
`lastDecayTs` Advances Without Actual Decay, Permanently Locking Stop-Loss After Breach on Small-Metric Bins — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`_checkAndUpdateWatermarks` unconditionally resets `hwmS.lastDecayTs` to `block.timestamp` on every successful swap, even when `_decayed()` returns the watermark unchanged because integer division truncated the decay step to zero. When swaps in the non-blocked direction continuously reset the clock, `dt` never accumulates to the threshold required for a non-zero decay step, so the watermark is frozen at its breached value indefinitely and the pool is permanently blocked in the breached direction.

## Finding Description

In `_checkAndUpdateWatermarks` (lines 258–285), after computing `hwm0` and `hwm1` via `_applyWatermark(_decayed(...))`, the function unconditionally writes:

```solidity
hwmS.lastDecayTs = uint32(block.timestamp);   // line 284 — always executed
```

The decay helper `_decayed` (lines 319–324) uses integer division:

```solidity
return hwm - (hwm * factor) / E8;
```

When `hwm * factor < E8`, this truncates to zero and `_decayed` returns `hwm` unchanged. For `ratePerSecondE8 = 58` (the NatDoc example, ≈5%/day) and `hwm = 100`, the minimum `dt` for a non-zero decay step is `ceil(E8 / (100 × 58)) = 17,242 seconds` (≈4.8 hours). Any swap arriving before that threshold produces zero decay but still resets the clock.

The direction-aware revert guards (lines 271–278) only block the matching direction:

```solidity
if (breach0 && zeroForOne) { revert ...; }   // blocks token0-breach on zeroForOne
if (breach1 && !zeroForOne) { revert ...; }  // blocks token1-breach on oneForZero
```

Swaps in the *non-blocked* direction pass both guards, reach line 284, and reset `lastDecayTs` — preventing `dt` from ever accumulating to the 17,242-second threshold. The watermark stored in `hwmS.token0/token1` never changes, `_applyWatermark` always sees `metric < hwm` and always reports a breach, and the blocked direction is permanently disabled.

**Exploit path:**
1. A swap drains a bin with small per-share metrics (e.g., `hwm = 100`) below the drawdown floor → `OracleStopLossTriggered` fires; the pool blocks that direction.
2. Any trader issues swaps in the still-allowed direction at any cadence faster than 4.8 hours.
3. Each such swap calls `afterSwap → _afterSwapOracleStopLoss → _checkAndUpdateWatermarks`, resets `lastDecayTs`, and exits without reverting.
4. The watermark never decays; the blocked direction is permanently unusable.

No privileged role is required. The pool admin's only recovery path is `executeOracleStopLossHighWatermarks`, which itself requires a timelock delay and an explicit admin action — not an automatic protocol recovery.

## Impact Explanation

After a stop-loss breach on a small-metric bin, the pool's swap path in the breached direction is permanently disabled by any unprivileged actor who continues to swap in the non-blocked direction. This constitutes broken core pool functionality: traders cannot use the pool in one direction, and the oracle market-maker cannot provide two-sided liquidity. This matches the allowed impact gate: **broken core pool functionality causing unusable swap flows**.

## Likelihood Explanation

- No privileged actor is required; any public swap in the non-blocked direction advances the clock.
- Small per-share metrics are common in bins partially consumed by prior swaps or in pools with high share counts relative to token balances. With `METRIC_SCALE = 1e6` and `minimalMintableLiquidity` as the share floor, bins with `hwm < 1,724,138` are affected at `ratePerSecondE8 = 58`.
- The decay rate of 58 is the value cited in the contract's own NatDoc comment as a typical configuration.
- The condition is self-reinforcing: once the clock starts advancing without decay, every subsequent swap in the non-blocked direction perpetuates it.

## Recommendation

Only advance `lastDecayTs` when the decay computation actually changed the stored watermark. Capture the decayed values before calling `_applyWatermark`, then compare:

```solidity
uint256 decayed0 = _decayed(hwmS.token0, decayRate, dt);
uint256 decayed1 = _decayed(hwmS.token1, decayRate, dt);

(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, decayed0, floorMultiplier);
if (breach0 && zeroForOne) { revert OracleStopLossTriggered(...); }

(uint256 hwm1, bool breach1) = _applyWatermark(metricT1, decayed1, floorMultiplier);
if (breach1 && !zeroForOne) { revert OracleStopLossTriggered(...); }

hwmS.token0 = uint104(hwm0);
hwmS.token1 = uint104(hwm1);
// Only advance the clock if decay actually moved the stored watermark
if (decayed0 != hwmS.token0 || decayed1 != hwmS.token1) {
    hwmS.lastDecayTs = uint32(block.timestamp);
}
```

This ensures elapsed time accumulates in `dt` until `hwm * factor` is large enough to produce a non-zero step.

## Proof of Concept

```
Setup:
  Pool with OracleValueStopLossExtension
  drawdownE6 = 50_000, decayPerSecondE8 = 58
  Bin 0: token balances small → hwm0 = hwm1 = 100 after first swap

Step 1: Swap sets watermark to 100.

Step 2: Drain bin → metric drops to 40.
        40 < 100 * (1e6 - 50_000) / 1e6 = 95 → breach triggered.
        zeroForOne swaps now revert with OracleStopLossTriggered.

Step 3: Every second, issue a oneForZero swap:
        dt = 1, factor = 58 * 1 = 58
        (100 * 58) / 1e8 = 0 → _decayed returns 100 (unchanged)
        _applyWatermark(40, 100, ...) → (100, true) — breach still set
        breach0 && zeroForOne = true && false → no revert
        hwmS.lastDecayTs = block.timestamp  ← clock resets, watermark stays 100

Step 4: After 86,400 seconds (1 day), watermark should have decayed ~5%.
        Expected: hwm ≈ 95, eventually allowing re-arm.
        Actual: hwm = 100 forever. Pool permanently blocked in zeroForOne direction.

Foundry test sketch:
  1. Deploy pool + OracleValueStopLossExtension with above params.
  2. Add liquidity to bin 0 with small balances (t0=1000, shares=10000).
  3. Swap to set watermark, then drain bin below floor → assert revert.
  4. Loop: vm.warp(block.timestamp + 1); swap oneForZero; 86400 iterations.
  5. Assert: zeroForOne swap still reverts after 86400 seconds.
  6. Assert: currentHighWatermarks(pool, 0) still returns (100, 100).
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L271-278)
```text
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L280-284)
```text
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L319-324)
```text
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
  }
```
