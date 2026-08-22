Found `DelegateResourceProcessor.execute()` (`actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java:117-144`) which is reachable directly from an unprivileged TVM `DelegateResource` precompiled/native contract call. Unlike every other resource-mutation path I checked (`FreezeBalanceProcessor`, `FreezeBalanceV2Processor`, `UnfreezeBalanceProcessor`, `UnfreezeBalanceV2Processor`, `UnDelegateResourceProcessor`, `CancelAllUnfreezeV2Processor`), it never calls `repo.addTotalNetWeight(...)` / `repo.addTotalEnergyWeight(...)`.

### Title
Missing total resource weight update in DelegateResourceProcessor.execute - (File: actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java)

### Summary
`DelegateResourceProcessor.execute` moves an account's own frozen balance into "delegated" frozen balance (`addFrozenBalanceForBandwidthV2` / `addFrozenBalanceForEnergyV2` with a negative delta, plus `addDelegatedFrozenV2BalanceForBandwidth/Energy` on the owner and `addAcquiredDelegatedFrozenV2BalanceForBandwidth/Energy` on the receiver), but it never recomputes or adjusts `DynamicPropertiesStore`'s `TOTAL_NET_WEIGHT` / `TOTAL_ENERGY_WEIGHT`, unlike every sibling processor that mutates frozen balances.

