Audit Report

## Title
`executeOracleStopLossDecay` applies new decay rate retroactively over full elapsed period, zeroing watermarks and disabling stop-loss — (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary
`executeOracleStopLossDecay` writes only to `oracleStopLossConfig[pool_].decayPerSecondE8` without resetting `lastDecayTs` in any bin's `BinHighWatermarks`. On the next swap, `_checkAndUpdateWatermarks` computes `dt = block.timestamp - hwmS.lastDecayTs` and feeds the new (potentially maximum) rate into `_decayed` over the entire elapsed window — retroactively applying the new rate to a period when the old rate was active. With `decayPerSecondE8 = 1e8` (the maximum allowed value) and `dt ≥ 1 second`, every watermark is zeroed, causing `_applyWatermark` to never report a breach and the stop-loss to fail open for the next trade.

## Finding Description

`executeOracleStopLossDecay` only writes the new rate:

```solidity
// L139-147
function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    ...
    uint32 decay = sched.pendingDecayPerSecondE8;
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;   // ← only this changes
    ...
}
``` [1](#0-0) 

`lastDecayTs` lives inside each bin's `BinHighWatermarks` and is only written by `_checkAndUpdateWatermarks` (on every swap, L284) and by `executeOracleStopLossHighWatermarks` (which explicitly resets it to `block.timestamp`, L174). `executeOracleStopLossDecay` performs no equivalent reset. [2](#0-1) 

On the next swap, `_checkAndUpdateWatermarks` computes `dt` spanning the entire gap since the last swap:

```solidity
// L267-270
BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
uint256 dt = block.timestamp - hwmS.lastDecayTs;   // ← spans entire gap since last swap
(uint256 hwm0, bool breach0) =
    _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
``` [3](#0-2) 

`_decayed` uses the new rate over the full `dt`:

```solidity
// L319-324
function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;   // ← zeroed when rate × elapsed ≥ 1e8
    return hwm - (hwm * factor) / E8;
}
``` [4](#0-3) 

`_validateDecay` permits rates up to `1e8`: [5](#0-4) 

With `decayPerSecondE8 = 1e8` and `dt ≥ 1 second`, `factor = 1e8 * dt ≥ 1e8 = E8`, so `_decayed` returns 0. `_applyWatermark` then sees `hwm = 0`, so `metric >= 0` is always true, `breached = false`, and the stop-loss never fires: [6](#0-5) 

The contrast with `executeOracleStopLossHighWatermarks` (which correctly resets `lastDecayTs = block.timestamp` when watermarks are manually set) confirms this is an omission, not a design choice: [7](#0-6) 

## Impact Explanation

The stop-loss extension's stated guarantee — *"value per share at oracle marks cannot fall faster than drawdown (one-time) + decay × t (ongoing)"* — is broken entirely. A swap that should be blocked because the pool's per-share value has fallen below the drawdown floor relative to the established watermark is allowed to proceed. LP principal drains through the unguarded swap. Any pool whose value has already declined to within the drawdown band is immediately exposed the moment the watermarks are zeroed. This is a direct loss of LP funds matching the "broken core pool functionality causing loss of funds" and "oracle guard path" impact categories. [8](#0-7) 

## Likelihood Explanation

The pool admin is semi-trusted and must pass the timelock before executing the change. However: (1) the timelock only delays when the new rate is stored — once stored, the retroactive application happens automatically on the next public swap; (2) the timelock period can be reduced via `executeOracleStopLossTimelock` (also timelocked, but the initial timelock can be set to zero at pool creation); (3) the maximum allowed decay rate (`1e8`) is reachable through the normal admin flow with no additional cap; (4) any public trader can trigger the first post-change swap, causing watermarks to be zeroed and the stop-loss to fail open — the pool admin does not need to execute the swap themselves. The effective decay applied retroactively (`1e8 × elapsed_seconds`) far exceeds what the per-second cap intends, constituting a fund-impacting cap failure. [1](#0-0) 

## Recommendation

In `executeOracleStopLossDecay`, before writing the new rate, apply the old decay rate to every active bin's watermark and reset `lastDecayTs` to `block.timestamp`. Concretely: read `oracleStopLossConfig[pool_].decayPerSecondE8` as `oldRate`, iterate over all bins in the pool's configured range, read each `BinHighWatermarks`, apply `_decayed(hwm, oldRate, block.timestamp - lastDecayTs)`, write the decayed value back, and set `lastDecayTs = block.timestamp`. Then store the new `decayPerSecondE8`. This mirrors the pattern already used in `executeOracleStopLossHighWatermarks` (L173-174) and ensures the new rate is only applied to time elapsed after the change. [2](#0-1) 

## Proof of Concept

```
State at T=0:
  decayPerSecondE8 = 10 (slow)
  Bin 0 watermark token0 = 1000, lastDecayTs = T=0

T=1: Swap occurs → watermark stays ~1000, lastDecayTs = T=1

T=2: Pool admin proposes decayPerSecondE8 = 1e8 (max)
     Timelock = 3 days → executeAfter = T=2 + 3 days

T=2 + 3 days: Admin calls executeOracleStopLossDecay
  → oracleStopLossConfig[pool].decayPerSecondE8 = 1e8
  → lastDecayTs in Bin 0 is still T=1 (NOT updated)

T=2 + 3 days + 2 seconds: Public trader calls swap
  → _checkAndUpdateWatermarks called
  → dt = (T=2 + 3 days + 2s) - T=1 ≈ 3 days + 1s >> 1 second
  → factor = 1e8 * dt >> 1e8 → _decayed returns 0
  → hwm0 = 0, hwm1 = 0
  → _applyWatermark: metric >= 0 always → breached = false
  → Stop-loss does NOT trigger
  → Value-leaking swap executes, LP funds drain
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L27-28)
```text
///      Watermarks decay linearly at decayPerSecondE8 (lazy, per bin). Guarantee: value per
///      share at oracle marks cannot fall faster than drawdown (one-time) + decay * t (ongoing).
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L139-147)
```text
  function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDecayExecuteAfter == 0) revert OracleStopLossNoPendingDecay(pool_);
    _requireElapsed(sched.pendingDecayExecuteAfter);
    uint32 decay = sched.pendingDecayPerSecondE8;
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;
    (sched.pendingDecayPerSecondE8, sched.pendingDecayExecuteAfter) = (0, 0);
    emit OracleStopLossDecaySet(pool_, decay);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L168-177)
```text
  /// @notice Apply the pending watermarks. Also resets the decay clock for the bin.
  function executeOracleStopLossHighWatermarks(address pool_) external onlyPoolAdmin(pool_) {
    PendingHighWatermarks memory pending = pendingHighWatermark[pool_];
    if (pending.executeAfter == 0) revert OracleStopLossNoPendingHighWatermark(pool_);
    _requireElapsed(pending.executeAfter);
    highWatermarks[pool_][pending.binIdx] =
      BinHighWatermarks({token0: pending.token0, token1: pending.token1, lastDecayTs: uint32(block.timestamp)});
    delete pendingHighWatermark[pool_];
    emit OracleStopLossHighWatermarkUpdated(pool_, pending.binIdx, pending.token0, pending.token1);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-270)
```text
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L309-311)
```text
  function _validateDecay(uint256 decayPerSecondE8) private pure {
    if (decayPerSecondE8 > E8) revert OracleStopLossDecayTooLarge(decayPerSecondE8);
  }
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
