Audit Report

## Title
Unbounded `newTimelock` in `proposeOracleStopLossTimelock` Causes uint32 Wrap, Bypassing the Stop-Loss Timelock Guard - (File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol)

## Summary

`proposeOracleStopLossTimelock` accepts any `uint32 newTimelock` with no upper-bound validation. Setting it to `type(uint32).max` causes `_afterTimelock` to silently truncate the uint256 sum to a past timestamp, making every subsequent proposal immediately executable. The pool admin can then instantly set `drawdownE6 = E6`, collapsing `floorMultiplier` to zero and permanently disabling the stop-loss guard for all LP positions.

## Finding Description

`proposeOracleStopLossTimelock` applies no validation to `newTimelock` before storing it as the pending value: [1](#0-0) 

By contrast, `proposeOracleStopLossDrawdown` and `proposeOracleStopLossDecay` both call `_validateDrawdown` / `_validateDecay` before proceeding: [2](#0-1) 

`_afterTimelock` adds the stored `uint32` timelock to `block.timestamp` in uint256 arithmetic, then **truncates to uint32**: [3](#0-2) 

With `timelock = type(uint32).max` (4,294,967,295) and `block.timestamp ≈ 1,753,000,000` (July 2026):
```
uint32(1_753_000_000 + 4_294_967_295)
= uint32(6_047_967_295)
= 6_047_967_295 % 4_294_967_296
= 1_752_999_999   ← one second in the past
```

`_requireElapsed` then passes immediately for every subsequent proposal because `block.timestamp (≈1,753,000,000) < 1,752,999,999` is false: [4](#0-3) 

`initialize` also stores `timelock` without any validation, enabling a pool to start with `timelock = 0`, making the first timelock-change proposal immediately executable: [5](#0-4) 

`_validateDrawdown` permits `drawdownE6 = E6` (the boundary value passes `> E6` check), so the admin can propose and immediately execute `drawdownE6 = 1e6`: [6](#0-5) 

## Impact Explanation

With `drawdownE6 = E6`, `floorMultiplier = E6 - drawdown = 0`. Inside `_applyWatermark`, the breach condition becomes `metric < (hwm * 0) / E6`, which is always false: [7](#0-6) 

The stop-loss guard in `afterSwap` is permanently silenced. Any oracle-price manipulation or value extraction through swaps will not be blocked, exposing all LP principal in every monitored bin. This is a direct admin-boundary break: the timelock is the sole on-chain constraint preventing the admin from making instant LP-adverse parameter changes, and it is fully bypassed.

## Likelihood Explanation

The trigger requires the pool admin — a semi-trusted role whose power is explicitly bounded by the timelock mechanism. The initial timelock can be zero (no validation in `initialize`), making the entire attack executable in a single block with no prior waiting period. If the initial timelock is non-zero, the admin must wait for it once before executing the timelock change to `type(uint32).max`; all subsequent proposals are then immediately executable. The attack is repeatable and requires no external conditions beyond admin key control.

## Recommendation

Add a `_validateTimelock` function mirroring the existing validators and call it in both `initialize` and `proposeOracleStopLossTimelock`:

```solidity
uint32 private constant MAX_TIMELOCK = 365 days;

function _validateTimelock(uint256 timelock) private pure {
    if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
}
```

Apply it in `initialize` alongside the existing validators: [8](#0-7) 

And at the top of `proposeOracleStopLossTimelock`: [1](#0-0) 

## Proof of Concept

```solidity
// Pool initialized with timelock = 0 (no validation, passes silently)
extension.initialize(pool, abi.encode(uint32(50_000), uint32(58), uint32(0)));

// Step 1: Admin sets timelock to type(uint32).max — no validation, passes silently
vm.prank(admin);
extension.proposeOracleStopLossTimelock(pool, type(uint32).max);
// executeAfter = uint32(block.timestamp + 0) = block.timestamp → _requireElapsed passes immediately
extension.executeOracleStopLossTimelock(pool);
// oracleStopLossConfig[pool].timelock == type(uint32).max

// Step 2: Admin proposes drawdown = E6 (100%) — passes _validateDrawdown (E6 <= E6)
// _afterTimelock returns uint32(block.timestamp + type(uint32).max) = past timestamp
vm.prank(admin);
extension.proposeOracleStopLossDrawdown(pool, 1e6);
// executeAfter is in the past → _requireElapsed passes immediately
extension.executeOracleStopLossDrawdown(pool);
// drawdownE6 == 1e6 → floorMultiplier == 0 → stop-loss permanently disabled

// Step 3: Verify stop-loss no longer triggers even on 100% value loss
// afterSwap will not revert regardless of bin value drop
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L56-62)
```text
    (uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
    _validateDrawdown(drawdownE6);
    _validateDecay(decayPerSecondE8);

    oracleStopLossConfig[pool] = PoolStopLossConfig({
      drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
    });
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L78-84)
```text
  function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    emit OracleStopLossTimelockProposed(pool_, newTimelock, executeAfter);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-110)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L297-299)
```text
  function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L301-303)
```text
  function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-307)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
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
