Audit Report

## Title
Pool Admin Bypasses Fee Cap via Uncapped Per-Bin Additional Fees in `setPoolBinAdditionalFees` — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary

`setPoolBinAdditionalFees` allows a pool admin to set per-bin additional spread fees (`addFeeBuyE6`, `addFeeSellE6`) with no upper-bound validation, while `setPoolAdminFees` enforces `maxAdminSpreadFeeE6`. A pool admin can silently exceed the protocol-enforced fee cap on targeted bins, causing traders to pay more than the maximum the cap system is designed to allow, with the excess accruing as spread surplus collectible by the admin.

## Finding Description

`setPoolAdminFees` enforces the cap at `MetricOmmPoolFactory.sol` L414–415:
```solidity
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
```

However, `setPoolBinAdditionalFees` at L450–457 passes `addFeeBuyE6` and `addFeeSellE6` directly to the pool with no cap check:
```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

`MetricOmmPool.setBinAdditionalFees` at L469–473 only validates the bin index:
```solidity
if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
BinState storage s = _binStates[bin];
s.addFeeBuyE6 = addFeeBuyE6;
s.addFeeSellE6 = addFeeSellE6;
```

During swap execution at L999, the additional fee is added on top of the base fee:
```solidity
params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6)
```

And in the quote path at L540–544:
```solidity
uint256 buyFeeX64 = baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6);
uint256 askBeforeNotional = Math.mulDiv(marginalPriceX64, ONE_X64 + buyFeeX64, ONE_X64, Math.Rounding.Ceil);
```

`BinState.addFeeBuyE6` and `addFeeSellE6` are `uint16` (max 65 535 ≈ 6.55% in E6 units), with no cap enforced post-creation. The spread surplus is then distributed to admin and protocol at L391–395 via `collectFees`, so the excess flows directly to the admin fee destination.

## Impact Explanation

This is a confirmed admin-boundary break: the pool admin exceeds the `maxAdminSpreadFeeE6` cap via an uncapped code path. Traders swapping through the targeted bin pay fees above the protocol-enforced cap — up to 6.55% additional per swap — as direct, quantifiable loss of trader principal. The excess accrues as spread surplus and is extracted by the admin via `collectFees`, satisfying the "Admin-boundary break: pool admin exceeds caps" allowed impact criterion at High severity.

## Likelihood Explanation

The pool admin is explicitly semi-trusted only within caps and timelocks. The bypass requires a single transaction with no additional privilege, no timelock, and no on-chain signal distinguishing it from a legitimate fine-tuning call. `addFeeBuyE6 = 65535` is a valid `uint16` value accepted without revert. The attack is repeatable at any time after pool creation.

## Recommendation

Add cap checks in `setPoolBinAdditionalFees` before forwarding to the pool:
```solidity
if (addFeeBuyE6  > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
```
Alternatively, enforce the cap inside `MetricOmmPool.setBinAdditionalFees` so the pool itself rejects out-of-range values regardless of the caller path.

## Proof of Concept

1. Factory owner sets `maxAdminSpreadFeeE6 = 200_000` (20%) via `setFeeCaps`.
2. Pool admin calls `setPoolAdminFees(pool, 200_000, 0)` — accepted, at the cap.
3. Pool admin calls `setPoolBinAdditionalFees(pool, 0, 65535, 65535)` — **no revert**, accepted.
4. A trader swaps through bin 0. Effective buy fee = `200_000 + 65_535 = 265_535` (26.55%) instead of the capped 20%.
5. The inflated ask price causes the trader to send ≈6.55% more token1 than the cap permits; the excess accrues as spread surplus.
6. Pool admin calls `collectPoolFees` and receives the excess above the intended cap.