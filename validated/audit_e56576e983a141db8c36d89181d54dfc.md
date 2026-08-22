### Title
Global energy/bandwidth resource limit derived from a manipulable spot-value (`totalEnergyWeight`/`totalNetWeight`) allows a whale to instantly alter another account's computed resource entitlement - ([File: chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java])

### Summary
Analogous to the DYAD kerosene case (a per-user collateral value derived from a globally-shared, instantaneously-mutable denominator that any user can move), java-tron computes each account's energy/bandwidth "limit" as a share of `TOTAL_ENERGY_CURRENT_LIMIT` / `TOTAL_NET_LIMIT` proportional to `frozenBalance / totalEnergyWeight` (or `totalNetWeight`). `totalEnergyWeight`/`totalNetWeight` are global spot aggregates that any staker can shrink or grow within the same block via `FreezeBalanceV2Actuator`/`UnfreezeBalanceV2Actuator`, instantly changing the computed resource entitlement of every other account that depends on that ratio — including values read on-chain by smart contracts through the `ResourceUsage`/`DelegatableResource`/`checkUnDelegateResource` precompiled contracts.

### Finding Description
`EnergyProcessor.calculateGlobalEnergyLimitV2` computes an account's usable energy as: [1](#0-0) 

`totalEnergyWeight` (and analogously `totalNetWeight`) is a chain-wide aggregate stored in `DynamicPropertiesStore`, updated instantly and atomically whenever any account freezes or unfreezes TRX for energy/bandwidth: [2](#0-1) [3](#0-2) 

This "spot ratio" is then exposed to arbitrary smart contracts through TVM precompiles that any contract can call in the same block/transaction as the freeze/unfreeze action: [4](#0-3) [5](#0-4) 

These precompile outputs are derived from `RepositoryImpl.getAccountEnergyUsageBalanceAndRestoreSeconds` / `getAccountNetUsageBalanceAndRestoreSeconds`, which both divide by `getTotalEnergyWeight()` / `getTotalNetWeight()`: [6](#0-5) 

The same denominator is also used inside `UnDelegateResourceActuator`'s `unDelegateMaxUsage`/`transferUsage` calculation, which determines how much "used resource" is transferred back from a delegatee to a delegator upon undelegation — a value that changes depending on the instantaneous `totalEnergyWeight`/`totalNetWeight` at the moment of the transaction: [7](#0-6) 

Exactly as in the DYAD bug (where TVL and kerosene supply are globally shared denominators an attacker can move by depositing/withdrawing, instantly repricing every other user's kerosene-based collateral value), a large staker ("whale") can call `UnfreezeBalanceV2Contract` to withdraw a large amount of frozen TRX for energy in the same block as a victim's resource-dependent operation. This instantly shrinks `totalEnergyWeight`, which increases the computed `energyWeight * (totalEnergyLimit / totalEnergyWeight)` for every remaining staker (including the attacker re-freezing later), and simultaneously reduces the "current usage vs balance" figures other accounts/contracts observe via the precompiles, and it also changes the `unDelegateMaxUsage`/`transferUsage` outcome for any concurrent `UnDelegateResourceContract` transactions relying on the pre-manipulation ratio.

### Impact Explanation
Any DApp or contract logic (e.g., resource-gated actions, auto-delegation vaults, energy-rental/energy-market contracts) that reads `DelegatableResource`, `ResourceUsage`, or `checkUndelegateResource` precompile results to make economic decisions (how much energy/bandwidth a counterparty currently has, how much can be safely delegated/undelegated) can be manipulated within a single block by a large staker unfreezing/freezing TRX to shift `totalEnergyWeight`/`totalNetWeight`. This can force incorrect delegation settlements, incorrect resource availability checks, or a denial-of-service/incorrect accounting outcome for the counterparty contract or user, analogous to forcing the DYAD "liquidation" outcome by depressing kerosene's spot price. This maps to unauthorized/incorrect resource accounting corruption reachable via unprivileged broadcast transactions (`FreezeBalanceV2Contract`, `UnfreezeBalanceV2Contract`, `UnDelegateResourceContract`, and TVM contract calls to the resource precompiles), matching the required categories (resource and reward accounting, precompiled contracts, TVM execution).

### Likelihood Explanation
Freezing/unfreezing TRX and delegating/undelegating resources are ordinary, unprivileged, broadcastable transaction types available to any account with sufficient TRX; no special permission is required. Manipulating `totalEnergyWeight`/`totalNetWeight` within a single block is achievable by any large staker acting opportunistically, mirroring the "whale trap" scenario described in the DYAD report. However, unlike DYAD's per-user liquidation threshold, java-tron has no on-chain lending/liquidation logic itself — the concrete blast radius depends on third-party contracts built on top of these precompiles (energy markets, resource-lending DApps), so the severity is contingent on external contract design; I could not find an in-repo consumer contract that turns this into a self-contained protocol-level loss (unlike DYAD's own liquidation mechanism), so likelihood of high-impact exploitation is dependent on ecosystem contracts, not the node itself.

### Recommendation
- Avoid exposing raw spot ratios of globally-mutable aggregates (`totalEnergyWeight`, `totalNetWeight`, `totalEnergyCurrentLimit`) directly through precompiles without smoothing (e.g., use a time-weighted/average value akin to `updateAdaptiveTotalEnergyLimit`'s averaging already used for `totalEnergyAverageUsage`, or delay total-weight updates to the next block for read purposes).
- When computing `unDelegateMaxUsage`/`transferUsage` in `UnDelegateResourceActuator` and the `getAccountXUsageBalanceAndRestoreSeconds` methods in `RepositoryImpl`, consider snapshotting `totalEnergyWeight`/`totalNetWeight` at a fixed point (e.g., start of block) rather than reading the live, same-block-mutable value.
- Document explicitly for DApp developers relying on `DelegatableResource`/`ResourceUsage`/`checkUndelegateResource` precompiles that these values are spot values subject to intra-block manipulation and should not be used for security-critical decisions without additional safeguards (analogous to DYAD's acknowledgment that kerosene price is a spot value).

### Proof of Concept
Conceptual reproduction (cannot be executed here, but derivable from code paths cited above):
1. Attacker (large staker) has frozen TRX for `ENERGY`, contributing significantly to `totalEnergyWeight` (`FreezeBalanceV2Actuator.execute`, adding to `dynamicStore.addTotalEnergyWeight`). [3](#0-2) 
2. In the same block, a victim's DApp contract calls the `ResourceUsage`/`DelegatableResource` precompile to check a counterparty's available/delegatable energy before executing an economic action (e.g., accepting a delegation, granting an energy-rental credit). [8](#0-7) 
3. Attacker submits `UnfreezeBalanceV2Contract` for a large frozen-energy balance before/around the victim's transaction, reducing `totalEnergyWeight` via `UnfreezeBalanceV2Processor.updateTotalResourceWeight`. [2](#0-1) 
4. Because `calculateGlobalEnergyLimitV2` and `getAccountEnergyUsageBalanceAndRestoreSeconds` divide by the now-reduced `totalEnergyWeight`, every other account's computed energy limit/available balance shifts within that block, changing the precompile's returned value and any dependent contract logic outcome — mirroring how Alice's TVU withdrawal instantly changed Bob's kerosene-based collateralization ratio in the DYAD report. [1](#0-0) [9](#0-8)

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L168-179)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L2149-2234)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L210-254)
```java
  public Pair<Long, Long> getAccountEnergyUsageBalanceAndRestoreSeconds(AccountCapsule accountCapsule) {
    long now = getHeadSlot();

    long energyUsage = accountCapsule.getEnergyUsage();
    long latestConsumeTime = accountCapsule.getAccountResource().getLatestConsumeTimeForEnergy();
    long accountWindowSize = accountCapsule.getWindowSize(Common.ResourceCode.ENERGY);

    if (now >= latestConsumeTime + accountWindowSize) {
      return Pair.of(0L, 0L);
    }

    long restoreSlots = latestConsumeTime + accountWindowSize - now;

    long newEnergyUsage = recover(energyUsage, latestConsumeTime, now, accountWindowSize);

    long totalEnergyLimit = getDynamicPropertiesStore().getTotalEnergyCurrentLimit();
    long totalEnergyWeight = getTotalEnergyWeight();

    long balance = usageToBalance(newEnergyUsage, totalEnergyWeight, totalEnergyLimit);

    return Pair.of(balance, restoreSlots * BLOCK_PRODUCED_INTERVAL / 1_000);
  }

  public Pair<Long, Long> getAccountNetUsageBalanceAndRestoreSeconds(AccountCapsule accountCapsule) {
    long now = getHeadSlot();

    long netUsage = accountCapsule.getNetUsage();
    long latestConsumeTime = accountCapsule.getLatestConsumeTime();
    long accountWindowSize = accountCapsule.getWindowSize(Common.ResourceCode.BANDWIDTH);

    if (now >= latestConsumeTime + accountWindowSize) {
      return Pair.of(0L, 0L);
    }

    long restoreSlots = latestConsumeTime + accountWindowSize - now;

    long newNetUsage = recover(netUsage, latestConsumeTime, now, accountWindowSize);

    long totalNetLimit = getDynamicPropertiesStore().getTotalNetLimit();
    long totalNetWeight = getTotalNetWeight();

    long balance = usageToBalance(newNetUsage, totalNetWeight, totalNetLimit);

    return Pair.of(balance, restoreSlots * BLOCK_PRODUCED_INTERVAL / 1_000);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L67-123)
```java
    // modify receiver Account
    if (receiverCapsule != null) {
      long now = chainBaseManager.getHeadSlot();
      switch (unDelegateResourceContract.getResource()) {
        case BANDWIDTH:
          BandwidthProcessor bandwidthProcessor = new BandwidthProcessor(chainBaseManager);
          bandwidthProcessor.updateUsageForDelegated(receiverCapsule);

          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForBandwidth()
              < unDelegateBalance) {
            // A TVM contract suicide, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForBandwidth(0);
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalNetLimit()) / dynamicStore.getTotalNetWeight()));
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }

          long newNetUsage = receiverCapsule.getNetUsage() - transferUsage;
          receiverCapsule.setNetUsage(newNetUsage);
          receiverCapsule.setLatestConsumeTime(now);
          break;
        case ENERGY:
          EnergyProcessor energyProcessor = new EnergyProcessor(dynamicStore, accountStore);
          energyProcessor.updateUsage(receiverCapsule);

          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForEnergy()
              < unDelegateBalance) {
            // A TVM contract receiver, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForEnergy(0);
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalEnergyCurrentLimit()) / dynamicStore.getTotalEnergyWeight()));
            transferUsage = (long) (receiverCapsule.getEnergyUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForEnergy()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(-unDelegateBalance);
          }

          long newEnergyUsage = receiverCapsule.getEnergyUsage() - transferUsage;
          receiverCapsule.setEnergyUsage(newEnergyUsage);
          receiverCapsule.setLatestConsumeTimeForEnergy(now);
          break;
        default:
          //this should never happen
          break;
      }
      accountStore.put(receiverCapsule.createDbKey(), receiverCapsule);
    }

```
