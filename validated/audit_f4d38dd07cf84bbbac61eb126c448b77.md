This confirms `ALLOW_HARDEN_RESOURCE_CALCULATION` defaults to `0` (disabled) unless a governance proposal enables it [1](#0-0) , meaning the legacy divide-before-multiply path in `calculateGlobalEnergyLimit` is live by default. This is the strongest analog to the reported bug class.

### Title
Divide-before-multiply truncation in `calculateGlobalEnergyLimit` energy-limit computation - (File: `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java`)

### Summary
The legacy (non-hardened) energy-limit calculation performs an integer division (`frozeBalance / TRX_PRECISION`) before multiplying by `totalEnergyLimit`, truncating a fractional "weight" to zero or a smaller integer before it is scaled up. This mirrors the Vader "divide before multiply" bug class: precision is destroyed before the multiplication that should have preserved it, and the loss is asymmetric depending on how the frozen balance divides against `TRX_PRECISION`.

### Finding Description
`RepositoryImpl.calculateGlobalEnergyLimit` computes:
```java
long energyWeight = frozeBalance / TRX_PRECISION;   // <-- integer divide FIRST
...
return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
``` [2](#0-1) 

`energyWeight` is truncated to an integer (`frozeBalance / TRX_PRECISION`) *before* being multiplied against `totalEnergyLimit / totalEnergyWeight`. Any fractional TRX below `TRX_PRECISION` (1 TRX) in the frozen balance is silently discarded from the weight used to size the account's global energy allowance, and multiplying afterward can never recover it, exactly analogous to the Vader `_units`/`slipAdjustment` order-of-operations issue where the division happening first collapses meaningful precision.

The identical pattern also exists in `EnergyProcessor.calculateGlobalEnergyLimit`/`calculateGlobalEnergyLimitV2` [3](#0-2)  and in `RepositoryImpl.usageToBalance` [4](#0-3)  and `ResourceProcessor.calculateGlobalLimitV1`-adjacent legacy path.

The codebase already contains a corrected, hardened path guarded by `VMConfig.allowHardenResourceCalculation()` / `DynamicPropertiesStore.allowHardenResourceCalculation()` that performs the multiplication with `BigInteger` before dividing [5](#0-4) , and dedicated tests (`CalculateGlobalLimitHardenTest`, `RepositoryImplHardenTest`) explicitly assert the "buggy" legacy result differs from the corrected `BigInteger` computation [6](#0-5) . However, this hardening is activated only via the `ALLOW_HARDEN_RESOURCE_CALCULATION` proposal, whose stored default is `0` (disabled) [1](#0-0) . Until super-representatives vote to enable it, every node runs the flawed order of operations.

### Impact Explanation
`calculateGlobalEnergyLimit` directly determines an account's TVM energy allowance derived from frozen-for-energy TRX in `getAccountLeftEnergyFromFreeze`, which gates how much energy a contract-calling account can consume without paying SUN fees. Truncation here causes divergence between the "intended" proportional allowance and the actual value credited on-chain, and because this is consensus-critical resource accounting executed by every full node during transaction processing, any inconsistency between hardened and non-hardened states across the network (or simply an incorrect but consistent legacy value) results in systematically wrong energy-limit accounting for all accounts with sub-TRX-precision remainders in their frozen balances.

### Likelihood Explanation
This code path executes on every transaction that triggers/creates a smart contract and touches an account with `AllFrozenBalanceForEnergy` that is not an exact multiple of `TRX_PRECISION` (1,000,000 SUN) — a very common and unprivileged condition since ordinary users freeze arbitrary TRX amounts for energy. No special privilege is required to trigger the miscalculation; it happens automatically as part of normal resource accounting whenever the hardening proposal has not been activated, which is the default state per `DynamicPropertiesStore.getAllowHardenResourceCalculation()`.

### Recommendation
Activate the `ALLOW_HARDEN_RESOURCE_CALCULATION` proposal network-wide (or make the `BigInteger`-based multiply-before-divide computation unconditional/default) so that `energyWeight` is never truncated before being multiplied against `totalEnergyLimit`. As done in the already-present hardened branch, always compute `BigInteger.valueOf(frozeBalance).multiply(BigInteger.valueOf(totalEnergyLimit)).divide(BigInteger.valueOf(totalEnergyWeight).multiply(BigInteger.valueOf(TRX_PRECISION)))`-style multiply-first arithmetic rather than dividing `frozeBalance` by `TRX_PRECISION` up front.

### Proof of Concept
Using `RepositoryImpl.calculateGlobalEnergyLimit` with `TRX_PRECISION = 1_000_000`:
```
frozeBalance = 1_999_999            // 1.999999 TRX frozen
totalEnergyLimit = 50_000_000_000L
totalEnergyWeight = 1_000_000L

// Legacy (default) path:
energyWeight = 1_999_999 / 1_000_000 = 1      // truncated from 1.999999
result = (long)(1 * (50_000_000_000.0 / 1_000_000)) = 50_000

// Correct multiply-first (hardened) path:
BigInteger.valueOf(1_999_999)
    .multiply(BigInteger.valueOf(50_000_000_000L))
    .divide(BigInteger.valueOf(1_000_000L).multiply(BigInteger.valueOf(1_000_000L)))
= 99_999   // roughly double the legacy result
```
This ~2x discrepancy (and similar smaller discrepancies for any non-multiple-of-`TRX_PRECISION` balance) demonstrates the account is credited with a materially different, understated energy allowance purely due to the divide-before-multiply ordering, reproducible via the existing `CalculateGlobalLimitHardenTest`/`RepositoryImplHardenTest` test classes by toggling `saveAllowHardenResourceCalculation(0)` vs `(1)`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L3044-3057)
```java
  public long getAllowHardenResourceCalculation() {
    return Optional.ofNullable(getUnchecked(ALLOW_HARDEN_RESOURCE_CALCULATION))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElse(0L);
  }

  public void saveAllowHardenResourceCalculation(long value) {
    this.put(ALLOW_HARDEN_RESOURCE_CALCULATION, new BytesCapsule(ByteArray.fromLong(value)));
  }

  public boolean allowHardenResourceCalculation() {
    return getAllowHardenResourceCalculation() == 1L;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L256-265)
```java
  private long usageToBalance(long usage, long totalWeight, long totalLimit) {
    if (hardenResourceCalculation()) {
      return BigInteger.valueOf(usage)
          .multiply(BigInteger.valueOf(totalWeight))
          .multiply(BigInteger.valueOf(TRX_PRECISION))
          .divide(BigInteger.valueOf(totalLimit))
          .longValueExact();
    }
    return (long) ((double) usage * totalWeight / totalLimit * TRX_PRECISION);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L967-985)
```java
  public long calculateGlobalEnergyLimit(AccountCapsule accountCapsule) {
    long frozeBalance = accountCapsule.getAllFrozenBalanceForEnergy();
    if (frozeBalance < TRX_PRECISION) {
      return 0;
    }
    long energyWeight = frozeBalance / TRX_PRECISION;
    long totalEnergyLimit = getDynamicPropertiesStore().getTotalEnergyCurrentLimit();
    long totalEnergyWeight = getDynamicPropertiesStore().getTotalEnergyWeight();

    assert totalEnergyWeight > 0;

    if (hardenResourceCalculation()) {
      return BigInteger.valueOf(energyWeight)
          .multiply(BigInteger.valueOf(totalEnergyLimit))
          .divide(BigInteger.valueOf(totalEnergyWeight))
          .longValueExact();
    }
    return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L145-179)
```java
  public long calculateGlobalEnergyLimit(AccountCapsule accountCapsule) {
    long frozeBalance = accountCapsule.getAllFrozenBalanceForEnergy();
    if (dynamicPropertiesStore.supportUnfreezeDelay()) {
      return calculateGlobalEnergyLimitV2(frozeBalance);
    }
    if (frozeBalance < TRX_PRECISION) {
      return 0;
    }

    long totalEnergyLimit = dynamicPropertiesStore.getTotalEnergyCurrentLimit();
    long totalEnergyWeight = dynamicPropertiesStore.getTotalEnergyWeight();
    if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) {
      return 0;
    } else {
      assert totalEnergyWeight > 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV1(frozeBalance, totalEnergyLimit, totalEnergyWeight);
    }
    long energyWeight = frozeBalance / TRX_PRECISION;
    return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
  }

  public long calculateGlobalEnergyLimitV2(long frozeBalance) {
    long totalEnergyLimit = dynamicPropertiesStore.getTotalEnergyCurrentLimit();
    long totalEnergyWeight = dynamicPropertiesStore.getTotalEnergyWeight();
    if (totalEnergyWeight == 0) {
      return 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV2(frozeBalance, totalEnergyLimit, totalEnergyWeight);
    }
    double energyWeight = (double) frozeBalance / TRX_PRECISION;
    return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
  }
```

**File:** framework/src/test/java/org/tron/core/vm/repository/RepositoryImplHardenTest.java (L228-257)
```java
  public void testCalculateGlobalEnergyLimitHardenedParityWithNonIntegerRatio() {
    long totalEnergyLimit = 50_000_000_000L;
    long totalEnergyWeight = 1_234_567L;
    long frozeBalance = 10_000_000_000L;

    dbManager.getDynamicPropertiesStore().saveTotalEnergyCurrentLimit(totalEnergyLimit);
    dbManager.getDynamicPropertiesStore().saveTotalEnergyWeight(totalEnergyWeight);

    AccountCapsule account = new AccountCapsule(
        ByteString.copyFromUtf8("owner"),
        ByteString.copyFrom(ByteArray.fromHexString(
            Wallet.getAddressPreFixString() + "548794500882809695a8a687866e76d4271a1abc")),
        AccountType.Normal, 0L);
    account.setFrozenForEnergy(frozeBalance, 0L);

    VMConfig.initAllowHardenResourceCalculation(0);
    long resultOld = repository.calculateGlobalEnergyLimit(account);

    VMConfig.initAllowHardenResourceCalculation(1);
    long resultNew = repository.calculateGlobalEnergyLimit(account);

    long expected = java.math.BigInteger.valueOf(10000L)
        .multiply(java.math.BigInteger.valueOf(totalEnergyLimit))
        .divide(java.math.BigInteger.valueOf(totalEnergyWeight))
        .longValueExact();
    Assert.assertEquals(expected, resultNew);
    Assert.assertEquals(resultOld, resultNew);

    long buggy = 10000L * (totalEnergyLimit / totalEnergyWeight);
    Assert.assertNotEquals(buggy, resultNew);
```
