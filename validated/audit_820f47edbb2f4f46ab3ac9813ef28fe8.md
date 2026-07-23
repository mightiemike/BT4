Audit Report

## Title
Unconditional `lastDecayTs` reset in `_checkAndUpdateWatermarks` allows any token1-direction swap to permanently suppress watermark decay, indefinitely blocking zeroForOne swaps — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary
`_checkAndUpdateWatermarks` resets `hwmS.lastDecayTs = block.timestamp` unconditionally on every non-reverting call. When `breach0=true` and `zeroForOne=false`, the function does not revert, writes the old high watermark back unchanged, and resets the decay clock to now. Any subsequent token1-direction swap repeats this, keeping `dt` perpetually near zero and preventing the watermark from ever decaying below the drawdown floor. The zeroForOne direction remains blocked indefinitely without admin intervention.

## Finding Description
In `_checkAndUpdateWatermarks` ( [1](#0-0) ), the revert guard at line 271 only fires when `breach0 && zeroForOne`:

```solidity
if (breach0 && zeroForOne) {
    revert OracleStopLossTriggered(...);
}
```

When `breach0=true` and `zeroForOne=false`, execution continues past the guard. `_applyWatermark` ( [2](#0-1) ) returns `(hwm, breached)` — the old high watermark — when `metric < hwm`, so `hwm0` is the old high. Then:

```solidity
hwmS.token0 = uint104(hwm0);   // old high preserved
hwmS.lastDecayTs = uint32(block.timestamp);  // clock reset unconditionally
``` [3](#0-2) 

The next zeroForOne swap computes `dt = block.timestamp - lastDecayTs` ≈ 0, so `_decayed` returns the full old watermark, the breach is re-detected, and the swap reverts. Any token1-direction swap by any user repeats this cycle indefinitely.

The existing test `test_decayRearmsAfterPermanentRepricing` ( [4](#0-3) ) only passes because it warps time with no intervening swaps — a condition that does not hold in a live pool.

## Impact Explanation
This breaks core swap functionality for the zeroForOne direction of any pool where a genuine breach condition exists and any token1-direction trading activity occurs. This meets the "broken core pool functionality causing unusable swap flows" impact gate. Severity is High: one swap direction is permanently disabled by ordinary, unprivileged pool usage, with no automatic recovery path — only a privileged admin timelock action can restore it.

## Likelihood Explanation
The breach condition arises naturally from oracle mid-price moves. Once triggered, any arbitrageur or normal user executing a token1-direction swap (e.g., correcting the price back) resets the clock. No special attacker setup is required; ordinary pool usage is sufficient to keep the clock reset indefinitely.

## Recommendation
Only reset `lastDecayTs` when neither direction is breached, or maintain separate per-direction timestamps:

```solidity
if (!breach0 && !breach1) {
    hwmS.lastDecayTs = uint32(block.timestamp);
}
```

Alternatively, track `lastDecayTs0` and `lastDecayTs1` independently and only reset each when the corresponding metric is not in breach.

## Proof of Concept
```solidity
// 1. Configure pool: drawdown=50%, decay=58 E8/s (~5%/day)
// 2. Establish watermark at metricT0 = 200
// 3. Price spikes: metricT0 drops to 80 (below floor of 100 = 200 * 50%)
// 4. zeroForOne swap reverts — breach confirmed
// 5. Execute token1-direction swap (zeroForOne=false) — passes, resets lastDecayTs to now
// 6. Warp 5 days
// 7. Execute another token1-direction swap — resets lastDecayTs again to now
// 8. zeroForOne swap still reverts — dt ≈ 0, watermark undecayed
// assertEq(hwmS.lastDecayTs, block.timestamp); // clock reset by step 7
// Repeat step 7 indefinitely — pool permanently blocked in zeroForOne direction
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-285)
```text
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

**File:** metric-periphery/test/extensions/OracleValueStopLossSubExtension.t.sol (L439-461)
```text
  function test_decayRearmsAfterPermanentRepricing() public {
    uint128 price = uint128(Q64);
    _storeBin(0, 1000, 1000, BIN_SHARES);
    _configure(50_000, 58); // ~5%/day

    _exposeStopLoss(0, 0, price, false);

    _storeBin(0, 800, 800, BIN_SHARES);

    vm.expectRevert();
    _exposeStopLoss(0, 0, price, true);

    // Warp until decayed watermark ratchets below the drawdown floor (~4 days at 58 E8/s).
    vm.warp(block.timestamp + 5 days);

    _exposeStopLoss(0, 0, price, true);

    (uint256 hwm0, uint256 hwm1) = extension.currentHighWatermarks(address(mockPool), 0);
    uint256 cur0 = _computeMetricToken0(800, 800, BIN_SHARES, price);
    uint256 cur1 = _computeMetricToken1(800, 800, BIN_SHARES, price);
    assertGe(hwm0, cur0);
    assertGe(hwm1, cur1);
  }
```
