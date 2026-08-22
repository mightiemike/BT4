### Title
Unhandled `ArithmeticException` from hardened resource-limit math can abort bandwidth/energy accounting for every transaction in a block - ([File: chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java])

### Summary
`ResourceProcessor` (and its subclasses `BandwidthProcessor`/`EnergyProcessor`) contains a "hardened" arithmetic path that replaces the legacy double-precision resource-limit computation with `BigInteger` math terminated by `.longValueExact()`. Unlike the legacy double-based formulas, which silently truncate/round, `longValueExact()` throws an unchecked `ArithmeticException` whenever the mathematically correct result does not fit in a `long`. This mirrors the C4 finding's root cause: a "hardening"/correction mechanism that turns a previously silent numeric edge case into a hard failure on a path that must succeed for normal chain operation to continue, creating a DoS vector.

### Finding Description
When `allowHardenResourceCalculation` (chain parameter) is enabled, `calculateGlobalLimitV1`/`calculateGlobalLimitV2` and the various `increase`/`increaseV2`/`getUsage`/`divideCeilExact`/`getNewWindowSize` helpers in `ResourceProcessor.java` compute bandwidth/energy limits and usage windows via `BigInteger` multiplication followed by `.longValueExact()`: [1](#0-0) 

These helpers are invoked unconditionally from `BandwidthProcessor.consume()` — the method that runs for **every contract in every transaction** during bandwidth accounting, which itself is on the block/transaction-processing path (`Manager` calls it while applying transactions): [2](#0-1) [3](#0-2) 

`calculateGlobalNetLimit`/`calculateGlobalNetLimitV2` compute `frozeBalance * totalLimit / totalWeight` (or `/ (TRX_PRECISION * totalWeight)`). This is called once per transaction for the sender's own frozen balance vs. the network-wide `totalNetWeight`/`totalNetLimit`. A repo test explicitly demonstrates that this computation can overflow a `long` and throw `ArithmeticException` once hardening is enabled: [4](#0-3) 

The same unguarded `longValueExact()` pattern recurs throughout `ResourceProcessor` for the usage/window-size accounting used on every `increase`/`unDelegateIncrease` call (delegate/undelegate resource, bandwidth/energy consumption, TVM vote/withdraw reward paths): [5](#0-4) [6](#0-5) 

None of these arithmetic helpers catch `ArithmeticException`; unlike `consumeFeeForBandwidth`/`consumeFeeForNewAccount`, which explicitly catch `BalanceInsufficientException`, there is no corresponding guard for the numeric-overflow path introduced by the "hardened" math.

### Impact Explanation
If any account's frozen balance, combined with the network's current `totalNetWeight`/`totalNetLimit` (or `totalEnergyWeight`/`totalEnergyCurrentLimit`) ratio, produces an intermediate/final value that exceeds `Long.MAX_VALUE`, `BandwidthProcessor.consume()` (or the analogous `EnergyProcessor` path, and `unDelegateIncrease`/`increase` used by `DelegateResourceActuator`/`UnDelegateResourceActuator`/TVM freeze-vote native contracts) throws an unchecked `ArithmeticException`. Because this call happens inside the mandatory resource-accounting step that every transaction must pass through during block application, an uncaught exception here can abort processing of the transaction/block — a deterministic, protocol-level DoS reachable purely from a broadcast transaction (no privileged actor needed), analogous to the report's `_rebalance()`/`updateRewardSum` DoS caused by an unguarded arithmetic edge case in reward/accounting bookkeeping. Because the computation is deterministic and part of chain state transition, this can also cause consensus divergence between nodes with different `allowHardenResourceCalculation` states, or a chain-wide halt once the parameter is enabled network-wide, if the condition is ever reached in practice.

### Likelihood Explanation
Likelihood depends on: (1) `allowHardenResourceCalculation` being enabled via committee proposal, and (2) network parameters (`totalNetWeight`, `totalNetLimit`, `totalEnergyWeight`, `totalEnergyCurrentLimit`) and an account's frozen balance combining to overflow a `long`. Given TRX's realistic total supply and typical weight/limit ratios, this is a low-to-moderate likelihood in the current mainnet state, but the repo's own test (`testGlobalNetLimitOverflowDetectedWithHardening`) proves the code path is genuinely reachable and unguarded — it is a latent correctness/availability defect introduced by the "hardening" migration rather than a purely theoretical concern. I could not fully verify within the available context whether `Manager`/`TransactionTrace` wraps `consume()` calls in a catch-all that converts `RuntimeException` into a normal validation failure (which would reduce this to a single-transaction rejection) or whether it propagates and aborts entire block application (a more severe, consensus-impacting DoS); this distinction should be confirmed by tracing `Manager.java`'s exception handling around the `BandwidthProcessor`/`EnergyProcessor.consume()` call sites.

### Recommendation
- Wrap the `longValueExact()`-based hardened arithmetic in `ResourceProcessor` (`calculateGlobalLimitV1/V2`, `divideCeilExact`, `getUsage`, `getNewWindowSize`, `increase`/`increaseV2`, `unDelegateIncrease(V2)`) with explicit overflow handling (e.g., saturate to `Long.MAX_VALUE` or fall back to the legacy double computation) instead of letting `ArithmeticException` propagate.
- Confirm and, if necessary, harden the exception handling in `Manager`/`TransactionTrace` so any exception thrown during bandwidth/energy accounting results in a well-defined transaction validation failure rather than aborting block processing.
- Add fuzz/property tests around realistic and adversarial `totalNetWeight`/`totalNetLimit`/`frozenBalance` combinations to ensure the hardened path never throws for any value reachable by network parameters and account balances.

### Proof of Concept
The repository's own hardening test constructs the overflow condition directly: [7](#0-6) 
This sets `totalNetLimit = Long.MAX_VALUE/2`, `totalNetWeight = 1`, and an account's frozen bandwidth balance to `Long.MAX_VALUE/4`, then asserts that `bandwidthProcessor.calculateGlobalNetLimit(ownerCapsule)` throws `ArithmeticException` once `allowHardenResourceCalculation` is enabled — the same call made unconditionally inside `useAccountNet`/`consume()` for every transaction that uses bandwidth.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L133-188)
```java
  public long increaseV2(AccountCapsule accountCapsule, ResourceCode resourceCode,
      long lastUsage, long usage, long lastTime, long now) {
    long oldWindowSizeV2 = accountCapsule.getWindowSizeV2(resourceCode);
    long oldWindowSize = accountCapsule.getWindowSize(resourceCode);
    long averageLastUsage;
    long averageUsage;
    if (hardenCalculation()) {
      BigInteger biPrecision = BigInteger.valueOf(this.precision);
      averageLastUsage = divideCeilExact(
          BigInteger.valueOf(lastUsage).multiply(biPrecision),
          BigInteger.valueOf(oldWindowSize));
      averageUsage = divideCeilExact(
          BigInteger.valueOf(usage).multiply(biPrecision),
          BigInteger.valueOf(this.windowSize));
    } else {
      averageLastUsage = divideCeil(lastUsage * this.precision, oldWindowSize);
      averageUsage = divideCeil(usage * this.precision, this.windowSize);
    }

    if (lastTime != now) {
      if (lastTime + oldWindowSize > now) {
        long delta = now - lastTime;
        double decay = (oldWindowSize - delta) / (double) oldWindowSize;
        averageLastUsage = round(averageLastUsage * decay,
            this.disableJavaLangMath());
      } else {
        averageLastUsage = 0;
      }
    }

    long newUsage = getUsage(averageLastUsage, oldWindowSize, averageUsage, this.windowSize);
    long remainUsage = getUsage(averageLastUsage, oldWindowSize);
    if (remainUsage == 0) {
      accountCapsule.setNewWindowSizeV2(resourceCode, this.windowSize * WINDOW_SIZE_PRECISION);
      return newUsage;
    }

    long remainWindowSize = oldWindowSizeV2 - (now - lastTime) * WINDOW_SIZE_PRECISION;
    long newWindowSize;
    if (hardenCalculation()) {
      BigInteger biNewWindowSize = BigInteger.valueOf(remainUsage)
          .multiply(BigInteger.valueOf(remainWindowSize))
          .add(BigInteger.valueOf(usage)
              .multiply(BigInteger.valueOf(this.windowSize))
              .multiply(BigInteger.valueOf(WINDOW_SIZE_PRECISION)));
      newWindowSize = divideCeilExact(biNewWindowSize, BigInteger.valueOf(newUsage));
    } else {
      newWindowSize = divideCeil(
          remainUsage * remainWindowSize + usage * this.windowSize * WINDOW_SIZE_PRECISION,
          newUsage);
    }
    newWindowSize = min(newWindowSize, this.windowSize * WINDOW_SIZE_PRECISION,
        this.disableJavaLangMath());
    accountCapsule.setNewWindowSizeV2(resourceCode, newWindowSize);
    return newUsage;
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L262-300)
```java
  private long getNewWindowSize(long lastUsage, long lastWindowSize, long usage,
      long windowSize, long newUsage) {
    if (hardenCalculation()) {
      BigInteger bi = BigInteger.valueOf(lastUsage).multiply(BigInteger.valueOf(lastWindowSize))
          .add(BigInteger.valueOf(usage).multiply(BigInteger.valueOf(windowSize)));
      return bi.divide(BigInteger.valueOf(newUsage)).longValueExact();
    }
    return (lastUsage * lastWindowSize + usage * windowSize) / newUsage;
  }

  private long divideCeil(long numerator, long denominator) {
    return (numerator / denominator) + ((numerator % denominator) > 0 ? 1 : 0);
  }

  private long divideCeilExact(BigInteger numerator, BigInteger denominator) {
    BigInteger[] divRem = numerator.divideAndRemainder(denominator);
    long result = divRem[0].longValueExact();
    if (divRem[1].signum() > 0) {
      result = StrictMathWrapper.addExact(result, 1);
    }
    return result;
  }

  private long getUsage(long usage, long windowSize) {
    if (hardenCalculation()) {
      return BigInteger.valueOf(usage).multiply(BigInteger.valueOf(windowSize))
          .divide(BigInteger.valueOf(precision)).longValueExact();
    }
    return usage * windowSize / precision;
  }

  private long getUsage(long oldUsage, long oldWindowSize, long newUsage, long newWindowSize) {
    if (hardenCalculation()) {
      BigInteger bi = BigInteger.valueOf(oldUsage).multiply(BigInteger.valueOf(oldWindowSize))
          .add(BigInteger.valueOf(newUsage).multiply(BigInteger.valueOf(newWindowSize)));
      return bi.divide(BigInteger.valueOf(precision)).longValueExact();
    }
    return (oldUsage * oldWindowSize + newUsage * newWindowSize) / precision;
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

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L96-178)
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
