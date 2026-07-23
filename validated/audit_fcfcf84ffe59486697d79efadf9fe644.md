The code is confirmed. Let me verify each step of the claim against the actual implementation.

**Step 1 — Zero-share skip:** Line 238 confirms `if (totalShares == 0) continue;` — `_checkAndUpdateWatermarks` is never called for empty bins. [1](#0-0) 

**Step 2 — `lastDecayTs` only written in `_checkAndUpdateWatermarks`:** Line 284 is the only swap-path write to `lastDecayTs` (the other is the admin-only `executeOracleStopLossHighWatermarks` at line 174). [2](#0-1) 

**Step 3 — Stale `dt` computation:** Line 268 computes `dt = block.timestamp - hwmS.lastDecayTs` with no floor or cap. [3](#0-2) 

**Step 4 — Watermark zeroed:** Lines 321–322 confirm `if (factor >= E8) return 0;`. At `ratePerSecondE8 = 58`, the threshold is crossed after `⌈1e8/58⌉ ≈ 1,724,138 s ≈ 19.95 days`. [4](#0-3) 

**Step 5 — Zero HWM bypasses breach check:** Line 333: `if (metric >= hwm) return (metric, false);`. When `hwm = 0`, `metric >= 0` is always true for `uint256`, so the function returns early with `breached = false` before ever reaching the floor comparison on line 334. [5](#0-4) 

All five steps are confirmed by the production code. The vulnerability is real.

---

Audit Report

## Title
`lastDecayTs` Not Updated for Zero-Share Bins Causes Stop-Loss Watermark to Over-Decay and Be Bypassed — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary
`_afterSwapOracleStopLoss` skips `_checkAndUpdateWatermarks` for any bin where `totalShares == 0`, leaving `lastDecayTs` frozen at its last non-zero value. When liquidity is later re-added and a swap crosses the bin, the elapsed `dt` is computed against the stale timestamp, causing the watermark to decay to zero in a single call. A zero watermark makes `_applyWatermark` unconditionally return `(metric, false)`, silently bypassing the stop-loss and allowing value-draining swaps to complete without reversion.

## Finding Description
In `_afterSwapOracleStopLoss` (lines 236–242), the loop skips empty bins entirely:

```solidity
for (uint256 i = 0; i < count; i++) {
    uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
    if (totalShares == 0) continue;   // lastDecayTs never advanced
    ...
    _checkAndUpdateWatermarks(pool_, binIdxs[i], ...);
}
```

`_checkAndUpdateWatermarks` is the only swap-path location that writes `lastDecayTs` (line 284). The admin-only path `executeOracleStopLossHighWatermarks` (line 174) also writes it, but requires a privileged call and a timelock delay.

When a bin re-acquires shares and a swap touches it, line 268 computes:

```solidity
uint256 dt = block.timestamp - hwmS.lastDecayTs;
```

With `ratePerSecondE8 = 58` and `dt ≥ 1,724,138 s (~20 days)`, `_decayed` returns 0 (lines 321–322):

```solidity
uint256 factor = ratePerSecondE8 * dt;   // 58 * 1_728_000 = 100_224_000 > 1e8
if (factor >= E8) return 0;
```

`_applyWatermark` then receives `hwm = 0`. Since `metric` is `uint256`, `metric >= 0` is always true, so line 333 returns `(metric, false)` unconditionally — the breach path on line 334 is never reached:

```solidity
if (metric >= hwm) return (metric, false);   // always fires when hwm == 0
```

No `OracleStopLossTriggered` revert is issued. The watermark is silently ratcheted up to the current (potentially already-drained) metric, and subsequent swaps are checked against the lower baseline.

## Impact Explanation
The `OracleValueStopLossExtension` is the primary on-chain guard that reverts swaps when per-share bin value falls below the configured `drawdownE6` floor. Bypassing it for the first swap after re-liquification means a swap that drains LP value beyond the drawdown threshold completes without reversion. The watermark is then silently reset to the post-drain metric, so the protection baseline is permanently lowered. This is a direct loss of LP principal above the protocol-configured drawdown threshold, matching the "Critical/High direct loss of user principal" allowed impact.

## Likelihood Explanation
All preconditions are reachable by unprivileged actors with no oracle manipulation:
1. **Bin reaches zero shares** — any LP can fully withdraw at any time; normal behavior.
2. **~20 days elapse** — a common gap between LP cycles, especially for less-active bins or during market stress.
3. **Liquidity re-added** — any allowed depositor (or all depositors if `allowAllDepositors` is set) can trigger this.
4. **Swap crosses the bin** — any swap in the bin's range triggers `afterSwap`.

The scenario is repeatable and requires no coordination with privileged roles.

## Recommendation
Advance `lastDecayTs` for zero-share bins without performing the breach check or watermark ratchet:

```diff
for (uint256 i = 0; i < count; i++) {
    uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
    if (totalShares == 0) {
+       // Advance the decay clock so stale dt cannot accumulate.
+       highWatermarks[pool_][binIdxs[i]].lastDecayTs = uint32(block.timestamp);
        continue;
    }
    (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
    (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
    _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
}
```

This ensures the decay clock is always current regardless of bin occupancy, preventing unbounded `dt` accumulation.

## Proof of Concept
1. Deploy pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%), `decayPerSecondE8 = 58` (~5%/day), `timelock = 0`.
2. LP Alice adds liquidity to bin 0. Admin sets watermarks: `hwm0 = 1000`, `hwm1 = 1000`, `lastDecayTs = T0`.
3. Alice removes all liquidity. `totalShares[0] = 0`.
4. 20 days pass (`dt = 1_728_000 s`). Swaps occur on other bins; bin 0 is skipped each time. `lastDecayTs` for bin 0 remains `T0`.
5. Bob adds liquidity to bin 0. `totalShares[0] > 0`.
6. A swap crosses bin 0. `_checkAndUpdateWatermarks` is called:
   - `dt = block.timestamp - T0 = 1_728_000`
   - `factor = 58 * 1_728_000 = 100_224_000 ≥ 1e8` → `_decayed(1000, 58, 1_728_000) = 0`
   - `_applyWatermark(metricT0, 0, 950_000)` → `metricT0 >= 0` → returns `(metricT0, false)`
7. No `OracleStopLossTriggered` revert. The swap completes. Bob's deposited value is drained beyond the 5% drawdown floor with no on-chain protection. The watermark is silently reset to the post-drain metric.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L236-242)
```text
    for (uint256 i = 0; i < count; i++) {
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
      (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
      (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
      _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
    }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L268-268)
```text
    uint256 dt = block.timestamp - hwmS.lastDecayTs;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L284-284)
```text
    hwmS.lastDecayTs = uint32(block.timestamp);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L319-323)
```text
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
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
