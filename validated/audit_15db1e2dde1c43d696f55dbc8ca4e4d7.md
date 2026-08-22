### Title
Silent long-multiplication overflow in default (non-hardened) resource/reward accounting math mirrors the ComplexRewarder fixed-width truncation bug - ([File: chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java])

### Summary
The audited Solidity bug is a class of "insufficient bit-width for an intermediate product" defect: `reward * ACC_TOKEN_PRECISION` can silently overflow a 128-bit accumulator when `reward` is large and `lpSupply` is tiny, corrupting `accRewardPerShare`. java-tron contains the same bug class in its resource/energy/bandwidth accounting math, which directly determines free-transaction "reward"/allowance a user is entitled to per cycle. The code base itself acknowledges this: it has an explicit, opt-in "hardened" path (`hardenCalculation()` / `hardenResourceCalculation()`) that replaces raw `long * long` arithmetic with `BigInteger` math specifically to avoid silent overflow, but the legacy, non-hardened arithmetic remains the default behavior gated behind a governance-controlled chain parameter (`getAllowHardenResourceCalculation()` / VM config `allowHardenResourceCalculation()`), and is exercised on every ordinary bandwidth/energy consuming transaction.

### Finding Description
In `ResourceProcessor.increase()`, `getUsage()`, and `RepositoryImpl.calculateGlobalEnergyLimit()` / `increase()`, the legacy branch performs plain 64-bit `long` multiplication with no overflow check: [1](#0-0) [2](#0-1) [3](#0-2) 

These same functions have hardened, `BigInteger`-based twins guarded by a feature flag: [4](#0-3) [5](#0-4) 

Test code explicitly documents and reproduces the exact failure mode described in the report — silent, wrap-around corruption of the accounting value when the hardened path is disabled: [6](#0-5) [7](#0-6) 

This is functionally identical to the reported defect: a fixed-width integer product (`usage * precision`, `lastUsage * precision`, `energyWeight * totalEnergyLimit` when not hardened) that is not guaranteed to fit in the container type, silently truncating instead of throwing, and thereby corrupting a per-account resource/reward accounting value analogous to `accRewardPerShare`.

A related, non-overflow-but-precision-loss instance of the same bug class also exists in reward distribution using `double` arithmetic instead of exact integer/BigInteger math: [8](#0-7) [9](#0-8) 

### Impact Explanation
Corrupted energy/bandwidth accounting values directly control how much free bandwidth/energy an account is granted vs. how much TRX must be burned as a fee, and control delegated-resource/vote-reward bookkeeping. A wrap-around in `usage * precision` (or similar) could let an attacker who can influence `usage`, `lastUsage`, or frozen-balance-derived weights (all attacker-controlled via ordinary freeze/vote/contract-call transactions) drive the computed usage or reward value far below (or above) the correct value, effectively obtaining free resources beyond entitlement or corrupting the global adaptive resource limit / reward allocation — a resource-accounting corruption reachable from ordinary, unprivileged broadcast transactions (freezing balance, voting, triggering contracts that consume energy).

### Likelihood Explanation
The vulnerable (legacy) arithmetic is the **default** code path — the hardened `BigInteger` path only activates when the chain parameter `getAllowHardenResourceCalculation()` is turned on via committee proposal (`ProposalUtil`/`ProposalService`), consistent with how such feature flags are exposed in `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java`. Reaching values large enough to overflow a signed 64-bit long product requires either very large windowSize/precision constants or attacker-influenced `usage`/`lastUsage` reaching magnitudes near `Long.MAX_VALUE / precision`; the project's own test suite (`testIncreaseOverflowSilentWithoutHardening`, `testUpdateAdaptiveLimitMultiplierOverflowDetected`) demonstrates these overflow conditions are achievable and are only caught when hardening is explicitly enabled. On networks/configurations where the hardening proposal has not been passed, the exposure is real but requires reaching extreme value ranges, so likelihood is moderate rather than trivially triggerable by any small transaction.

### Recommendation
Make the `BigInteger`/overflow-checked (`hardenCalculation`/`hardenResourceCalculation`) arithmetic the unconditional default rather than an opt-in governance flag, removing the legacy `long * long` and `double`-based multiplication paths in `ResourceProcessor`, `RepositoryImpl`, `MortgageService.payStandbyWitness`, and `IncentiveManager.reward`. This mirrors the report's accepted recommendation of widening the integer type used for the intermediate product (here, replacing raw `long` products with `BigInteger`/`Math.multiplyExact` computations network-wide, not just when a proposal is activated).

### Proof of Concept
The project's own regression tests constitute a working PoC of the silent-overflow condition when hardening is disabled: [10](#0-9) 
With `lastUsage = Long.MAX_VALUE / 10` and hardening disabled, `invokeIncrease(...)` completes without error yet the internal `lastUsage * precision` product wraps, as shown by the companion hardened test throwing `ArithmeticException` for the same inputs — directly analogous to the reported Solidity overflow of `reward * ACC_TOKEN_PRECISION`.

**Note on limitations:** I was unable to fully confirm the on-chain default value of the `ALLOW_HARDEN_RESOURCE_CALCULATION` dynamic property (i.e., whether mainnet has already activated the hardened path via a passed proposal) because the relevant getter/constant definitions in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` were not retrievable through the index in this session. If mainnet already has this proposal permanently activated, the practical exploitability of this specific instance is reduced to legacy/pre-activation state only; a Devin session with full file access would be needed to verify the current default and activation status precisely.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L50-63)
```java
  protected long increase(long lastUsage, long usage, long lastTime, long now, long windowSize) {
    long averageLastUsage;
    long averageUsage;
    if (hardenCalculation()) {
      BigInteger biPrecision = BigInteger.valueOf(precision);
      BigInteger biWindowSize = BigInteger.valueOf(windowSize);
      averageLastUsage = divideCeilExact(
          BigInteger.valueOf(lastUsage).multiply(biPrecision), biWindowSize);
      averageUsage = divideCeilExact(
          BigInteger.valueOf(usage).multiply(biPrecision), biWindowSize);
    } else {
      averageLastUsage = divideCeil(lastUsage * precision, windowSize);
      averageUsage = divideCeil(usage * precision, windowSize);
    }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L285-291)
```java
  private long getUsage(long usage, long windowSize) {
    if (hardenCalculation()) {
      return BigInteger.valueOf(usage).multiply(BigInteger.valueOf(windowSize))
          .divide(BigInteger.valueOf(precision)).longValueExact();
    }
    return usage * windowSize / precision;
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L359-378)
```java
  /**
   * Hardened replacement of legacy V2 formula
   * {@code (long)(((double) frozeBalance / TRX_PRECISION)
   *               * ((double) totalLimit / totalWeight))}.
   *
   * <p>Preserves V2 semantics: equivalent to
   * {@code (frozeBalance * totalLimit) / (TRX_PRECISION * totalWeight)} with
   * a single integer truncation at the end. Critically, fractional weight
   * (i.e. {@code frozeBalance < TRX_PRECISION}) is preserved through the
   * multiplication and only truncated at the final divide, so small balances
   * yield the same proportional result as the double-arithmetic path.
   */
  protected long calculateGlobalLimitV2(long frozeBalance,
      long totalLimit, long totalWeight) {
    return BigInteger.valueOf(frozeBalance)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(TRX_PRECISION)
            .multiply(BigInteger.valueOf(totalWeight)))
        .longValueExact();
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L911-924)
```java
  private long increase(long lastUsage, long usage, long lastTime, long now, long windowSize) {
    long averageLastUsage;
    long averageUsage;
    if (hardenResourceCalculation()) {
      BigInteger biPrecision = BigInteger.valueOf(precision);
      BigInteger biWindowSize = BigInteger.valueOf(windowSize);
      averageLastUsage = divideCeilExact(
          BigInteger.valueOf(lastUsage).multiply(biPrecision), biWindowSize);
      averageUsage = divideCeilExact(
          BigInteger.valueOf(usage).multiply(biPrecision), biWindowSize);
    } else {
      averageLastUsage = divideCeil(lastUsage * precision, windowSize);
      averageUsage = divideCeil(usage * precision, windowSize);
    }
```

**File:** framework/src/test/java/org/tron/core/vm/repository/RepositoryImplHardenTest.java (L122-146)
```java
  @Test
  public void testIncreaseOverflowDetectedWithHardening() {
    long lastUsage = Long.MAX_VALUE / 10; // ~9.2e17
    long usage = 1L;
    long lastTime = 9990L;
    long now = 9995L;
    long windowSize = 28800L;

    VMConfig.initAllowHardenResourceCalculation(1);

    Assert.assertThrows(ArithmeticException.class,
        () -> invokeIncrease(lastUsage, usage, lastTime, now, windowSize));
  }

  @Test
  public void testIncreaseOverflowSilentWithoutHardening() throws Exception {
    long lastUsage = Long.MAX_VALUE / 10;
    long usage = 1L;
    long lastTime = 9990L;
    long now = 9995L;
    long windowSize = 28800L;

    VMConfig.initAllowHardenResourceCalculation(0);
    invokeIncrease(lastUsage, usage, lastTime, now, windowSize);
  }
```

**File:** framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java (L334-345)
```java
  @Test
  public void testUpdateAdaptiveLimitMultiplierOverflowDetected() {
    dbManager.getDynamicPropertiesStore().saveTotalEnergyAverageUsage(0L);
    dbManager.getDynamicPropertiesStore().saveTotalEnergyTargetLimit(Long.MAX_VALUE);
    dbManager.getDynamicPropertiesStore().saveTotalEnergyCurrentLimit(1_000_000L);
    dbManager.getDynamicPropertiesStore().saveTotalEnergyLimit(Long.MAX_VALUE / 100);
    dbManager.getDynamicPropertiesStore().saveAdaptiveResourceLimitMultiplier(1000L);
    dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(1);

    Assert.assertThrows(ArithmeticException.class,
        () -> energyProcessor.updateAdaptiveTotalEnergyLimit());
  }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L53-67)
```java
  public void payStandbyWitness() {
    List<WitnessCapsule> witnessStandbys = witnessStore.getWitnessStandby(
        dynamicPropertiesStore.allowWitnessSortOptimization());
    long voteSum = witnessStandbys.stream().mapToLong(WitnessCapsule::getVoteCount).sum();
    if (voteSum < 1) {
      return;
    }
    long totalPay = dynamicPropertiesStore.getWitness127PayPerBlock();
    double eachVotePay = (double) totalPay / voteSum;
    for (WitnessCapsule w : witnessStandbys) {
      long pay = (long) (w.getVoteCount() * eachVotePay);
      payReward(w.getAddress().toByteArray(), pay);
      logger.debug("Pay {} stand reward {}.", Hex.toHexString(w.getAddress().toByteArray()), pay);
    }
  }
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java (L34-42)
```java
    long totalPay = consensusDelegate.getWitnessStandbyAllowance();
    for (ByteString witness : witnesses) {
      byte[] address = witness.toByteArray();
      long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay
          / voteSum));
      AccountCapsule accountCapsule = consensusDelegate.getAccount(address);
      accountCapsule.setAllowance(accountCapsule.getAllowance() + pay);
      consensusDelegate.saveAccount(accountCapsule);
    }
```
