### Title
Flash delegate/undelegate of TVM-native resources lets an attacker corrupt a receiver's bandwidth/energy usage-window accounting within a single transaction - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java], [File: actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java])

### Summary
`DelegateResourceProcessor` and `UnDelegateResourceProcessor` implement the native-contract (TVM opcode) versions of `DelegateResource`/`UnDelegateResource`, invoked from `Program.java`, meaning a smart contract can call delegate and then undelegate the same resource within a single transaction. [1](#0-0) [2](#0-1) 
`UnDelegateResourceActuator`/`UnDelegateResourceProcessor` allow an unlocked delegation to be revoked with no minimum holding time - only locked delegations check an expiry timestamp. [3](#0-2) 

### Finding Description
This is the same bug class as the Morpho report: a shared accounting state (there, the P2P borrow-rate delta; here, a receiver account's bandwidth/energy usage window) is mutated based on a ratio computed from a value that an attacker can inflate and then instantly retract in the same transaction, permanently corrupting the shared state for everyone who relies on it.

When undelegating, the receiver's `transferUsage` (the portion of its recorded resource *usage* that is "clawed back" to the un-delegating owner) is computed as:
```
transferUsage = receiverCapsule.getNetUsage() * (unDelegateBalance / receiverCapsule.getAllFrozenBalanceForBandwidth())
```
(analogous formula for ENERGY). [4](#0-3) 

`getAllFrozenBalanceForBandwidth`/`ForEnergy` is the receiver's *current* total frozen balance (own + all delegated-in balance) at the moment of the undelegate call. Because a single transaction can:
1. Call the native `DelegateResource` to delegate a huge, momentary balance to a receiver `R` (inflating `R.getAllFrozenBalanceForEnergy()`), and
2. Immediately call the native `UnDelegateResource` to withdraw that same balance,

the attacker can make `unDelegateBalance / receiver.getAllFrozenBalanceForEnergy()` an arbitrary ratio close to 1 (by making its flash delegation the dominant share of `R`'s frozen balance at the instant of undelegation). This lets the attacker siphon almost all of `R`'s recorded `energyUsage`/`netUsage` into `transferUsage`, which is then folded into the *attacker's own* usage/`windowSize` via `unDelegateIncrease`/`unDelegateIncreaseV2`. [5](#0-4) [6](#0-5) 

The net effect, exactly mirroring the Morpho "delta" mechanic: the receiver's real accumulated usage record (`receiverCapsule.setEnergyUsage(newEnergyUsage)`) is reset to near-zero within a single transaction, giving `R` an artificially refreshed usage window (extra free energy/bandwidth capacity it did not legitimately earn) at the expense of the correctness of the shared resource-limit accounting used by `EnergyProcessor`/`BandwidthProcessor` (`calculateGlobalEnergyLimit`, `calculateGlobalNetLimit`) for every other account whose limits are derived from the same global weight/limit ratios. [7](#0-6) [8](#0-7) 

This matches the report's root cause precisely: a "queue"/shared-accounting mechanism (there: P2P delta; here: usage-window `transferUsage`) that is meant to be updated gradually/fairly, but can instead be manipulated to an extreme ratio via an atomic flash supply-then-withdraw, permanently skewing accounting for parties who did not participate in the attack transaction.

### Impact Explanation
An attacker-controlled (or attacker-colluding) receiver account can use this technique to erase its own recorded energy/bandwidth usage on demand, effectively granting itself free resource capacity beyond what its real staked/delegated backing justifies. This corrupts the energy/bandwidth accounting invariants relied upon by `EnergyProcessor`/`BandwidthProcessor` for fair resource allocation, and can be used to let a contract consume more energy/bandwidth than it is entitled to (a resource-accounting/DoS-adjacent corruption), while the attacker's own account absorbs a manipulated `windowSize`/usage value that does not reflect real consumption history. This is a Medium-severity accounting-corruption issue analogous to the original report, not privileged-actor-only, since it is reachable from an ordinary broadcast transaction (a smart contract invoking the delegate/undelegate native contracts).

### Likelihood Explanation
Likelihood is Medium: the primitive (delegate then immediately undelegate) is directly reachable via TVM native contracts from any smart contract in a single transaction, and no minimum holding time is enforced for unlocked delegations (`lock=false`, the default `DelegateResourceContract.lockPeriod`/`lock` fields), as shown by the validate() logic that only checks a lock-expiry timestamp for *locked* delegations. [9](#0-8) 
Exploitation requires the attacker to control (or be) both the delegating owner and the receiver, or to target a receiver whose `AllFrozenBalanceForEnergy`/`ForBandwidth` is small enough that a large flash delegation dominates the ratio - a condition an attacker can engineer.

### Recommendation
- Base the `transferUsage` ratio calculation on the receiver's frozen balance *before* the current transaction's delegation (e.g., a balance snapshot at the start of the transaction/block), rather than the instantaneous value that includes same-transaction delegation.
- Alternatively, disallow delegating and undelegating the same resource to/from the same receiver within the same transaction or block (a per-block/tx cooldown), similar to how many DeFi protocols block flash mint-then-burn or flash supply-then-withdraw patterns.
- Consider capping `transferUsage` proportionally to time held, not just to instantaneous balance ratio, so a zero-duration delegation cannot transfer a large usage delta.

### Proof of Concept
1. Deploy an attacker smart contract `Attacker` that, within a single external transaction:
   - Calls the native `DelegateResource` (via the `delegateresource` TVM precompile call surfaced in `Program.java`) to delegate a very large ENERGY balance from attacker-controlled account `A` to receiver `R` (attacker also controls or colludes with `R`), inflating `R.getAllFrozenBalanceForEnergy()`.
   - Immediately calls the native `UnDelegateResource` to withdraw the same balance back from `R` to `A` in the same transaction.
2. In `UnDelegateResourceProcessor.execute`, `unDelegateBalance / receiverCapsule.getAllFrozenBalanceForEnergy()` is now close to 1 because the attacker's flash delegation dominates `R`'s momentary frozen balance.
3. `transferUsage = receiverCapsule.getEnergyUsage() * ratio` transfers nearly all of `R`'s recorded energy usage away, and `receiverCapsule.setEnergyUsage(newEnergyUsage)` resets `R`'s usage to near zero. [10](#0-9) 
4. `R` can now issue further calls consuming energy up to its full `calculateGlobalEnergyLimit`, as if it had never previously consumed energy in the current window, even though no legitimate resource-refresh/time-window event occurred - all within (or immediately after) the same transaction, at negligible cost (only the transaction fee), demonstrating the flash-manipulation of shared usage-window accounting.

Note: I was unable to fully inspect `Program.java`'s exact opcode wiring for these two native contracts (only confirmed via `grep` that both classes are referenced there) due to remaining iteration constraints; a background engineer should verify the exact TVM opcode/precompile address and gas cost to fully confirm same-transaction reachability from a contract call.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L117-144)
```java
  public void execute(DelegateResourceParam param, Repository repo) {
    byte[] ownerAddress = param.getOwnerAddress();
    AccountCapsule ownerCapsule = repo.getAccount(param.getOwnerAddress());
    long delegateBalance = param.getDelegateBalance();
    byte[] receiverAddress = param.getReceiverAddress();

    // delegate resource to receiver
    switch (param.getResourceType()) {
      case BANDWIDTH:
        delegateResource(ownerAddress, receiverAddress, true,
            delegateBalance, repo);

        ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(delegateBalance);
        ownerCapsule.addFrozenBalanceForBandwidthV2(-delegateBalance);
        break;
      case ENERGY:
        delegateResource(ownerAddress, receiverAddress, false,
            delegateBalance, repo);

        ownerCapsule.addDelegatedFrozenV2BalanceForEnergy(delegateBalance);
        ownerCapsule.addFrozenBalanceForEnergyV2(-delegateBalance);
        break;
      default:
        logger.debug("Resource Code Error.");
    }

    repo.updateAccount(ownerCapsule.createDbKey(), ownerCapsule);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L91-123)
```java
  public void execute(UnDelegateResourceParam param, Repository repo) {
    byte[] ownerAddress = param.getOwnerAddress();
    byte[] receiverAddress = param.getReceiverAddress();
    long unDelegateBalance = param.getUnDelegateBalance();
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddress);
    AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    long now = repo.getHeadSlot();

    long transferUsage = 0;
    // modify receiver Account
    if (receiverCapsule != null) {
      switch (param.getResourceType()) {
        case BANDWIDTH:
          BandwidthProcessor bandwidthProcessor = new BandwidthProcessor(ChainBaseManager.getInstance());
          bandwidthProcessor.updateUsageForDelegated(receiverCapsule);
          /* For example, in a scenario where a regular account can be upgraded to a contract
          account through an interface, the account information will be cleared after the
          contract suicide, and this account will be converted to a regular account in the future */
          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForBandwidth()
              < unDelegateBalance) {
            // A TVM contract suicide, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForBandwidth(0);
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalNetLimit() / repo.getTotalNetWeight());
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage, VMConfig.disableJavaLangMath());

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L129-152)
```java
        case ENERGY:
          EnergyProcessor energyProcessor =
              new EnergyProcessor(dynamicStore, ChainBaseManager.getInstance().getAccountStore());
          energyProcessor.updateUsage(receiverCapsule);

          if (receiverCapsule.getAcquiredDelegatedFrozenV2BalanceForEnergy()
              < unDelegateBalance) {
            // A TVM contract receiver, re-create will produce this situation
            receiverCapsule.setAcquiredDelegatedFrozenV2BalanceForEnergy(0);
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalEnergyCurrentLimit() / repo.getTotalEnergyWeight());
            transferUsage = (long) (receiverCapsule.getEnergyUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForEnergy()));
            transferUsage = min(unDelegateMaxUsage, transferUsage, VMConfig.disableJavaLangMath());

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(-unDelegateBalance);
          }

          long newEnergyUsage = receiverCapsule.getEnergyUsage() - transferUsage;
          receiverCapsule.setEnergyUsage(newEnergyUsage);
          receiverCapsule.setLatestConsumeTimeForEnergy(now);
          break;
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L255-304)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule unlockResourceCapsule = delegatedResourceStore.get(key);
    byte[] lockKey = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, true);
    DelegatedResourceCapsule lockResourceCapsule = delegatedResourceStore.get(lockKey);
    if (unlockResourceCapsule == null && lockResourceCapsule == null) {
      throw new ContractValidateException(
          "delegated Resource does not exist");
    }

    long unDelegateBalance = unDelegateResourceContract.getBalance();
    if (unDelegateBalance <= 0) {
      throw new ContractValidateException("unDelegateBalance must be more than 0 TRX");
    }
    switch (unDelegateResourceContract.getResource()) {
      case BANDWIDTH: {
        long delegateBalance = 0;
        if (unlockResourceCapsule != null) {
          delegateBalance += unlockResourceCapsule.getFrozenBalanceForBandwidth();
        }
        if (lockResourceCapsule != null
            && lockResourceCapsule.getExpireTimeForBandwidth() < now) {
          delegateBalance += lockResourceCapsule.getFrozenBalanceForBandwidth();
        }
        if (delegateBalance < unDelegateBalance) {
          throw new ContractValidateException(
              "insufficient delegatedFrozenBalance(BANDWIDTH), request="
                  + unDelegateBalance + ", unlock_balance=" + delegateBalance);
        }
      }
      break;
      case ENERGY: {
        long delegateBalance = 0;
        if (unlockResourceCapsule != null) {
          delegateBalance += unlockResourceCapsule.getFrozenBalanceForEnergy();
        }
        if (lockResourceCapsule != null
            && lockResourceCapsule.getExpireTimeForEnergy() < now) {
          delegateBalance += lockResourceCapsule.getFrozenBalanceForEnergy();
        }
        if (delegateBalance < unDelegateBalance) {
          throw new ContractValidateException("insufficient delegateFrozenBalance(Energy), request="
              + unDelegateBalance + ", unlock_balance=" + delegateBalance);
        }
      }
      break;
      default:
        throw new ContractValidateException(
            "ResourceCode error.valid ResourceCode[BANDWIDTH、Energy]");
    }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L190-220)
```java
  public void unDelegateIncrease(AccountCapsule owner, final AccountCapsule receiver,
      long transferUsage, ResourceCode resourceCode, long now) {
    if (dynamicPropertiesStore.supportAllowCancelAllUnfreezeV2()) {
      unDelegateIncreaseV2(owner, receiver, transferUsage, resourceCode, now);
      return;
    }
    long lastOwnerTime = owner.getLastConsumeTime(resourceCode);
    long ownerUsage = owner.getUsage(resourceCode);
    // Update itself first
    ownerUsage = increase(owner, resourceCode, ownerUsage, 0, lastOwnerTime, now);

    long remainOwnerWindowSize = owner.getWindowSize(resourceCode);
    long remainReceiverWindowSize = receiver.getWindowSize(resourceCode);
    remainOwnerWindowSize = remainOwnerWindowSize < 0 ? 0 : remainOwnerWindowSize;
    remainReceiverWindowSize = remainReceiverWindowSize < 0 ? 0 : remainReceiverWindowSize;

    long newOwnerUsage = ownerUsage + transferUsage;
    // mean ownerUsage == 0 and transferUsage == 0
    if (newOwnerUsage == 0) {
      owner.setNewWindowSize(resourceCode, this.windowSize);
      owner.setUsage(resourceCode, 0);
      owner.setLatestTime(resourceCode, now);
      return;
    }
    // calculate new windowSize
    long newOwnerWindowSize = getNewWindowSize(ownerUsage, remainOwnerWindowSize, transferUsage,
        remainReceiverWindowSize, newOwnerUsage);
    owner.setNewWindowSize(resourceCode, newOwnerWindowSize);
    owner.setUsage(resourceCode, newOwnerUsage);
    owner.setLatestTime(resourceCode, now);
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L222-260)
```java
  public void unDelegateIncreaseV2(AccountCapsule owner, final AccountCapsule receiver,
      long transferUsage, ResourceCode resourceCode, long now) {
    long lastOwnerTime = owner.getLastConsumeTime(resourceCode);
    long ownerUsage = owner.getUsage(resourceCode);
    // Update itself first
    ownerUsage = increase(owner, resourceCode, ownerUsage, 0, lastOwnerTime, now);
    long newOwnerUsage = ownerUsage + transferUsage;
    // mean ownerUsage == 0 and transferUsage == 0
    if (newOwnerUsage == 0) {
      owner.setNewWindowSizeV2(resourceCode, this.windowSize * WINDOW_SIZE_PRECISION);
      owner.setUsage(resourceCode, 0);
      owner.setLatestTime(resourceCode, now);
      return;
    }

    long remainOwnerWindowSizeV2 = owner.getWindowSizeV2(resourceCode);
    long remainReceiverWindowSizeV2 = receiver.getWindowSizeV2(resourceCode);
    remainOwnerWindowSizeV2 = remainOwnerWindowSizeV2 < 0 ? 0 : remainOwnerWindowSizeV2;
    remainReceiverWindowSizeV2 = remainReceiverWindowSizeV2 < 0 ? 0 : remainReceiverWindowSizeV2;

    // calculate new windowSize
    long newOwnerWindowSize;
    if (hardenCalculation()) {
      BigInteger bi = BigInteger.valueOf(ownerUsage)
          .multiply(BigInteger.valueOf(remainOwnerWindowSizeV2))
          .add(BigInteger.valueOf(transferUsage)
              .multiply(BigInteger.valueOf(remainReceiverWindowSizeV2)));
      newOwnerWindowSize = divideCeilExact(bi, BigInteger.valueOf(newOwnerUsage));
    } else {
      newOwnerWindowSize = divideCeil(
          ownerUsage * remainOwnerWindowSizeV2 + transferUsage * remainReceiverWindowSizeV2,
          newOwnerUsage);
    }
    newOwnerWindowSize = min(newOwnerWindowSize, this.windowSize * WINDOW_SIZE_PRECISION,
        this.disableJavaLangMath());
    owner.setNewWindowSizeV2(resourceCode, newOwnerWindowSize);
    owner.setUsage(resourceCode, newOwnerUsage);
    owner.setLatestTime(resourceCode, now);
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

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L432-453)
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
```
