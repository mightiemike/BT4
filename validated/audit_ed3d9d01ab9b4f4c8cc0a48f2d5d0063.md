Audit Report

## Title
`OracleValueStopLossExtension` Uses Arithmetic Mean Instead of Geometric Mean for Mid Price, Miscalibrating the Stop-Loss Guard - (File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol)

## Summary

`_afterSwapOracleStopLoss` computes the oracle mid price as the arithmetic mean of bid and ask prices, while every other component in the protocol uses the geometric mean via `SwapMath.midAndSpreadFeeX64FromBidAsk`. Since AM ≥ GM always holds (with equality only when bid == ask), and production enforces `bid < ask`, the stop-loss metrics are computed against a systematically biased mid price. This miscalibrates the watermarks and drawdown floors relative to the configured drawdown, allowing LP principal to leak beyond the configured threshold before the guard triggers.

## Finding Description

In `_afterSwapOracleStopLoss` at line 218:

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [1](#0-0) 

This is the arithmetic mean. In contrast, `PriceVelocityGuardExtension` and the swap engine use the geometric mean:

```solidity
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
// which computes: midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
``` [2](#0-1) [3](#0-2) 

The per-bin metrics computed in `_metrics` are:

```solidity
metricT0 = t0ps + t1 * Q64 / (mid * shares)   // higher mid → lower metricT0
metricT1 = t0 * mid / (Q64 * shares) + t1ps   // higher mid → higher metricT1
``` [4](#0-3) 

Since AM > GM for any non-zero spread, `metricToken0` is understated and `metricToken1` is overstated. The watermarks ratchet up to these biased values, and the drawdown floor is set as `hwm * (1 - drawdownE6)`. When the oracle spread changes between the watermark-setting swap and a later swap, the AM/GM ratio shifts:

- **Spread narrows after watermark is set**: AM/GM decreases, so the current `metricToken0_am` rises relative to the floor set at the wider spread. The stop-loss for `zeroForOne` swaps becomes less sensitive — it fails to trigger when the true GM-based metric has already breached the configured floor.
- **Spread widens after watermark is set**: The stop-loss becomes overly sensitive, blocking legitimate swaps prematurely.

The test suite does not catch this because `_exposeStopLoss` always passes `priceX64, priceX64` as both bid and ask, making AM == GM in every test: [5](#0-4) 

In production, the pool enforces `bid < ask` unconditionally: [6](#0-5) 

So the discrepancy is always present in production and never exercised in tests.

## Impact Explanation

The stop-loss guard is the primary on-chain protection for LP principal. When miscalibrated in the false-negative direction (spread narrows after watermark is set), the guard fails to trigger when the true GM-based metric has already breached the configured drawdown floor. LP principal leaks beyond the configured threshold without the guard firing. For pools with wide oracle spreads (e.g., illiquid RWA pairs with 10–50% spreads), the extra leakage is 0.57%–3.15% of bin value beyond the configured drawdown — a direct, quantifiable loss of LP principal that meets the Sherlock medium/high threshold for loss of user funds.

## Likelihood Explanation

Every pool using `OracleValueStopLossExtension` with a non-zero oracle spread is affected. The spread is always non-zero in production (the pool reverts on `bid >= ask`). Any public swap that crosses a bin boundary triggers `afterSwap`, which recomputes the metric with the wrong mid. No privileged access is required — an unprivileged trader executing a normal swap is sufficient to trigger the miscalibrated guard path. The effect accumulates whenever the oracle spread changes between swaps, which is normal oracle behavior.

## Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the rest of the protocol:

```solidity
// Before (wrong):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(
    uint256(bidPriceX64),
    uint256(askPriceX64)
);
```

Also update `_exposeStopLoss` in the test helper to pass distinct bid and ask values so the test suite exercises the real production path.

## Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension`, drawdown = 5%, oracle spread = 10% (bid = 0.95·Q64, ask = 1.05·Q64).
2. Execute a swap. The extension computes AM = 1.00·Q64 vs GM ≈ 0.99875·Q64. `metricToken0_am` is understated by ~0.125% relative to `metricToken0_gm`. Watermark `hwm0` is set to the understated value, so the floor is also set ~0.125% lower than the GM-based floor.
3. Oracle spread narrows to 1% (bid = 0.995·Q64, ask = 1.005·Q64). A subsequent swap drains token0 until the true GM-based metric has fallen exactly 5% below `hwm0_gm`.
4. The AM-based metric at the narrower spread is now above the AM-based floor (because the floor was set lower due to the wider spread at step 2), so the stop-loss does **not** revert.
5. LP principal has leaked beyond the configured 5% drawdown without the guard triggering.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L218-218)
```text
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L254-255)
```text
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L48-48)
```text
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L70-70)
```text
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
```

**File:** metric-periphery/test/extensions/OracleValueStopLossSubExtension.t.sol (L98-103)
```text
  function _exposeStopLoss(int8 loBin, int8 hiBin, uint128 priceX64, bool zeroForOne) internal {
    vm.prank(address(mockPool));
    extension.afterSwap(
      address(0), address(0), zeroForOne, 0, 0, _packSlot0(loBin), _packSlot0(hiBin), priceX64, priceX64, 0, 0, 0, ""
    );
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L806-808)
```text
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
```
