Audit Report

## Title
Stop-Loss Guard Uses Arithmetic Mid Price Instead of Geometric Mid Price, Allowing LP Value to Drain Beyond Configured Drawdown — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid price as the arithmetic mean of bid and ask, while the pool's swap engine uses the geometric mean via `SwapMath.midAndSpreadFeeX64FromBidAsk`. By the AM-GM inequality, the arithmetic mean is always ≥ the geometric mean, so per-share value metrics are inflated relative to the pool's actual internal pricing. When the oracle spread widens between the watermark-setting swap and a subsequent draining swap, the inflated current metric can exceed the drawdown floor even though actual LP value has fallen past the configured threshold, silently bypassing the stop-loss guard.

## Finding Description

In `_afterSwapOracleStopLoss` (line 218), the mid price is computed as:

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

The pool's swap engine uses the geometric mean (`SwapMath.midAndSpreadFeeX64FromBidAsk`, `SwapMath.sol` line 70):

```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
```

`PriceVelocityGuardExtension` already uses the correct geometric mean (`PriceVelocityGuardExtension.sol` line 48):

```solidity
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

`OracleValueStopLossExtension` does not import `SwapMath` at all, so it cannot call the correct function.

The `metricT1` value (line 255) is:

```solidity
metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

Because `midPriceX64` (AM) > actual pool mid (GM), `metricT1` is inflated. The watermark ratchets up to this inflated value. The breach check (line 334) is:

```solidity
breached = metric < (hwm * floorMultiplier) / E6;
```

When the spread widens from S₁ (at watermark-setting time) to S₂ > S₁ (at drain time), the ratio AM/GM(S₂) / AM/GM(S₁) > 1 inflates the current metric relative to the floor, suppressing the breach signal even when actual LP value has fallen past `drawdownE6`.

**Exploit path:**
1. Swap occurs at narrow spread S₁ (e.g., 1%): watermark set to `V × AM/GM(S₁) ≈ V × 1.00005`.
2. Oracle spread widens to S₂ (e.g., 100%, e.g., Pyth confidence interval during volatility spike).
3. Unprivileged attacker calls `swap(zeroForOne=false, ...)`, draining 10% of token0.
4. `afterSwap` computes `midPriceX64 = (100 + 200)/2 = 150` (AM); GM = `sqrt(100×200) ≈ 141.42`.
5. `metricT1 = 0.90V × 1.061 ≈ 0.9549V`.
6. Floor = `V × 1.00005 × 0.95 ≈ 0.9500V`.
7. `0.9549V > 0.9500V` → breach check is false → swap succeeds.
8. LPs have lost 10% while the configured drawdown was 5%.

No existing guard catches this: the AM/GM discrepancy is structural and grows monotonically with spread width.

## Impact Explanation

The `OracleValueStopLossExtension` is documented to guarantee: *"value per share at oracle marks cannot fall faster than drawdown (one-time) + decay × t (ongoing)."* This invariant is broken. LPs suffer direct loss of principal beyond the configured safety threshold. The effective protection is reduced by the factor AM/GM(S₂) / AM/GM(S₁), which for spread variation from 1% to 100% yields roughly half the configured drawdown protection. This is a direct loss of LP principal above Sherlock thresholds.

## Likelihood Explanation

The trigger is any unprivileged `swap()` call. No special role or malicious setup is required. The discrepancy activates whenever the oracle spread at the time of a draining swap is wider than the spread at the time the watermark was last set. For pools using Pyth or other confidence-interval-based oracles, spread variation is a normal market condition during volatility events, not an edge case. The attack is repeatable and requires no privileged access.

## Recommendation

Replace the arithmetic mean with the geometric mean, consistent with the pool's internal pricing. Add the `SwapMath` import and replace line 218:

```solidity
// Before (incorrect):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct):
(uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
```

`SwapMath.midAndSpreadFeeX64FromBidAsk` computes `Math.sqrt(bidPriceX64 * askPriceX64)` and is already used correctly by `PriceVelocityGuardExtension`.

## Proof of Concept

1. Deploy a pool with `OracleValueStopLossExtension`, `drawdownE6 = 50_000` (5%).
2. Oracle: bid = 990, ask = 1010 (~1% spread). Execute a swap; watermark for bin 0 is set to `metricT1 ≈ V × 1.00005`.
3. Oracle moves to bid = 100, ask = 200 (100% spread — Pyth confidence interval widens during volatility).
4. Attacker calls `swap(zeroForOne=false, ...)`, draining 10% of token0 from the pool.
5. `afterSwap` computes `midPriceX64 = (100 + 200)/2 = 150` (AM). `metricT1 = 0.90V × 1.061 ≈ 0.9549V`.
6. Floor = `V × 1.00005 × 0.95 ≈ 0.9500V`.
7. Breach check: `0.9549V < 0.9500V` → false → swap succeeds.
8. LPs lose 10% while the configured drawdown was 5%; the stop-loss guard is silently bypassed.