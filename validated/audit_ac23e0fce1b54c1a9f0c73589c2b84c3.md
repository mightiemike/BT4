The code confirms the claim. Let me verify the key mechanics:

1. `executeOracleStopLossDecay` (lines 139–147): writes the new rate to `oracleStopLossConfig[pool_].decayPerSecondE8` but never touches `highWatermarks[pool_][binIdx].lastDecayTs` for any bin.

2. `_checkAndUpdateWatermarks` (line 268): computes `dt = block.timestamp - hwmS.lastDecayTs` using the stale timestamp, then passes the **current** (new) `decayRate` to `_decayed`.

3. `_decayed` (lines 321–322): `factor = ratePerSecondE8 * dt; if (factor >= E8) return 0;` — any idle pool where `dt > 1e8 / newRate` seconds will have all watermarks zeroed on the next swap.

The concrete scenario checks out: with `newRate = 1000` and `dt = 37 days (3,196,800 s)`, `factor = 3,196,800,000 ≥ 1e8`, so `_decayed` returns 0. After that, `_applyWatermark` with `hwm = 0` always returns `(metric, false)` — no breach ever fires again for those bins.

The pool admin is acting within authorized caps (`rate ≤ 1e8`, timelock respected), but the retroactive application constitutes a fund-impacting cap failure: the drawdown protection the extension is designed to enforce is permanently disabled for affected bins.

---

Audit Report

## Title
Retrospective Decay-Rate Application in `executeOracleStopLossDecay` Permanently Zeroes Stop-Loss Watermarks — (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary
`executeOracleStopLossDecay` stores the new `decayPerSecondE8` without resetting `lastDecayTs` for any bin. The next swap applies the new rate over the full elapsed time since the last swap via `_decayed`, which returns 0 whenever `ratePerSecondE8 * dt ≥ 1e8`. Once zeroed, `_applyWatermark` never reports a breach, permanently disabling the stop-loss for those bins and exposing LP principal to unguarded value loss.

## Finding Description
`executeOracleStopLossDecay` (lines 139–147) writes `oracleStopLossConfig[pool_].decayPerSecondE8 = decay` but leaves every bin's `BinHighWatermarks.lastDecayTs` unchanged. On the next swap, `_checkAndUpdateWatermarks` (line 268) computes `dt = block.timestamp - hwmS.lastDecayTs` spanning the entire idle period, then calls `_decayed(hwmS.token0, decayRate, dt)` with the new rate (line 270). Inside `_decayed` (lines 321–322), `factor = ratePerSecondE8 * dt`; if `factor ≥ 1e8` the function returns 0. With `newRate = 1000` and a pool idle for 37 days (`dt = 3,196,800 s`), `factor = 3,196,800,000 ≥ 1e8`, so both `hwm0` and `hwm1` are zeroed. `_applyWatermark` (line 333) then always returns `(metric, false)` because `metric >= 0 == hwm`, so `OracleStopLossTriggered` is never emitted for those bins again. The timelock is respected but does not prevent the retroactive erasure because `lastDecayTs` is not snapshotted at execution time.

## Impact Explanation
The stop-loss extension's sole purpose is to cap per-bin LP value loss at `drawdownE6`. Once watermarks are zeroed, the guard is permanently disabled for those bins: any subsequent swap — including one that extracts all remaining token0 or token1 — passes `afterSwap` without triggering `OracleStopLossTriggered`. LPs suffer direct loss of principal bounded only by the bin's total balance, which can be the entire LP deposit. This is a fund-impacting cap failure: the drawdown protection the extension contractually enforces is bypassed.

## Likelihood Explanation
The trigger is the pool admin executing a timelocked decay-rate increase — a routine administrative action explicitly supported by the contract. No malicious setup is required. The admin proposes a higher rate, waits for the timelock, and executes. The retroactive zeroing is most severe when the pool has been swap-idle for longer than `1e8 / newRate` seconds before execution. With `newRate = 1000` (a modest 0.001%/s), the threshold is ~28 hours of inactivity — a condition that naturally arises in low-volume pools or during market downtime. The pool admin is semi-trusted; this path constitutes a fund-impacting cap failure within their authorized action space.

## Recommendation
When `executeOracleStopLossDecay` is called, apply the old rate's accumulated decay to every bin's watermark before writing the new rate, then reset `lastDecayTs` to `block.timestamp`. This ensures the new rate only applies prospectively. Since iterating all bins is not directly feasible without enumeration, the minimal correct fix is to store the rate alongside `lastDecayTs` in `BinHighWatermarks` so `_decayed` can split the elapsed interval at the rate-change boundary, applying the old rate up to the change timestamp and the new rate thereafter.

## Proof of Concept
```solidity
// 1. Deploy pool with decayPerSecondE8 = 0, drawdownE6 = 50_000 (5%)
// 2. Swaps occur; watermarks set to hwm0 = hwm1 = METRIC_SCALE (1e6)
//    lastDecayTs = block.timestamp (day 0)

// 3. Pool goes idle for 30 days
vm.warp(block.timestamp + 30 days);

// 4. Admin proposes decayPerSecondE8 = 1_000
extension.proposeOracleStopLossDecay(pool, 1_000);

// 5. Timelock elapses (7 days) — now 37 days since last swap
vm.warp(block.timestamp + 7 days);

// 6. Admin executes — lastDecayTs for all bins still = day 0
extension.executeOracleStopLossDecay(pool);

// 7. Next swap: dt = 37 days = 3_196_800 s
//    factor = 1_000 * 3_196_800 = 3_196_800_000 >= 1e8
//    _decayed returns 0 for both token0 and token1 watermarks
//    _applyWatermark(metric, 0, floorMultiplier) → (metric, false) always
//    OracleStopLossTriggered is never emitted

// 8. Adversarial swap drains the bin; LP loses full bin balance with no stop-loss protection
assertEq(hwm.token0, 0);
assertEq(hwm.token1, 0);
```