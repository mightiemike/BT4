Audit Report

## Title
Pool admin bypasses `maxAdminSpreadFeeE6` cap via unchecked `setPoolBinAdditionalFees` — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary

`MetricOmmPoolFactory.setPoolBinAdditionalFees` forwards raw `uint16` per-bin fee values directly to the pool without comparing them against `maxAdminSpreadFeeE6`, the factory-enforced hard cap on pool-admin-controlled spread fees. Every other admin fee setter in the contract enforces this cap. A pool admin can set per-bin additional fees up to `uint16.max = 65 535` (6.5535 % in E6 units), regardless of what `maxAdminSpreadFeeE6` is configured to, directly extracting excess fees from traders.

## Finding Description

The factory enforces a layered fee-cap system. The factory owner sets `maxAdminSpreadFeeE6` via `setFeeCaps`, bounded by `HARD_MAX_SPREAD_FEE_E6 = 200_000`. `setPoolAdminFees` enforces this cap before updating pool fees:

```solidity
// MetricOmmPoolFactory.sol L414-415
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
```

However, the sibling function `setPoolBinAdditionalFees` performs no such check:

```solidity
// MetricOmmPoolFactory.sol L450-457
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

`uint16` max is 65 535, representing 6.5535 % in E6 units. If `maxAdminSpreadFeeE6` is set to, e.g., 10 000 (1 %), a pool admin can still set per-bin additional fees to 65 535 via this path, completely bypassing the cap. The pool's `setBinAdditionalFees` interface declares no internal cap either. Additionally, unlike `setPoolAdminFees` which calls `collectFees` before updating (settling accrued fees at the old rate), `setPoolBinAdditionalFees` skips fee collection, so the change takes effect immediately on the next swap.

## Impact Explanation

**High.** This is a direct admin-boundary break: the pool admin exceeds the `maxAdminSpreadFeeE6` cap that the factory owner explicitly configured to constrain pool admin fee power. A pool admin can front-run any pending swap targeting a specific bin by raising the per-bin additional fee to `uint16.max` (65 535 E6), causing the swap to execute with up to 6.5535 % additional fee on top of the base spread, directly extracting user principal. The excess accrues to the pool fee balance and is collected by the admin via `collectPoolFees`. This is a quantifiable, direct loss of user principal with no recovery path, meeting the "Admin-boundary break: pool admin exceeds caps" allowed impact gate.

## Likelihood Explanation

**Low.** Exploitation requires a malicious or compromised pool admin. Pool admins are permissioned actors set at pool creation. However, once compromised, the attack is trivially executable in a single transaction with no timelock, and can be repeated per-bin and per-swap. The bypass is unconditional — it does not depend on any specific pool configuration.

## Recommendation

Add an upper-bound check in `setPoolBinAdditionalFees` mirroring the pattern in `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Optionally, introduce a dedicated `maxAdminBinAdditionalFeeE6` cap to give users a clear, auditable upper bound on per-bin surcharges separate from the aggregate spread cap.

## Proof of Concept

1. Factory owner sets `maxAdminSpreadFeeE6 = 10_000` (1 % cap on admin spread fees).
2. Pool admin calls `setPoolAdminFees(pool, 10_001, 0)` → reverts with `AdminFeeTooHigh`. Cap is enforced.
3. Pool admin instead calls `factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535)` → succeeds. No cap check.
4. Alice's pending swap through bin 0 executes with an additional 6.5535 % fee on top of the base spread, paying ~6.55 USDC extra on a 100 USDC swap.
5. Admin calls `collectPoolFees` to extract the excess, then resets the bin fee to 0.

Foundry test plan: deploy factory with `maxAdminSpreadFeeE6 = 10_000`, assert `setPoolAdminFees` reverts at 10 001, then assert `setPoolBinAdditionalFees(pool, 0, 65535, 65535)` succeeds and the bin additional fee is stored at 65 535, confirming the cap bypass.