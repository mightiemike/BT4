### Title
Unguarded division when `TotalEnergyWeight` is zero inflates the TVM energy-freeze limit to `Long.MAX_VALUE` - (File: `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java`)

### Summary
`RepositoryImpl.calculateGlobalEnergyLimit`, the TVM/actuator-facing implementation used during contract execution, guards against `totalEnergyWeight == 0` only with a Java `assert` (compiled out in production, since java-tron does not run with `-ea`), whereas the parallel resource-accounting implementation in `chainbase`'s `EnergyProcessor.calculateGlobalEnergyLimit` explicitly checks `totalEnergyWeight <= 0` and returns `0`. This is the same bug class as the reported Aave issue: a global scaling/weight parameter reaching `0` is not defended against at the point where it is used as a divisor to compute a "limit," so the limit calculation silently produces an enormous, meaningless value instead of failing safe.

### Finding Description
`RepositoryImpl.calculateGlobalEnergyLimit`: [1](#0-0) 

only protects the divide with `assert totalEnergyWeight > 0;` before executing `(long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight))` in the non-hardened path. In production JVMs assertions are disabled by default, so if `totalEnergyWeight` is `0`, `(double) totalEnergyLimit / totalEnergyWeight` evaluates to `Infinity`, and casting a positive-infinite double to `long` in Java yields `Long.MAX_VALUE` — an effectively unbounded energy limit — instead of throwing or returning a sane default.

Contrast this with the sibling implementation in `chainbase`'s `EnergyProcessor`, which the core team already hardened for exactly this state: [2](#0-1) 

Here `if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) { return 0; }` is present precisely because `totalEnergyWeight <= 0` is a recognized reachable state (this parameter is a chain-wide aggregate recalculated over time, not something a single user's frozen balance alone guarantees to be positive). The TVM-facing `RepositoryImpl` copy of this logic never received the same fix — only a no-op `assert`.

This value flows into `RepositoryImpl.getAccountLeftEnergyFromFreeze`, used to compute how much energy a contract creator/caller can draw from frozen TRX during contract execution: [3](#0-2) 

which is itself the basis for `VMActuator.getTotalEnergyLimitWithFixRatio`'s `originEnergyLeft`/`creatorEnergyLimit` computation used whenever a contract has `consumeUserResourcePercent < 100`: [4](#0-3) 

The project's own hardened-mode test suite confirms the reviewers were aware that this divide can misbehave/throw when denominators are pathological (`ArithmeticException` on `BigInteger` divide-by-zero once hardening is enabled): [5](#0-4) 

but no equivalent test exercises `totalEnergyWeight == 0` for the non-hardened `RepositoryImpl.calculateGlobalEnergyLimit` path, so the `Infinity → Long.MAX_VALUE` degenerate case is untested and unguarded in the currently-default (non-hardened) code path.

### Impact Explanation
If `totalEnergyWeight` reaches `0` while `allowHardenResourceCalculation` is not enabled, any contract creator's "left energy from freeze" becomes `Long.MAX_VALUE`. Through `VMActuator.getTotalEnergyLimitWithFixRatio`, this inflates `creatorEnergyLimit` for any contract with `consumeUserResourcePercent < 100`, letting callers execute essentially unbounded energy against the creator's (near-zero) frozen stake — a resource-accounting/state-divergence bug directly analogous to the reported Aave issue where a zero-valued scaling parameter caused the borrow limit to appear unbounded. If hardening is enabled instead, the same zero-weight condition throws `ArithmeticException` deep in transaction/energy accounting, i.e., a DoS on transaction processing for affected contract calls — mirroring the "DoS" half of the original report as well.

### Likelihood Explanation
This does not require a trusted role to exploit directly — an ordinary contract caller triggers the vulnerable code path simply by invoking any smart contract whose creator has frozen energy and `consumeUserResourcePercent < 100`, whenever the chain-wide `TotalEnergyWeight` aggregate is `0`. Whether `TotalEnergyWeight` can reach exactly `0` in a live, fully-migrated (FreezeV2) network depends on chain-wide state/migration history that I could not fully trace within available tool budget; the `chainbase` team's own explicit `<= 0` guard is nonetheless strong evidence that this is a state they consider reachable in practice. This uncertainty is the main open item for a background agent to confirm before treating the impact as production-exploitable versus a defense-in-depth gap.

### Recommendation
Replace the `assert totalEnergyWeight > 0;` in `RepositoryImpl.calculateGlobalEnergyLimit` with an explicit runtime check mirroring `EnergyProcessor.calculateGlobalEnergyLimit` (`if (totalEnergyWeight <= 0) return 0;`), and add a unit test analogous to `CalculateGlobalLimitHardenTest.testGlobalEnergyLimitOverflowDetectedWithHardening` but for the non-hardened path with `totalEnergyWeight = 0`, asserting the result is `0` (or otherwise bounded) rather than `Long.MAX_VALUE`.

### Proof of Concept
1. Force chain state so `DynamicPropertiesStore.getTotalEnergyWeight() == 0` (e.g., via the maintenance-cycle recalculation path that also motivated the `allowNewReward()` guard in `EnergyProcessor`).
2. Have a contract creator with `frozeBalance >= TRX_PRECISION` for energy and a deployed contract with `consumeUserResourcePercent < 100`.
3. Call `RepositoryImpl.calculateGlobalEnergyLimit(creator)`; with hardening disabled, `(double) totalEnergyLimit / 0` → `Infinity`, and `(long) (energyWeight * Infinity)` → `Long.MAX_VALUE`.
4. This propagates through `getAccountLeftEnergyFromFreeze` → `VMActuator.getTotalEnergyLimitWithFixRatio`'s `creatorEnergyLimit`, producing an effectively unbounded energy allotment attributed to the creator's frozen balance for any contract invocation, as shown in: [6](#0-5)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L184-197)
```java
  @Override
  public long getAccountLeftEnergyFromFreeze(AccountCapsule accountCapsule) {
    long now = getHeadSlot();

    long energyUsage = accountCapsule.getEnergyUsage();
    long latestConsumeTime = accountCapsule.getAccountResource().getLatestConsumeTimeForEnergy();
    long energyLimit = calculateGlobalEnergyLimit(accountCapsule);

    long windowSize = accountCapsule.getWindowSize(Common.ResourceCode.ENERGY);

    long newEnergyUsage = recover(energyUsage, latestConsumeTime, now, windowSize);

    return max(energyLimit - newEnergyUsage, 0, VMConfig.disableJavaLangMath()); // us
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

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L145-166)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L735-757)
```java
    long originEnergyLeft = 0;
    if (consumeUserResourcePercent < VMConstant.ONE_HUNDRED) {
      originEnergyLeft = rootRepository.getAccountLeftEnergyFromFreeze(creator);
      if (VMConfig.allowTvmFreeze() || VMConfig.allowTvmFreezeV2()) {
        receipt.setOriginEnergyLeft(originEnergyLeft);
      }
    }
    if (consumeUserResourcePercent <= 0) {
      creatorEnergyLimit = min(originEnergyLeft, originEnergyLimit,
          VMConfig.disableJavaLangMath());
    } else {
      if (consumeUserResourcePercent < VMConstant.ONE_HUNDRED) {
        // creatorEnergyLimit =
        // min(callerEnergyLimit * (100 - percent) / percent,
        //   creatorLeftFrozenEnergy, originEnergyLimit)

        creatorEnergyLimit = min(
            BigInteger.valueOf(callerEnergyLimit)
                .multiply(BigInteger.valueOf(VMConstant.ONE_HUNDRED - consumeUserResourcePercent))
                .divide(BigInteger.valueOf(consumeUserResourcePercent)).longValueExact(),
            min(originEnergyLeft, originEnergyLimit, VMConfig.disableJavaLangMath()),
            VMConfig.disableJavaLangMath());
      }
```

**File:** framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java (L67-78)
```java
  @Test
  public void testGlobalEnergyLimitOverflowDetectedWithHardening() {
    dbManager.getDynamicPropertiesStore().saveTotalEnergyCurrentLimit(Long.MAX_VALUE / 2);
    dbManager.getDynamicPropertiesStore().saveTotalEnergyWeight(1L);
    ownerCapsule.setFrozenForEnergy(Long.MAX_VALUE / 4, 0L);
    dbManager.getAccountStore().put(ownerCapsule.getAddress().toByteArray(), ownerCapsule);

    dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(1);

    Assert.assertThrows(ArithmeticException.class,
        () -> energyProcessor.calculateGlobalEnergyLimit(ownerCapsule));
  }
```
