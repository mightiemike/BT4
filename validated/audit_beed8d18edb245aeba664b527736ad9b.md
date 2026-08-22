### Title
Non-functional `assert` used to guard division-by-zero in `EnergyProcessor.calculateGlobalEnergyLimit()` energy metering - (File: `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java`)

### Summary
`EnergyProcessor.calculateGlobalEnergyLimit()`, which computes an account's global energy limit used to gate every TVM contract call's energy accounting, relies on a Java `assert` statement instead of an executable runtime check to prevent dividing by `totalEnergyWeight` when it is zero. Because JVM assertions are disabled by default (no `-ea` flag in a standard node runtime), this "guard" is a no-op, and the subsequent division is executed with an actual zero denominator whenever the legacy (non-`allowNewReward`) code path is taken. This mirrors the PirexGmx `_calculateRewards()` bug class: a reward/limit calculation copied from a "should never be zero" invariant without an enforced runtime check.

### Finding Description [1](#0-0) 

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

The only real safety check (`totalEnergyWeight <= 0 → return 0`) is gated behind `dynamicPropertiesStore.allowNewReward()`. If this hard-fork flag is not active (e.g. on a chain/testnet that has not yet enabled it, or during the historical window before it was activated on mainnet), execution falls to the `else` branch, which contains only `assert totalEnergyWeight > 0;`. Java assertions are compiled out at runtime unless the JVM is started with `-ea`, which is not the default for production node launch scripts. Consequently, if `totalEnergyWeight` is ever `0` in this code path:
- When `hardenCalculation()` is enabled, `calculateGlobalLimitV1()` performs `BigInteger.valueOf(totalLimit).divide(BigInteger.valueOf(0))`, throwing `ArithmeticException` ( [2](#0-1) ), which will propagate out of `useEnergy()`/`getAccountLeftEnergyFromFreeze()` and abort processing for any transaction that consumes energy.
- When `hardenCalculation()` is disabled, `(double) totalEnergyLimit / totalEnergyWeight` evaluates to `Infinity` (double division does not throw), and casting `Infinity` to `long` in Java yields `Long.MAX_VALUE`, silently granting an account an unbounded free energy limit — an accounting corruption rather than a crash.

This function is reached from `useEnergy()` [3](#0-2)  and from `getAccountLeftEnergyFromFreeze()` [4](#0-3) , both of which are invoked on essentially every smart-contract-triggering transaction (broadcast transactions calling contracts go through `VMActuator`/`RepositoryImpl` energy accounting, e.g. `RepositoryImpl.calculateGlobalEnergyLimit` at [5](#0-4) , which has the identical `assert totalEnergyWeight > 0;` pattern). This makes the flaw reachable from anonymous broadcast transactions with no privileged access required.

### Impact Explanation
If `totalEnergyWeight` reaches zero while the `allowNewReward()` proposal is inactive (a legitimate, non-privileged network state that can occur on any chain instance that has not activated that specific committee proposal, including private/consortium deployments and early-stage testnets running this codebase), then:
1. With hardened resource calculation enabled, every account attempting to consume energy via a contract call throws `ArithmeticException`, effectively causing a denial-of-service for all TVM contract execution on affected nodes — directly analogous to the `claimRewards()`/`harvest()` revert in the referenced report.
2. With hardened resource calculation disabled, the same condition corrupts energy accounting by granting `Long.MAX_VALUE` energy limit, allowing free/unlimited contract execution — an accounting/consensus integrity issue since nodes computing this differently (assertions enabled vs disabled) could diverge.

### Likelihood Explanation
The precondition (`totalEnergyWeight == 0` while `allowNewReward()` is false) depends on network/proposal configuration rather than attacker action, so likelihood on current TRON mainnet (where `allowNewReward` is long since activated) is low. However, the same unguarded pattern is duplicated in three separate locations (`EnergyProcessor`, `RepositoryImpl`, and the structurally identical bandwidth-side check in `BandwidthProcessor`), indicating a systemic reliance on a non-enforced `assert` as if it were a real invariant check — a code-quality/defense-in-depth flaw that is trivially triggerable on any fresh or consortium chain deployment that has not yet enabled the relevant proposal, without any special privilege.

### Recommendation
Replace the `assert totalEnergyWeight > 0;` (and the analogous checks in `RepositoryImpl.calculateGlobalEnergyLimit()` and `BandwidthProcessor`'s net-weight equivalent) with an unconditional runtime guard, e.g.:
```java
if (totalEnergyWeight <= 0) {
  return 0;
}
```
removing the dependency on `allowNewReward()` and on JVM assertion flags, matching the safe pattern already used in `calculateGlobalEnergyLimitV2()`.

### Proof of Concept
Not independently executable without control over `allowNewReward` and `totalEnergyWeight` dynamic properties on a running node; however, the code path can be exercised in a unit test by:
1. Setting `dynamicPropertiesStore.saveAllowNewReward(0)` (or ensuring it defaults to 0 on a fresh chain).
2. Setting `dynamicPropertiesStore.saveTotalEnergyWeight(0)`.
3. Ensuring `supportUnfreezeDelay()` is false (legacy freeze model) and an account has `frozeBalance >= TRX_PRECISION`.
4. Calling `EnergyProcessor.calculateGlobalEnergyLimit(account)` (or `RepositoryImpl.calculateGlobalEnergyLimit`) — this reproduces either `ArithmeticException` (hardened path) or a `Long.MAX_VALUE` result (legacy double path), as confirmed by the existing test file structure covering `calculateGlobalEnergyLimit` variants (`framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java`), none of which currently cover the `totalEnergyWeight == 0` / `allowNewReward()==false` combination.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L102-120)
```java
  public boolean useEnergy(AccountCapsule accountCapsule, long energy, long now) {

    long energyUsage = accountCapsule.getEnergyUsage();
    long latestConsumeTime = accountCapsule.getAccountResource().getLatestConsumeTimeForEnergy();
    long energyLimit = calculateGlobalEnergyLimit(accountCapsule);
    long newEnergyUsage;
    if (!dynamicPropertiesStore.supportUnfreezeDelay()) {
      newEnergyUsage = increase(energyUsage, 0, latestConsumeTime, now);
    } else {
      // only participate in the calculation as a temporary variable, without disk flushing
      newEnergyUsage = recovery(accountCapsule, ENERGY, energyUsage,
          latestConsumeTime, now);
    }

    if (energy > (energyLimit - newEnergyUsage)
        && dynamicPropertiesStore.getAllowTvmFreeze() == 0
        && !dynamicPropertiesStore.supportUnfreezeDelay()) {
      return false;
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

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L182-191)
```java
  public long getAccountLeftEnergyFromFreeze(AccountCapsule accountCapsule) {
    long now = getHeadSlot();
    long energyUsage = accountCapsule.getEnergyUsage();
    long latestConsumeTime = accountCapsule.getAccountResource().getLatestConsumeTimeForEnergy();
    long energyLimit = calculateGlobalEnergyLimit(accountCapsule);

    long newEnergyUsage = recovery(accountCapsule, ENERGY, energyUsage, latestConsumeTime, now);

    return max(energyLimit - newEnergyUsage, 0, this.disableJavaLangMath()); // us
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L350-357)
```java
  protected long calculateGlobalLimitV1(long frozeBalance,
      long totalLimit, long totalWeight) {
    long weight = frozeBalance / TRX_PRECISION;
    return BigInteger.valueOf(weight)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(totalWeight))
        .longValueExact();
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
