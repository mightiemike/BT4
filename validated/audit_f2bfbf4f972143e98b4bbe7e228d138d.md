Audit Report

## Title
Pool Admin Bypasses `maxAdminSpreadFeeE6` Cap via Uncapped `setPoolBinAdditionalFees` - (File: metric-core/contracts/MetricOmmPoolFactory.sol)

## Summary
`setPoolBinAdditionalFees` forwards per-bin additional fees (`addFeeBuyE6`, `addFeeSellE6`) directly to the pool with no comparison against `maxAdminSpreadFeeE6`, while the parallel setter `setPoolAdminFees` enforces that cap. A pool admin can therefore set per-bin fees up to the `uint16` maximum (65 535 E6 ≈ 6.55%) regardless of the governance-configured cap, causing swappers in the affected bin to pay excess fees immediately and without a timelock.

## Finding Description
`setFeeCaps` (L284–315) establishes `maxAdminSpreadFeeE6` as the hard ceiling for pool-admin-controlled spread fees. `setPoolAdminFees` (L408–435) enforces this ceiling:

```solidity
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
``` [1](#0-0) 

`setPoolBinAdditionalFees` (L450–457) performs no equivalent check and passes values straight through:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool) {
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
``` [2](#0-1) 

`setBinAdditionalFees` on the pool validates only the bin index, not the fee magnitude: [3](#0-2) 

These per-bin fees are added directly to `baseFeeX64` in both swap directions: [4](#0-3) [5](#0-4) 

Because `addFeeBuyE6`/`addFeeSellE6` are `uint16`, the maximum settable value is 65 535 E6 ≈ 6.55%, which can exceed any governance-configured `maxAdminSpreadFeeE6` value.

## Impact Explanation
This is a direct admin-boundary break: the pool admin role is semi-trusted only within the caps set by governance. By using `setPoolBinAdditionalFees`, the pool admin can charge swappers an effective fee exceeding `maxAdminSpreadFeeE6` in any bin. The excess fee accrues to LPs (potentially including the pool admin), constituting a direct loss of user principal above the governance-intended ceiling. The change takes effect for the very next swap with no timelock.

## Likelihood Explanation
The pool admin role is a standard operational role. The bypass requires a single transaction with no special preconditions beyond holding the pool admin role. The function is a normal admin operation, making accidental or intentional exploitation straightforward and repeatable.

## Recommendation
Add cap validation inside `setPoolBinAdditionalFees` before forwarding to the pool, mirroring `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool) {
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Additionally, consider applying a timelock to per-bin fee changes so users can observe and react before the new rate takes effect.

## Proof of Concept
1. Factory owner deploys a pool with `maxAdminSpreadFeeE6 = 10_000` (1%).
2. Pool admin calls `setPoolAdminFees(pool, 15_000, 0)` → reverts with `AdminFeeTooHigh` (cap enforced correctly).
3. Pool admin calls `setPoolBinAdditionalFees(pool, 0, 65_535, 65_535)` → **succeeds** with no revert.
4. `setBinAdditionalFees` stores `addFeeBuyE6 = 65_535` and `addFeeSellE6 = 65_535` for bin 0.
5. The next swap in bin 0 computes fee as `baseFeeX64 + mulDiv(65_535, ONE_X64, 1e6)`, adding ≈6.55% on top of the oracle spread — far above the 1% governance cap.
6. The excess fee is retained by LPs; the swapper suffers a direct loss of principal above the governance-intended ceiling.

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L414-415)
```text
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L450-457)
```text
  function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L464-474)
```text
  function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
  {
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L999-999)
```text
              params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1177-1177)
```text
              params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6),
```
