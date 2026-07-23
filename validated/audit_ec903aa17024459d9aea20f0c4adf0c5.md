Audit Report

## Title
Off-by-one in `_validateDrawdown` allows pool admin to set `drawdownE6 = 1e6`, silently disabling stop-loss for all LPs — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`_validateDrawdown` uses a strict `>` comparison, permitting `drawdownE6 == 1e6` (100%) to be stored. When this value is later read in `_afterSwapOracleStopLoss`, `floorMultiplier` becomes `0`, reducing the breach predicate in `_applyWatermark` to `metric < 0`, which is structurally impossible for `uint256`. The stop-loss is permanently silenced for every bin in the pool after the timelock elapses, with no on-chain signal.

## Finding Description

`_validateDrawdown` at line 306 rejects only values **strictly greater than** `E6`:

```solidity
if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
```

`E6 == 1e6` therefore passes. After `executeOracleStopLossDrawdown` stores it at line 117:

```solidity
oracleStopLossConfig[pool_].drawdownE6 = drawdown;   // == 1e6
```

In `_afterSwapOracleStopLoss` at lines 216–234:

```solidity
uint256 drawdown = cfg.drawdownE6;          // 1e6
if (drawdown == 0) return;                  // does NOT fire
uint256 floorMultiplier = E6 - drawdown;    // 1e6 - 1e6 = 0
```

In `_applyWatermark` at line 334:

```solidity
breached = metric < (hwm * floorMultiplier) / E6;
//       = metric < (hwm * 0) / 1e6
//       = metric < 0   ← always false for uint256
```

`OracleStopLossTriggered` is never emitted regardless of how much value is drained from the bin. The stop-loss hook runs to completion on every swap but never reverts. The only existing guard (`drawdown == 0` early-exit at line 217) does not cover the `drawdown == 1e6` case. [1](#0-0) [2](#0-1) [3](#0-2) 

## Impact Explanation

LPs in any pool using `OracleValueStopLossExtension` lose their stop-loss protection entirely. A pool admin who sets `drawdownE6 = 1e6` after the timelock can then allow (or execute) value-draining swaps — e.g., a large directional swap that moves the oracle mid far from the bin's fair value — without the extension ever blocking them. LP principal is at risk of full drain with no on-chain circuit breaker. This satisfies the **Admin-boundary break** impact gate: the pool admin exceeds the intended drawdown cap (which should be `< 100%`) through a validation off-by-one, disabling a core LP protection mechanism. [4](#0-3) [5](#0-4) 

## Likelihood Explanation

The pool admin is a semi-trusted role constrained by caps and timelocks. The timelock gives LPs a window to exit, but LPs who do not actively monitor pending schedule changes are silently exposed after the timelock elapses. The action requires only two pool-admin transactions (`proposeOracleStopLossDrawdown(pool, 1e6)` then `executeOracleStopLossDrawdown(pool)`) and no privileged factory-owner or oracle-admin access. [6](#0-5) [7](#0-6) 

## Recommendation

Change the validation to reject `drawdownE6 == E6` as well:

```solidity
// Before (allows 100% drawdown):
if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);

// After (rejects 100% drawdown):
if (drawdownE6 >= E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
```

This prevents the invalid value from ever being stored and is consistent with the intent that `drawdownE6` represents a fractional drawdown strictly less than 100%. [1](#0-0) 

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import "forge-std/Test.sol";
import {OracleValueStopLossExtension} from
    "metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol";

function test_drawdownE6_1e6_disables_stop_loss() public {
    uint104 t0 = 1000;
    uint104 t1 = 1000;
    uint128 price = uint128(Q64);

    _storeBin(0, t0, t1, BIN_SHARES);

    // Step 1: admin sets drawdown to exactly 1e6 (passes _validateDrawdown due to > not >=)
    vm.startPrank(admin);
    extension.proposeOracleStopLossDrawdown(address(mockPool), 1e6);
    extension.executeOracleStopLossDrawdown(address(mockPool)); // timelock=0 in test setup
    vm.stopPrank();

    // Confirm stored value
    (uint32 dd,,,) = extension.oracleStopLossConfig(address(mockPool));
    assertEq(dd, 1e6);

    // Step 2: establish watermarks at current value
    _exposeStopLoss(0, 0, price, false);

    // Step 3: drain the bin (remove 80% of both reserves)
    _storeBin(0, 200, 200, BIN_SHARES);

    // Step 4: assert stop-loss does NOT trigger despite 80% value loss
    // floorMultiplier = 1e6 - 1e6 = 0 → breached = metric < 0 → always false
    _exposeStopLoss(0, 0, price, true);  // zeroForOne — should have triggered, does not
    _exposeStopLoss(0, 0, price, false); // oneForZero — should have triggered, does not
}
``` [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-120)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
  }

  function executeOracleStopLossDrawdown(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDrawdownExecuteAfter == 0) revert OracleStopLossNoPendingDrawdown(pool_);
    _requireElapsed(sched.pendingDrawdownExecuteAfter);
    uint32 drawdown = sched.pendingDrawdownE6;
    oracleStopLossConfig[pool_].drawdownE6 = drawdown;
    (sched.pendingDrawdownE6, sched.pendingDrawdownExecuteAfter) = (0, 0);
    emit OracleStopLossDrawdownSet(pool_, drawdown);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L215-243)
```text
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L31-35)
```text
  modifier onlyPoolAdmin(address pool_) {
    address poolAdmin = IMetricOmmPoolFactory(FACTORY).poolAdmin(pool_);
    if (msg.sender != poolAdmin) revert OnlyPoolAdmin(pool_, msg.sender, poolAdmin);
    _;
  }
```