### Finding Description
`DelegateResourceProcessor.execute` (lines 117-144) performs: [1](#0-0) 
For BANDWIDTH it calls `delegateResource(...)` then `ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(delegateBalance)` and `ownerCapsule.addFrozenBalanceForBandwidthV2(-delegateBalance)`; ENERGY is symmetric. There is no corresponding `repo.addTotalNetWeight(...)` or `repo.addTotalEnergyWeight(...)` call anywhere in this class.

Compare this to the analogous, correctly-implemented processors that mutate the same `frozenV2` fields and always keep `TotalNetWeight`/`TotalEnergyWeight` in sync via an old/new-weight delta pattern: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 
`UnfreezeBalanceProcessor.java` even has an explicit code comment "adjust total resource, used to be a bug here" documenting that this exact class of bug was previously fixed for that path: [6](#0-5) 

This exactly matches the Sherlock bug-class: a state-transition function mutates the per-account "raw" balances that determine a cached, network-wide aggregate value (`meta.tuneIntervalCapacity`/`tuneBelowCapacity` in the original report ⇔ `TOTAL_NET_WEIGHT`/`TOTAL_ENERGY_WEIGHT` here), but forgets to update that cached aggregate, leaving it permanently stale relative to the sum of actual per-account frozen balances.

Note: since `DelegateResource` only *moves* balance between "self-frozen" and "delegated-frozen" categories on the *same* account (owner's frozen balance decreases by exactly the amount the owner's delegated balance increases), the owner's own contribution to global weight should be unchanged — that part is correct not to touch total weight. However, `addAcquiredDelegatedFrozenV2BalanceForBandwidth/Energy` on the **receiver** account increases the receiver's `getFrozenV2BalanceWithDelegated(...)`, and nothing decreases it elsewhere to compensate, since `TotalNetWeight`/`TotalEnergyWeight` are computed as sums that should stay invariant across a pure transfer. I could not fully confirm from the indexed code whether `TotalNetWeight`/`TotalEnergyWeight` are defined as "sum of `getFrozenV2BalanceWithDelegated()`" across all accounts, or as "sum of self-frozen only" (in which case delegation legitimately requires no update, as the amount is merely re-labeled as delegated, still backed by the same underlying TRX). Given the other processors' pattern of tracking `getFrozenV2BalanceWithDelegated()` deltas specifically (which include delegated balances), and that `DelegateResourceProcessor` is the sole outlier that skips this bookkeeping entirely, this looks like an inconsistency, but I was not able to fully trace whether it causes measurable double counting or under-counting without deeper analysis of `getFrozenV2BalanceWithDelegated()`'s exact formula and where else `TotalNetWeight`/`TotalEnergyWeight` get reconciled (e.g., during witness reward maintenance cycles).

### Impact Explanation
If confirmed, this would corrupt `TOTAL_NET_WEIGHT`/`TOTAL_ENERGY_WEIGHT`, which are consensus-relevant dynamic properties used to compute per-account bandwidth/energy limits (`getFrozenV2BalanceWithDelegated`-based weight is proportional to `TotalNetLimit * (accountWeight / TotalNetWeight)`, per the standard java-tron bandwidth/energy model). A stale aggregate would cause systemic mis-allocation of free/frozen bandwidth and energy across all accounts network-wide — potentially a resource-accounting/DoS-class issue reachable via an unprivileged, ordinary `DelegateResourceContract`/native-contract call.

### Likelihood Explanation
Medium confidence only. The delegation path is a pure re-categorization (frozen → delegated-frozen) on the owner side and only touches an "acquired" field on the receiver side; whether omitting the total-weight update is a bug or intentional (because `TotalNetWeight`/`TotalEnergyWeight` might only track self-frozen, not delegated, balances) could not be fully verified against the exact formula of `getFrozenV2BalanceWithDelegated()` and against `Repository.addTotalNetWeight`/`addTotalEnergyWeight` call sites in the FreezeBalanceV2Processor pattern, which does include delegated balance in its "new weight" calculation. Given the index size limits, I was unable to pull the full body of `AccountCapsule.getFrozenV2BalanceWithDelegated` or exhaustively confirm there is no compensating adjustment elsewhere (e.g. in `UnDelegateResourceProcessor`, which does adjust weight on the reverse operation, suggesting weight bookkeeping is expected to be symmetric for delegate/undelegate).

### Recommendation
Start a Devin session with full repository access to: (1) read the exact definition of `AccountCapsule.getFrozenV2BalanceWithDelegated`, (2) trace every call site of `addTotalNetWeight`/`addTotalEnergyWeight` including in `DelegateResourceActuator` (the legacy, non-native-contract path) to see if it performs the update that `DelegateResourceProcessor` (native/TVM path) omits, and (3) if the legacy actuator does update total weight on delegate but the native-contract processor does not, add the missing `repo.addTotalNetWeight(...)`/`repo.addTotalEnergyWeight(...)` calls to `DelegateResourceProcessor.execute` to match, mirroring the delta pattern already used in `UnDelegateResourceProcessor`.

### Proof of Concept
Not able to construct a concrete PoC without confirming the exact `TotalNetWeight`/`TotalEnergyWeight` semantics described above; this requires deeper repository access (full file bodies for `AccountCapsule` and `DelegateResourceActuator`) beyond what the current index exposes, given the size limits mentioned in my instructions. Recommend a Devin session with full codebase access to confirm root cause before treating this as a confirmed vulnerability.

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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceV2Processor.java (L78-90)
```java
    switch (param.getResourceType()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(frozenBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        repo.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(frozenBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        repo.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L178-194)
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
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/CancelAllUnfreezeV2Processor.java (L72-97)
```java
  public void updateFrozenInfoAndTotalResourceWeight(
      AccountCapsule accountCapsule, Protocol.Account.UnFreezeV2 unFreezeV2, Repository repo) {
    switch (unFreezeV2.getType()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(unFreezeV2.getUnfreezeAmount());
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        repo.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(unFreezeV2.getUnfreezeAmount());
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        repo.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(unFreezeV2.getUnfreezeAmount());
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        repo.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        // this should never happen
        break;
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L160-193)
```java
    // modify owner Account
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule delegatedResourceCapsule = repo.getDelegatedResource(key);
    switch (param.getResourceType()) {
      case BANDWIDTH: {
        delegatedResourceCapsule.addFrozenBalanceForBandwidth(-unDelegateBalance, 0);

        ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
        ownerCapsule.addFrozenBalanceForBandwidthV2(unDelegateBalance);

        BandwidthProcessor processor = new BandwidthProcessor(ChainBaseManager.getInstance());
        if (Objects.nonNull(receiverCapsule) && transferUsage > 0) {
          processor.unDelegateIncrease(ownerCapsule, receiverCapsule,
              transferUsage, BANDWIDTH, now);
        }
      }
      break;
      case ENERGY: {
        delegatedResourceCapsule.addFrozenBalanceForEnergy(-unDelegateBalance, 0);

        ownerCapsule.addDelegatedFrozenV2BalanceForEnergy(-unDelegateBalance);
        ownerCapsule.addFrozenBalanceForEnergyV2(unDelegateBalance);

        EnergyProcessor processor =
            new EnergyProcessor(dynamicStore, ChainBaseManager.getInstance().getAccountStore());
        if (Objects.nonNull(receiverCapsule) && transferUsage > 0) {
          processor.unDelegateIncrease(ownerCapsule, receiverCapsule, transferUsage, ENERGY, now);
        }
      }
      break;
      default:
        //this should never happen
        break;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L190-201)
```java
    // adjust total resource, used to be a bug here
    switch (param.getResourceType()) {
      case BANDWIDTH:
        repo.addTotalNetWeight(-unfreezeBalance / TRX_PRECISION);
        break;
      case ENERGY:
        repo.addTotalEnergyWeight(-unfreezeBalance / TRX_PRECISION);
        break;
      default:
        //this should never happen
        break;
    }
```
