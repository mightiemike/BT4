### Title
DoS via ArithmeticException in resource-limit calculation when `allowHardenResourceCalculation` is enabled - (File: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java`, `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java`, `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java`)

### Summary
`calculateGlobalEnergyLimit`/`calculateGlobalNetLimit` (and their V1/V2 helpers `calculateGlobalLimitV1`/`calculateGlobalLimitV2`) compute an account's per-cycle energy/bandwidth cap as `frozenBalance * totalLimit / totalWeight`. When the chain parameter `allowHardenResourceCalculation` is on, this is done via `BigInteger...longValueExact()`, which throws an unhandled `ArithmeticException` if the mathematically correct result does not fit in a `long`, or if `totalWeight` is `0` (division by zero). The legacy (unhardened) path used `double` arithmetic, which silently saturates instead of throwing. This function is invoked on essentially every resource-consuming transaction/contract call (bandwidth or energy accounting), so an unhandled exception here can fail transaction processing broadly — the same "unexpected throw in a cap-vs-usage calculation triggered by every user operation" bug class as the DnGmx `availableBorrow` underflow-revert.

### Finding Description
`ResourceProcessor.calculateGlobalLimitV1`/`calculateGlobalLimitV2` use `BigInteger` division followed by `.longValueExact()`: [1](#0-0) 

`EnergyProcessor.calculateGlobalEnergyLimit`/`calculateGlobalEnergyLimitV2` call into these hardened helpers whenever `allowHardenResourceCalculation` is enabled, with only an `assert totalEnergyWeight > 0` guard (Java assertions are disabled by default in production JVMs, so this provides no real protection against `totalEnergyWeight == 0`, which would make `BigInteger.divide(BigInteger.ZERO)` throw `ArithmeticException`): [2](#0-1) 

The identical pattern exists for bandwidth in `BandwidthProcessor.calculateGlobalNetLimit`/`calculateGlobalNetLimitV2`, and in the VM's `RepositoryImpl.calculateGlobalEnergyLimit`: [3](#0-2) [4](#0-3) 

These functions are reached on the hot path of ordinary user transactions: `EnergyProcessor.useEnergy` and `getAccountLeftEnergyFromFreeze` call `calculateGlobalEnergyLimit` for every TVM contract invocation, and `VMActuator.getAccountEnergyLimitWithFloatRatio` calls `rootRepository.calculateGlobalEnergyLimit(account)` while computing the caller's available energy for a contract call: [5](#0-4) [6](#0-5) 

`BandwidthProcessor.consume()` calls `calculateGlobalNetLimit` for every transaction (via `useAccountNet`/`consumeBandwidthForCreateNewAccount`/`useAssetAccountNet`) — this is exercised by every broadcast transaction, not just contract calls: [7](#0-6) [8](#0-7) 

This is directly analogous to the reported bug class: a cap-vs-usage arithmetic expression that unconditionally throws when triggered by attacker-influenced state (a large frozen-for-energy/bandwidth balance relative to `totalEnergyWeight`/`totalNetWeight`, or a transient zero total weight), causing every subsequent transaction touching that computation to fail — a DoS, just as `availableBorrow`'s underflow blocked all junior-vault deposits/withdraws. The repo's own test suite explicitly documents this failure mode with tests named `testGlobalEnergyLimitOverflowDetectedWithHardening` and `testCalculateGlobalEnergyLimitHardenedOverflowDetected`, both of which assert that `ArithmeticException` is thrown: [9](#0-8) [10](#0-9) 

### Impact Explanation
If an account's frozen-for-energy (or -bandwidth) weight becomes disproportionately large relative to the network-wide `totalEnergyWeight`/`totalNetWeight` — or if `totalEnergyWeight`/`totalNetWeight` is (even momentarily) `0` — the hardened calculation throws an unhandled `ArithmeticException`. Because `calculateGlobalEnergyLimit`/`calculateGlobalNetLimit` are called on essentially every transaction that touches bandwidth accounting (`BandwidthProcessor.consume`, called for all transactions) or energy accounting (any smart contract call), this can turn into a broad denial-of-service: transactions from the affected account fail, and if the exception is thrown during block-level processing it is not caught by `ContractValidateException`/`ContractExeException`/`AccountResourceInsufficientException` handlers up the call stack (those are the only checked exceptions these methods' callers declare), so it propagates as a `RuntimeException`, which for a witness node processing a block could interrupt consensus/transaction-execution flow for that block.

### Likelihood Explanation
This bug is gated behind the `allowHardenResourceCalculation` chain parameter — a committee-controlled feature flag that must be turned on via proposal before this code path is reachable. Whether the exact overflow/zero-weight condition (large frozen balance vs. small `totalEnergyWeight`, or `totalNetWeight == 0`) is realistically achievable by an ordinary account under production supply/precision constraints is not fully verified here — the test cases that trigger the exception use extreme synthetic values (`Long.MAX_VALUE/4` frozen balance, `totalEnergyWeight = 1`) that may not correspond to states reachable through normal `FreezeBalanceV2`/`UnfreezeBalanceV2` usage, since a user's own freeze action also increases the corresponding total weight. The `totalWeight == 0` divide-by-zero path is more plausible (e.g., immediately after genesis, or after mass unfreezing) but confirming exact reachability would require deeper analysis of how `totalEnergyWeight`/`totalNetWeight` are maintained across freeze/unfreeze/delegate lifecycle code, which was not completed within the scope of this review.

### Recommendation
- Add explicit, non-assertion guards for `totalEnergyWeight <= 0` / `totalNetWeight <= 0` before entering the hardened `BigInteger` path in `EnergyProcessor.calculateGlobalEnergyLimit`, `calculateGlobalEnergyLimitV2`, `BandwidthProcessor.calculateGlobalNetLimit`, `calculateGlobalNetLimitV2`, and `RepositoryImpl.calculateGlobalEnergyLimit`, returning `0` instead of dividing by zero (mirroring the recommendation to return `0` instead of underflowing in the original report).
- In `calculateGlobalLimitV1`/`calculateGlobalLimitV2`, replace `longValueExact()` with a saturating conversion (clamp to `Long.MAX_VALUE`) rather than letting `ArithmeticException` escape, so a pathological ratio degrades gracefully instead of failing the enclosing transaction/block-processing call.
- Ensure any remaining `ArithmeticException` from these paths is caught and converted into a checked `ContractValidateException`/`ContractExeException` at the actuator/VM boundary rather than propagating as an unchecked `RuntimeException`.

### Proof of Concept
Reachable via the repository's own unit tests, which reproduce the exact throw without any privileged access:
```java
// EnergyProcessor hardened overflow (chainbase test)
dbManager.getDynamicPropertiesStore().saveTotalEnergyCurrentLimit(Long.MAX_VALUE / 2);
dbManager.getDynamicPropertiesStore().saveTotalEnergyWeight(1L);
ownerCapsule.setFrozenForEnergy(Long.MAX_VALUE / 4, 0L);
dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(1);
Assert.assertThrows(ArithmeticException.class,
    () -> energyProcessor.calculateGlobalEnergyLimit(ownerCapsule));
``` [9](#0-8) 

```java
// RepositoryImpl (VM-facing) hardened overflow
dbManager.getDynamicPropertiesStore().saveTotalEnergyCurrentLimit(Long.MAX_VALUE / 2);
dbManager.getDynamicPropertiesStore().saveTotalEnergyWeight(1L);
account.setFrozenForEnergy(Long.MAX_VALUE / 4, 0L);
VMConfig.initAllowHardenResourceCalculation(1);
Assert.assertThrows(ArithmeticException.class,
    () -> repository.calculateGlobalEnergyLimit(account));
``` [10](#0-9) 

Uncertainty: whether an unprivileged account can drive its own `frozenForEnergy`/`totalEnergyWeight` ratio into this exact regime through normal `FreezeBalanceV2Contract` usage (given that freezing also increases `totalEnergyWeight` proportionally) was not conclusively established; the divide-by-zero variant (`totalEnergyWeight == 0`) is the more plausible unprivileged trigger but its exact reachability window was not verified against the freeze/unfreeze/delegate accounting code within the scope of this review.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L96-177)
```java
  @Override
  public void consume(TransactionCapsule trx, TransactionTrace trace)
      throws ContractValidateException, AccountResourceInsufficientException,
      TooBigTransactionResultException, TooBigTransactionException {
    List<Contract> contracts = trx.getInstance().getRawData().getContractList();
    long resultSizeWithMaxContractRet = trx.getResultSizeWithMaxContractRet();
    boolean optimizeTxs = !trx.isInBlock() || chainBaseManager
        .getDynamicPropertiesStore().allowConsensusLogicOptimization();
    if (!trx.isInBlock() && resultSizeWithMaxContractRet >
        Constant.MAX_RESULT_SIZE_IN_TX * contracts.size()) {
      throw new TooBigTransactionResultException(String.format(
          "Too big transaction result, TxId %s, the result size is %d bytes, maxResultSize %d",
          trx.getTransactionId(), resultSizeWithMaxContractRet, Constant.MAX_RESULT_SIZE_IN_TX));
    }
    if (trx.getResultSerializedSize() > Constant.MAX_RESULT_SIZE_IN_TX * contracts.size()) {
      throw new TooBigTransactionResultException();
    }

    long bytesSize;

    if (chainBaseManager.getDynamicPropertiesStore().supportVM()) {
      bytesSize = trx.getInstance().toBuilder().clearRet().build().getSerializedSize();
    } else {
      bytesSize = trx.getSerializedSize();
    }

    for (Contract contract : contracts) {
      if (contract.getType() == ShieldedTransferContract) {
        continue;
      }
      if (chainBaseManager.getDynamicPropertiesStore().supportVM()) {
        bytesSize += Constant.MAX_RESULT_SIZE_IN_TX;
      }

      logger.debug("TxId {}, bandwidth cost: {}.", trx.getTransactionId(), bytesSize);
      trace.setNetBill(bytesSize, 0);
      byte[] address = TransactionCapsule.getOwner(contract);
      AccountCapsule accountCapsule = chainBaseManager.getAccountStore().get(address);
      if (accountCapsule == null) {
        throw new ContractValidateException(String.format("account [%s] does not exist",
            StringUtil.encode58Check(address)));
      }
      long now = chainBaseManager.getHeadSlot();
      if (contractCreateNewAccount(contract)) {
        if (optimizeTxs) {
          long maxCreateAccountTxSize = dynamicPropertiesStore.getMaxCreateAccountTxSize();
          int signatureCount = trx.getInstance().getSignatureCount();
          long createAccountBytesSize = trx.getInstance().toBuilder().clearRet()
              .build().getSerializedSize() - (signatureCount * PER_SIGN_LENGTH);
          if (createAccountBytesSize > maxCreateAccountTxSize) {
            throw new TooBigTransactionException(String.format(
                "Too big new account transaction, TxId %s, the size is %d bytes, maxTxSize %d",
                trx.getTransactionId(), createAccountBytesSize, maxCreateAccountTxSize));
          }
        }
        consumeForCreateNewAccount(accountCapsule, bytesSize, now, trace);
        continue;
      }

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

      if (useTransactionFee(accountCapsule, bytesSize, trace)) {
        continue;
      }

      long fee = chainBaseManager.getDynamicPropertiesStore().getTransactionFee() * bytesSize;
      throw new AccountResourceInsufficientException(
          String.format(
              "account [%s] has insufficient bandwidth[%d] and balance[%d] to create new account",
              StringUtil.encode58Check(address), bytesSize, fee));
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

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L468-488)
```java
  private boolean useAccountNet(AccountCapsule accountCapsule, long bytes, long now) {

    long netUsage = accountCapsule.getNetUsage();
    long latestConsumeTime = accountCapsule.getLatestConsumeTime();
    long netLimit = calculateGlobalNetLimit(accountCapsule);

    long newNetUsage;
    if (!dynamicPropertiesStore.supportUnfreezeDelay()) {
      newNetUsage = increase(netUsage, 0, latestConsumeTime, now);
    } else {
      // only participate in the calculation as a temporary variable, without disk flushing
      newNetUsage = recovery(accountCapsule, BANDWIDTH, netUsage, latestConsumeTime, now);
    }


    if (bytes > (netLimit - newNetUsage)) {
      logger.debug("Net usage is running out, now use free net usage."
              + " Bytes: {}, netLimit: {}, newNetUsage: {}.",
          bytes, netLimit, newNetUsage);
      return false;
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

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L606-631)
```java
  private long getAccountEnergyLimitWithFloatRatio(AccountCapsule account, long feeLimit,
      long callValue) {

    long sunPerEnergy = VMConstant.SUN_PER_ENERGY;
    if (rootRepository.getDynamicPropertiesStore().getEnergyFee() > 0) {
      sunPerEnergy = rootRepository.getDynamicPropertiesStore().getEnergyFee();
    }
    // can change the calc way
    long leftEnergyFromFreeze = rootRepository.getAccountLeftEnergyFromFreeze(account);
    callValue = max(callValue, 0, VMConfig.disableJavaLangMath());
    long energyFromBalance = floorDiv(max(
        account.getBalance() - callValue, 0, VMConfig.disableJavaLangMath()), sunPerEnergy,
        VMConfig.disableJavaLangMath());

    long energyFromFeeLimit;
    long totalBalanceForEnergyFreeze = account.getAllFrozenBalanceForEnergy();
    if (0 == totalBalanceForEnergyFreeze) {
      energyFromFeeLimit =
          feeLimit / sunPerEnergy;
    } else {
      long totalEnergyFromFreeze = rootRepository
          .calculateGlobalEnergyLimit(account);
      long leftBalanceForEnergyFreeze = getEnergyFee(totalBalanceForEnergyFreeze,
          leftEnergyFromFreeze,
          totalEnergyFromFreeze);

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
