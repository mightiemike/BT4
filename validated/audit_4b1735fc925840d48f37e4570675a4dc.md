Audit Report

## Title
Pool admin bypasses `maxAdminSpreadFeeE6` cap via `setPoolBinAdditionalFees` — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary

`MetricOmmPoolFactory.setPoolBinAdditionalFees` forwards raw `uint16` per-bin fee values directly to the pool with no upper-bound validation against `maxAdminSpreadFeeE6`. When the factory owner lowers `maxAdminSpreadFeeE6` below 65,535 (e.g., to 1,000 = 0.1%), a pool admin can still set per-bin additional fees up to `type(uint16).max` (65,535 E6 = 6.5535%) via this function, directly bypassing the configured cap and extracting excess fees from traders.

## Finding Description

`MetricOmmPoolFactory` enforces a layered fee-cap system. The factory owner sets `maxAdminSpreadFeeE6` via `setFeeCaps`, bounded by `HARD_MAX_SPREAD_FEE_E6 = 200_000`. [1](#0-0) 

`setPoolAdminFees` enforces this cap before updating pool-level fees: [2](#0-1) 

However, the sibling function `setPoolBinAdditionalFees` performs no such check and forwards values directly to the pool: [3](#0-2) 

The pool's `setBinAdditionalFees` implementation also contains no cap validation — it only checks the bin index range and stores the values: [4](#0-3) 

The `BinState` struct stores `addFeeBuyE6` and `addFeeSellE6` as `uint16`, whose maximum value is 65,535 (6.5535% in E6 units): [5](#0-4) 

The bypass is active whenever the factory owner has lowered `maxAdminSpreadFeeE6` below 65,535. For example, if the owner sets `maxAdminSpreadFeeE6 = 1_000` (0.1%), `setPoolAdminFees` is capped at 0.1%, but `setPoolBinAdditionalFees` still accepts up to 65,535 (6.5535%) with no revert. Additionally, unlike `setPoolAdminFees` which calls `collectFees` before updating to settle accrued fees at the old rate, `setPoolBinAdditionalFees` skips fee collection entirely, meaning the fee change takes effect immediately on the next swap. [6](#0-5) 

## Impact Explanation

**High.** A pool admin can set per-bin additional fees above the configured `maxAdminSpreadFeeE6` cap, directly extracting excess value from traders' principal on any swap routed through the affected bin. The excess fee accrues to the pool's fee balance and is collected by the admin via `collectPoolFees`. This is a direct, quantifiable loss of user principal that bypasses the admin-boundary cap system the protocol explicitly enforces for all other admin fee setters. This satisfies the "Admin-boundary break: pool admin exceeds caps" allowed impact.

## Likelihood Explanation

**Low.** Exploitation requires a malicious or compromised pool admin. Pool admins are permissioned actors known at pool creation time. However, once compromised, the attack requires only a single transaction with no timelock, and the bypass is trivially executable. The cap bypass is only meaningful when the factory owner has lowered `maxAdminSpreadFeeE6` below 65,535 — a configuration that is explicitly supported and expected by the cap system.

## Recommendation

Add an upper-bound check in `setPoolBinAdditionalFees` mirroring the pattern used in `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Optionally, introduce a dedicated `maxAdminBinAdditionalFeeE6` cap so users have a clear, auditable upper bound on per-bin surcharges separate from the aggregate spread cap.

## Proof of Concept

1. Factory owner calls `setFeeCaps(200_000, 1_000, 1_000_000, 1_000_000)` to lower `maxAdminSpreadFeeE6` to 1,000 (0.1%).
2. Pool admin calls `setPoolAdminFees(pool, 2_000, 0)` — this reverts with `AdminFeeTooHigh` as expected.
3. Pool admin calls `setPoolBinAdditionalFees(pool, 0, 65535, 65535)` — this succeeds with no revert, setting a 6.5535% additional fee on bin 0, far exceeding the 0.1% cap.
4. A trader's swap routed through bin 0 pays 6.5535% additional fee on top of the base spread fee.
5. Admin calls `collectPoolFees` to extract the excess fee balance.

The existing test `test_setPoolBinAdditionalFees_updatesStorage_emitsEvent` confirms the function accepts arbitrary `uint16` values without any cap check: [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L44-44)
```text
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L414-415)
```text
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L417-425)
```text
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
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

**File:** metric-core/contracts/types/PoolStorage.sol (L19-25)
```text
struct BinState {
  uint104 token0BalanceScaled;
  uint104 token1BalanceScaled;
  uint16 lengthE6;
  uint16 addFeeBuyE6;
  uint16 addFeeSellE6;
}
```

**File:** metric-core/test/MetricOmmPoolFactory.t.sol (L375-400)
```text
  function test_setPoolBinAdditionalFees_updatesStorage_emitsEvent() public {
    address pool = _createPool();
    (,,, uint16 buy0Before, uint16 sell0Before) = PoolStateLibrary._binState(pool, 0);
    assertEq(buy0Before, 0);
    assertEq(sell0Before, 0);

    vm.expectEmit(true, false, false, true, pool);
    emit IMetricOmmPoolFactoryActions.BinAdditionalFeesUpdated(int8(0), uint16(500), uint16(700));

    vm.prank(admin);
    factory.setPoolBinAdditionalFees(pool, 0, 500, 700);

    (,,, uint16 buy0After, uint16 sell0After) = PoolStateLibrary._binState(pool, 0);
    assertEq(buy0After, 500);
    assertEq(sell0After, 700);

    (,,, uint16 buyNeg, uint16 sellNeg) = PoolStateLibrary._binState(pool, -1);
    assertEq(buyNeg, 0);
    assertEq(sellNeg, 0);

    vm.prank(admin);
    factory.setPoolBinAdditionalFees(pool, -1, 10, 20);
    (,,, buyNeg, sellNeg) = PoolStateLibrary._binState(pool, -1);
    assertEq(buyNeg, 10);
    assertEq(sellNeg, 20);
  }
```
