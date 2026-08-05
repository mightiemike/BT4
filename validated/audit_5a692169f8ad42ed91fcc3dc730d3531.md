### Title
Freeze-V2 TVM Precompiles Silently Return Zero for TRON_POWER (type=2), Omitting the Third Resource Type from Delegatable/Usage/Undelegate Calculations - (File: actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java)

### Summary
`FreezeV2Util` implements the backing logic for several TVM precompiled contracts that let smart contracts query FreezeV2 resource state (delegatable amount, usage, and safe-undelegate amount). The `ResourceCode` enum has three resource types — `BANDWIDTH` (0), `ENERGY` (1), and `TRON_POWER` (2) — and `queryUnfreezableBalanceV2` correctly handles all three [1](#0-0) . However, `queryFrozenBalanceUsage`, `queryDelegatableResource`, `checkUndelegateResource`, and `queryResourceV2` only implement branches for type 0 and type 1, falling through to a hardcoded zero result for type 2 (`TRON_POWER`) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) . This mirrors the reported bug class: a table/branch construct that enumerates only a subset of valid categories, silently returning zero for the omitted (here, third) category instead of computing/erroring.

### Finding Description
The `ResourceCode` protobuf enum defines `BANDWIDTH=0`, `ENERGY=1`, `TRON_POWER=2` and TRON_POWER-based freezing/unfreezing is a fully supported, user-facing feature — actuators such as `FreezeBalanceV2Actuator` and `UnfreezeBalanceV2Processor` explicitly implement the `TRON_POWER` case alongside `BANDWIDTH`/`ENERGY` [6](#0-5) [7](#0-6) . Yet the corresponding read-side TVM precompiles in `PrecompiledContracts.java` — `ResourceUsage`, `DelegatableResource`, and `CheckUnDelegateResource` — delegate to `FreezeV2Util` methods that never check `type == 2`, so any contract-driven query for TRON_POWER usage, delegatable amount, or undelegate safety silently returns all zeros rather than the real value or an explicit error [8](#0-7) .

### Impact Explanation
Any unprivileged smart contract can call these precompiled contracts (addresses `0x...f`, `0x...11`, `0x...12`) with `type=2` to query TRON_POWER-related state. Because the omitted branch returns zero:
- `ResourceUsage`/`DelegatableResource` will report zero usage and zero delegatable balance for TRON_POWER even when the account has frozen/used substantial TRON_POWER, causing on-chain contracts that gate delegate/undelegate logic on these precompile results (e.g., automated delegation managers, resource marketplaces) to make incorrect accounting/authorization decisions — potentially allowing operations that should be blocked (e.g., attempting delegation beyond real capacity) or blocking operations that should be allowed.
- `CheckUnDelegateResource` for `type=2` always returns `(0,0,0)`, misrepresenting how much of an undelegation would be "clean" vs. still-locked-by-usage and the remaining lock time, which can cause downstream contract logic relying on this precompile to under- or over-estimate safe undelegation amounts for TRON_POWER.

This is a divergence/invalid-state class issue confined to the read-only accounting precompiles for one of three resource types; it does not directly move funds itself, but it produces incorrect state to any contract logic built atop these precompiles, which is the class of impact acknowledged in the analog report (silent zero instead of correct nonzero value for a valid, in-scope category).

### Likelihood Explanation
High likelihood of being hit in practice: any contract or off-chain actor issuing a TVM call to these precompile addresses with `type=2` (TRON_POWER) — a documented, first-class resource type — triggers the bug deterministically, with no special privileges or race conditions required. TRON_POWER freezing/unfreezing has been supported since the FreezeV2 mechanism was introduced, so type=2 queries are a normal, expected usage pattern for any voting-power delegation tooling built on TVM.

### Recommendation
Add the missing `type == 2` (`TRON_POWER`) branches to `queryFrozenBalanceUsage`, `queryDelegatableResource`, `checkUndelegateResource`, and `queryResourceV2` in `FreezeV2Util.java`, mirroring the pattern already used in `queryUnfreezableBalanceV2` and in the write-side actuators (`FreezeBalanceV2Actuator`, `UnfreezeBalanceV2Processor`), using the appropriate TRON_POWER usage/restore-seconds accessors instead of falling through to a hardcoded zero.

### Proof of Concept
1. Freeze balance for TRON_POWER on an account via `FreezeBalanceV2Contract` with `resource=TRON_POWER` — confirmed supported in `FreezeBalanceV2Actuator` [9](#0-8) .
2. From a smart contract (or via `eth_call`), invoke the `DelegatableResource` precompile (address `...f`) with `(address, type=2)`.
3. Observe that `FreezeV2Util.queryDelegatableResource` returns `0` unconditionally for `type==2` because no branch exists for it [3](#0-2) , regardless of the account's actual frozen TRON_POWER balance — contrast with step 1 where the account demonstrably holds frozen TRON_POWER.
4. Similarly invoke `ResourceUsage` (address `...12`) and `CheckUnDelegateResource` (address `...11`) with `type=2` and observe zeroed results via `queryFrozenBalanceUsage`/`checkUndelegateResource` [2](#0-1) [4](#0-3) , in contrast to the correctly-handled `type=0/1` cases exercised by the existing test suite (`PrecompiledContractsTest.delegatableResourceTest`/`checkUnDelegateResourceTest`/`resourceUsageTest`), none of which exercise `type=2`.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L39-65)
```java
  public static long queryUnfreezableBalanceV2(byte[] address, long type, Repository repository) {
    if (!VMConfig.allowTvmFreezeV2()) {
      return 0;
    }

    AccountCapsule accountCapsule = repository.getAccount(address);
    if (accountCapsule == null) {
      return 0;
    }

    // BANDWIDTH
    if (type == 0) {
      return accountCapsule.getFrozenV2BalanceForBandwidth();
    }

    // ENERGY
    if (type == 1) {
      return accountCapsule.getFrozenV2BalanceForEnergy();
    }

    // POWER
    if (type == 2) {
      return accountCapsule.getTronPowerFrozenV2Balance();
    }

    return 0;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L68-105)
```java
  public static long queryResourceV2(byte[] from, byte[] to, long type, Repository repository) {
    if (!VMConfig.allowTvmFreezeV2()) {
      return 0;
    }

    byte[] key = DelegatedResourceCapsule.createDbKeyV2(from, to, false);
    byte[] lockKey = DelegatedResourceCapsule.createDbKeyV2(from, to, true);
    DelegatedResourceCapsule delegatedResource = repository.getDelegatedResource(key);
    DelegatedResourceCapsule lockDelegateResource = repository.getDelegatedResource(lockKey);
    if (delegatedResource == null && lockDelegateResource == null) {
      return 0;
    }

    long amount = 0;
    // BANDWIDTH
    if (type == 0) {
      if (delegatedResource != null) {
        amount += delegatedResource.getFrozenBalanceForBandwidth();
      }
      if (lockDelegateResource != null) {
        amount += lockDelegateResource.getFrozenBalanceForBandwidth();
      }
      return amount;
    }

    // ENERGY
    if (type == 1) {
      if (delegatedResource != null) {
        amount += delegatedResource.getFrozenBalanceForEnergy();
      }
      if (lockDelegateResource != null) {
        amount += lockDelegateResource.getFrozenBalanceForEnergy();
      }
      return amount;
    }

    return 0;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L107-124)
```java
  public static Pair<Long, Long> queryFrozenBalanceUsage(byte[] address, long type, Repository repository) {
    if (!VMConfig.allowTvmFreezeV2()) {
      return Pair.of(0L, 0L);
    }

    AccountCapsule accountCapsule = repository.getAccount(address);
    if (accountCapsule == null) {
      return Pair.of(0L, 0L);
    }

    if (type == 0) {
      return repository.getAccountNetUsageBalanceAndRestoreSeconds(accountCapsule);
    } else if (type == 1) {
      return repository.getAccountEnergyUsageBalanceAndRestoreSeconds(accountCapsule);
    }

    return Pair.of(0L, 0L);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L142-193)
```java
  public static long queryDelegatableResource(byte[] address, long type, Repository repository) {
    if (!VMConfig.allowTvmFreezeV2()) {
      return 0L;
    }

    AccountCapsule accountCapsule = repository.getAccount(address);
    if (accountCapsule == null) {
      return 0L;
    }

    if (type == 0) {
      // self frozenV2 resource
      long frozenV2Resource = accountCapsule.getFrozenV2BalanceForBandwidth();

      // total Usage.
      Pair<Long, Long> usagePair =
          repository.getAccountNetUsageBalanceAndRestoreSeconds(accountCapsule);
      if (usagePair == null || usagePair.getLeft() == null) {
        return frozenV2Resource;
      }

      long usage = usagePair.getLeft();
      if (usage <= 0) {
        return frozenV2Resource;
      }

      long v2NetUsage = getV2NetUsage(accountCapsule, usage, VMConfig.disableJavaLangMath());
      return max(0L, frozenV2Resource - v2NetUsage, VMConfig.disableJavaLangMath());
    }

    if (type == 1) {
      // self frozenV2 resource
      long frozenV2Resource = accountCapsule.getFrozenV2BalanceForEnergy();

      // total Usage.
      Pair<Long, Long> usagePair =
          repository.getAccountEnergyUsageBalanceAndRestoreSeconds(accountCapsule);
      if (usagePair == null || usagePair.getLeft() == null) {
        return frozenV2Resource;
      }

      long usage = usagePair.getLeft();
      if (usage <= 0) {
        return frozenV2Resource;
      }

      long v2EnergyUsage = getV2EnergyUsage(accountCapsule, usage, VMConfig.disableJavaLangMath());
      return max(0L, frozenV2Resource - v2EnergyUsage, VMConfig.disableJavaLangMath());
    }

    return 0L;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L195-233)
```java
  public static Triple<Long, Long, Long> checkUndelegateResource(byte[] address, long amount, long type, Repository repository) {
    if (!VMConfig.allowTvmFreezeV2()) {
      return Triple.of(0L, 0L, 0L);
    }

    if (amount <= 0) {
      return Triple.of(0L, 0L, 0L);
    }

    AccountCapsule accountCapsule = repository.getAccount(address);
    if (accountCapsule == null) {
      return Triple.of(0L, 0L, 0L);
    }

    Pair<Long, Long> usagePair;
    long resourceLimit;
    if (type == 0) {
      usagePair = repository.getAccountNetUsageBalanceAndRestoreSeconds(accountCapsule);
      resourceLimit = accountCapsule.getAllFrozenBalanceForBandwidth();
    } else if (type == 1) {
      usagePair = repository.getAccountEnergyUsageBalanceAndRestoreSeconds(accountCapsule);
      resourceLimit = accountCapsule.getAllFrozenBalanceForEnergy();
    } else {
      return Triple.of(0L, 0L, 0L);
    }

    if (usagePair == null || usagePair.getLeft() == null || usagePair.getRight() == null) {
      return Triple.of(0L, 0L, 0L);
    }

    amount = min(amount, resourceLimit, VMConfig.disableJavaLangMath());
    if (resourceLimit <= usagePair.getLeft()) {
      return Triple.of(0L, amount, usagePair.getRight());
    }

    long clean = (long) (amount * ((double) (resourceLimit - usagePair.getLeft()) / resourceLimit));

    return Triple.of(clean, amount - clean, usagePair.getRight());
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L60-81)
```java
    switch (freezeBalanceV2Contract.getResource()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(frozenBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        dynamicStore.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(frozenBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        dynamicStore.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(frozenBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        dynamicStore.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        logger.debug("Resource Code Error.");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L178-204)
```java
  public void updateTotalResourceWeight(AccountCapsule accountCapsule,
                                        Common.ResourceCode freezeType,
                                        long unfreezeBalance,
                                        Repository repo) {
    switch (freezeType) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(-unfreezeBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        repo.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(-unfreezeBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        repo.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(-unfreezeBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        repo.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        //this should never happen
        break;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L2127-2234)
```java
  public static class DelegatableResource extends PrecompiledContract {

    @Override
    public long getEnergyForData(byte[] data) {
      return 50;
    }

    @Override
    public Pair<Boolean, byte[]> execute(byte[] data) {
      if (data == null || data.length != 2 * WORD_SIZE) {
        return Pair.of(true, DataWord.ZERO().getData());
      }

      DataWord[] words = DataWord.parseArray(data);
      byte[] address = words[0].toTronAddress();
      long type = words[1].longValueSafe();

      long result = FreezeV2Util.queryDelegatableResource(address, type, getDeposit());
      return Pair.of(true, longTo32Bytes(result));
    }
  }

  public static class ResourceV2 extends PrecompiledContract {

    @Override
    public long getEnergyForData(byte[] data) {
      return 50;
    }

    @Override
    public Pair<Boolean, byte[]> execute(byte[] data) {
      if (data == null || data.length != 3 * WORD_SIZE) {
        return Pair.of(true, DataWord.ZERO().getData());
      }

      DataWord[] words = DataWord.parseArray(data);
      byte[] target = words[0].toTronAddress();
      byte[] from = words[1].toTronAddress();
      long type = words[2].longValueSafe();

      long balance;
      if (Arrays.equals(from, target)) {
        balance = FreezeV2Util.queryUnfreezableBalanceV2(from, type, getDeposit());
      } else {
        balance = FreezeV2Util.queryResourceV2(from, target, type, getDeposit());
      }
      return Pair.of(true, longTo32Bytes(balance));
    }
  }

  public static class CheckUnDelegateResource extends PrecompiledContract {

    @Override
    public long getEnergyForData(byte[] data) {
      return 50;
    }

    @Override
    public Pair<Boolean, byte[]> execute(byte[] data) {
      if (data == null || data.length != 3 * WORD_SIZE) {
        return Pair.of(true, encodeMultiRes(
            DataWord.ZERO().getData(), DataWord.ZERO().getData(), DataWord.ZERO().getData()));
      }

      DataWord[] words = DataWord.parseArray(data);
      byte[] target = words[0].toTronAddress();
      long amount = words[1].longValueSafe();
      long type = words[2].longValueSafe();

      Triple<Long, Long, Long> values =
          FreezeV2Util.checkUndelegateResource(target, amount, type, getDeposit());
      if (values == null || values.getLeft() == null
          || values.getMiddle() == null || values.getRight() == null) {
        return Pair.of(true, encodeMultiRes(
            DataWord.ZERO().getData(), DataWord.ZERO().getData(), DataWord.ZERO().getData()));
      }

      return Pair.of(true, encodeMultiRes(longTo32Bytes(values.getLeft()),
          longTo32Bytes(values.getMiddle()), longTo32Bytes(values.getRight())));
    }
  }

  public static class ResourceUsage extends PrecompiledContract {

    @Override
    public long getEnergyForData(byte[] data) {
      return 50;
    }

    @Override
    public Pair<Boolean, byte[]> execute(byte[] data) {
      if (data == null || data.length != 2 * WORD_SIZE) {
        return Pair.of(true, encodeRes(DataWord.ZERO().getData(), DataWord.ZERO().getData()));
      }

      DataWord[] words = DataWord.parseArray(data);
      byte[] address = words[0].toTronAddress();
      long type = words[1].longValueSafe();

      Pair<Long, Long> values = FreezeV2Util.queryFrozenBalanceUsage(address, type, getDeposit());
      if (values == null || values.getLeft() == null || values.getRight() == null) {
        return Pair.of(true, encodeRes(DataWord.ZERO().getData(), DataWord.ZERO().getData()));
      }

      return Pair.of(true, encodeRes(
          longTo32Bytes(values.getLeft()), longTo32Bytes(values.getRight())));
    }
  }
```
