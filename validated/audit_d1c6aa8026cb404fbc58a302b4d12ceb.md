### Title
Committee-controlled `allowHardenResourceCalculation` path can throw an uncaught `ArithmeticException` from resource-limit math, bricking an account's/​block's resource accounting - ([File: chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java])

### Summary
The Sablier report describes a debt-accounting function that multiplies two attacker-influenceable values (`elapsedTime * ratePerSecond`) into a fixed-width type, overflows, and reverts every downstream call that depends on it — permanently bricking withdraw/refund/adjust for the stream. The analogous pattern in java-tron is the "hardened" bandwidth/energy limit math: `calculateGlobalLimitV1`/`calculateGlobalLimitV2` compute `frozenBalance * totalLimit / totalWeight` using `BigInteger` and finish with `.longValueExact()`, which throws `ArithmeticException` if the true mathematical result does not fit in a `long`.

### Finding Description
`ResourceProcessor.calculateGlobalLimitV1` and `calculateGlobalLimitV2` [1](#0-0)  use `BigInteger(...).longValueExact()` to compute an account's global energy/bandwidth limit from its frozen balance. This path is only taken when the committee-controlled parameter `allowHardenResourceCalculation` is enabled [2](#0-1) , gated via the `HARDEN_RESOURCE_CALCULATION` proposal type in `ProposalUtil`. `EnergyProcessor.calculateGlobalEnergyLimit` and `BandwidthProcessor.calculateGlobalNetLimit`/`calculateGlobalNetLimitV2` call these helpers directly [3](#0-2) , and the equivalent logic is duplicated in the TVM's `RepositoryImpl` for contract-triggered resource checks [4](#0-3) .

Crucially, these limit functions feed directly into `EnergyProcessor.useEnergy` [5](#0-4)  and `BandwidthProcessor.useAccountNet`/`useAssetAccountNet`, which are called unconditionally from `BandwidthProcessor.consume()` during ordinary transaction processing/block validation [6](#0-5) . None of these call sites catch `ArithmeticException`. Project-added tests explicitly confirm that when hardening is enabled, sufficiently large `frozenBalance`/`totalLimit`/`totalWeight` combinations throw `ArithmeticException` from `calculateGlobalEnergyLimit`/`calculateGlobalNetLimit` [7](#0-6) [8](#0-7) , and from the TVM `RepositoryImpl` counterpart [9](#0-8) . `ResourceProcessor.increase()` (the usage-recovery/decay function invoked on essentially every bandwidth/energy update) has the same `longValueExact()` overflow-to-exception behavior when hardening is on [10](#0-9) , confirmed by `ResourceProcessorHardenTest.testIncreaseOverflowDetectedWithHardening` [11](#0-10) .

An unprivileged account controls its own `frozenBalance` (via freeze-for-energy/bandwidth), which is one operand of the overflow-prone multiplication; the other operands (`totalEnergyLimit`/`totalNetLimit`, `totalEnergyWeight`/`totalNetWeight`) are network-wide values that fluctuate with aggregate freezing activity across all accounts. If a large enough frozen balance (relative to current total-limit/total-weight ratio) is staked, `frozenBalance * totalLimit` can exceed `Long.MAX_VALUE`, and `longValueExact()` throws — exactly mirroring the Sablier root cause (unchecked/overflow-prone multiplication feeding a value that later hard-fails all dependent operations) but manifesting in Java as an uncaught runtime exception rather than a Solidity revert.

### Impact Explanation
If this uncaught `ArithmeticException` propagates out of `consume()`/`useEnergy()` during block application, it would not be handled as a normal validation failure (like `ContractValidateException`/`AccountResourceInsufficientException`, which are anticipated and caught along the tx-processing pipeline). An unchecked runtime exception surfacing during block processing risks: (a) permanently breaking resource accounting/transaction processing for the affected account (self-inflicted denial of service on transacting), and (b) in the worst case, if it propagates up through block application without a wrapping catch, causing node-level failure or consensus-affecting divergence between nodes that have/haven't enabled hardening or that hit the condition at different points — a state-halt-class impact analogous to permanently locking a Sablier stream's funds. Note that TVM-native contract call paths (e.g., `delegateResource`, `voteWitness`) do catch `ArithmeticException` explicitly and fail gracefully [12](#0-11) , showing the developers are aware of this exact overflow class in TVM contexts — but the core `BandwidthProcessor.consume()`/`EnergyProcessor.useEnergy()` paths used for ordinary (non-TVM) transaction bandwidth/energy accounting do not have equivalent guards.

### Likelihood Explanation
This requires `allowHardenResourceCalculation` to be enabled by committee proposal (it defaults to off, gated by `ProposalType`/`ProposalUtil`), which lowers likelihood since it is not exploitable under default chain configuration. However, once enabled network-wide (as the tests suggest is the intended forward direction, replacing legacy double-precision math), any single unprivileged account that freezes a sufficiently large balance for energy or bandwidth relative to the current total-weight/total-limit ratio can trigger the exception purely through its own freeze action — no special privileges or victim cooperation required.

### Recommendation
Do not let `longValueExact()` (or any BigInteger-to-long narrowing) throw an unhandled `ArithmeticException` from resource-limit computation paths that are invoked unconditionally during transaction/block processing (`EnergyProcessor.useEnergy`, `BandwidthProcessor.consume`/`useAccountNet`/`useAssetAccountNet`, and `ResourceProcessor.increase`). Either (1) clamp/saturate the result to `Long.MAX_VALUE` instead of throwing, or (2) explicitly catch `ArithmeticException` at these call sites and convert it into a defined, already-handled validation/insufficient-resource exception, consistent with how `Program.delegateResource`/`Program.voteWitness` already handle this class of overflow in the TVM path.

### Proof of Concept
Existing project tests already demonstrate the overflow/throw behavior deterministically (would need to be exercised through the live `consume()`/`useEnergy()` call path to confirm end-to-end propagation, which requires running the code — not verifiable purely via static reading): [8](#0-7) [11](#0-10) 

**Uncertainty:** I was not able to fully verify within the available context (a) whether `allowHardenResourceCalculation` is on any live/default network configuration today, and (b) whether an uncaught `ArithmeticException` thrown mid-block-processing is caught somewhere further up the call stack (e.g., in a generic block-application try/catch) that would downgrade this to a less severe per-transaction failure rather than a node crash/halt. Confirming actual severity would require tracing the full block-processing call stack and current mainnet proposal state, which is best done in a full Devin session with repository build/test access.

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

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L342-348)
```java
  protected boolean disableJavaLangMath() {
    return dynamicPropertiesStore.disableJavaLangMath();
  }

  protected boolean hardenCalculation() {
    return dynamicPropertiesStore.allowHardenResourceCalculation();
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L350-378)
```java
  protected long calculateGlobalLimitV1(long frozeBalance,
      long totalLimit, long totalWeight) {
    long weight = frozeBalance / TRX_PRECISION;
    return BigInteger.valueOf(weight)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(totalWeight))
        .longValueExact();
  }

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

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L155-166)
```java
      if (contract.getType() == TransferAssetContract && useAssetAccountNet(contract,
          accountCapsule, now, bytesSize)) {
        continue;
      }

      if (useAccountNet(accountCapsule, bytesSize, now)) {
        continue;
      }

      if (useFreeNet(accountCapsule, bytesSize, now)) {
        continue;
      }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L432-466)
```java
  public long calculateGlobalNetLimit(AccountCapsule accountCapsule) {
    long frozeBalance = accountCapsule.getAllFrozenBalanceForBandwidth();
    if (dynamicPropertiesStore.supportUnfreezeDelay()) {
      return calculateGlobalNetLimitV2(frozeBalance);
    }
    if (frozeBalance < TRX_PRECISION) {
      return 0;
    }
    long totalNetLimit = chainBaseManager.getDynamicPropertiesStore().getTotalNetLimit();
    long totalNetWeight = chainBaseManager.getDynamicPropertiesStore().getTotalNetWeight();
    if (dynamicPropertiesStore.allowNewReward() && totalNetWeight <= 0) {
      return 0;
    }
    if (totalNetWeight == 0) {
      return 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV1(frozeBalance, totalNetLimit, totalNetWeight);
    }
    long netWeight = frozeBalance / TRX_PRECISION;
    return (long) (netWeight * ((double) totalNetLimit / totalNetWeight));
  }

  public long calculateGlobalNetLimitV2(long frozeBalance) {
    long totalNetLimit = dynamicPropertiesStore.getTotalNetLimit();
    long totalNetWeight = dynamicPropertiesStore.getTotalNetWeight();
    if (totalNetWeight == 0) {
      return 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV2(frozeBalance, totalNetLimit, totalNetWeight);
    }
    double netWeight = (double) frozeBalance / TRX_PRECISION;
    return (long) (netWeight * ((double) totalNetLimit / totalNetWeight));
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

**File:** framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java (L130-141)
```java
  @Test
  public void testGlobalNetLimitOverflowDetectedWithHardening() {
    dbManager.getDynamicPropertiesStore().saveTotalNetLimit(Long.MAX_VALUE / 2);
    dbManager.getDynamicPropertiesStore().saveTotalNetWeight(1L);
    ownerCapsule.setFrozenForBandwidth(Long.MAX_VALUE / 4, 0L);
    dbManager.getAccountStore().put(ownerCapsule.getAddress().toByteArray(), ownerCapsule);

    dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(1);

    Assert.assertThrows(ArithmeticException.class,
        () -> bandwidthProcessor.calculateGlobalNetLimit(ownerCapsule));
  }
```

**File:** framework/src/test/java/org/tron/core/vm/repository/RepositoryImplHardenTest.java (L260-279)
```java
  @Test
  public void testCalculateGlobalEnergyLimitHardenedOverflowDetected() {
    long totalEnergyLimit = Long.MAX_VALUE / 2;
    long totalEnergyWeight = 1L;
    long frozeBalance = Long.MAX_VALUE / 4;

    dbManager.getDynamicPropertiesStore().saveTotalEnergyCurrentLimit(totalEnergyLimit);
    dbManager.getDynamicPropertiesStore().saveTotalEnergyWeight(totalEnergyWeight);

    AccountCapsule account = new AccountCapsule(
        ByteString.copyFromUtf8("owner"),
        ByteString.copyFrom(ByteArray.fromHexString(
            Wallet.getAddressPreFixString() + "548794500882809695a8a687866e76d4271a1abc")),
        AccountType.Normal, 0L);
    account.setFrozenForEnergy(frozeBalance, 0L);

    VMConfig.initAllowHardenResourceCalculation(1);
    Assert.assertThrows(ArithmeticException.class,
        () -> repository.calculateGlobalEnergyLimit(account));
  }
```

**File:** framework/src/test/java/org/tron/core/db/ResourceProcessorHardenTest.java (L106-118)
```java
  @Test
  public void testIncreaseOverflowDetectedWithHardening() {
    long lastUsage = Long.MAX_VALUE / 10; // ~9.2e17
    long usage = 1L;
    long lastTime = 9990L;
    long now = 9995L;
    long windowSize = 28800L;

    dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(1);

    Assert.assertThrows(ArithmeticException.class,
        () -> processor.increase(lastUsage, usage, lastTime, now, windowSize));
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2180-2184)
```java
    } catch (ContractValidateException e) {
      logger.warn("TVM DelegateResource: validate failure. Reason: {}", e.getMessage());
    } catch (ArithmeticException e) {
      logger.warn("TVM DelegateResource: balance out of long range.");
    }
```
