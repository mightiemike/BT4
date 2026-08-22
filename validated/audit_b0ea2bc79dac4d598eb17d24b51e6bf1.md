### Title
Global resource-weight counter (`TotalNetWeight`/`TotalEnergyWeight`/`TotalTronPowerWeight`) can drift and go negative, corrupting network-wide bandwidth/energy limit accounting — analog of the CL-pool `feeGrowth` underflow ([File: chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java])

### Summary
The Sushi Trident bug arises because a global accumulator (`feeGrowthGlobal`) is compared/subtracted against two independently-updated per-position counters (`feeGrowthOutside0`/`feeGrowthOutside1`) whose updates are not kept perfectly in sync with the crossing logic, so the invariant `feeGrowthBelow + feeGrowthAbove <= feeGrowthGlobal` can be violated, causing reverts that lock user funds. The closest reachable analog in java-tron is the global resource-weight accounting (`TotalNetWeight`, `TotalEnergyWeight`, `TotalTronPowerWeight`) in `DynamicPropertiesStore`, which is incremented/decremented from many independent, unprivileged actuator paths (freeze/unfreeze v1 and v2, delegate/undelegate, TVM native freeze contracts, contract suicide) using differing formulas depending on feature-flag state, with only conditional clamping to zero.

### Finding Description
`DynamicPropertiesStore.addTotalNetWeight`, `addTotalEnergyWeight`, and `addTotalTronPowerWeight` clamp the resulting value to a minimum of `0` **only when `allowNewReward()` is enabled**: [1](#0-0) 

These global counters are decremented from many independent unprivileged, transaction-triggered code paths that compute the "decrease" amount with different formulas depending on chain feature flags:
- Legacy v1 unfreeze uses `dynamicStore.allowNewReward() ? decrease : -unfreezeBalance / TRX_PRECISION` as the weight delta, i.e. two structurally different computations selected by a global flag: [2](#0-1) 
- v1 delegated-resource unfreeze computes `decrease` from a receiver-side "old vs new weight" delta that is itself branch-dependent on `getAllowTvmSolidity059`/`getAllowTvmConstantinople`: [3](#0-2) 
- v2 unfreeze, undelegate, cancel-all-unfreeze-v2 and TVM-native unfreeze processors each independently recompute `oldWeight`/`newWeight` from the account's frozen-balance fields and feed the delta into the same global accumulator: [4](#0-3) [5](#0-4) [6](#0-5) 
- Contract self-destruct also feeds an independently-derived decrement into the same global weight, bypassing the actuator-level "decrease" computation entirely: [7](#0-6) 

Because each of these code paths derives its delta from a *local* view of a specific account's frozen-balance fields at a *specific point in time*, and only some of them are protected by the `allowNewReward()` clamp, the global aggregate (`TotalNetWeight`/`TotalEnergyWeight`/`TotalTronPowerWeight`) is not guaranteed to equal the true sum of all accounts' weights — structurally the same class of bug as feeGrowthGlobal vs. feeGrowthOutside0/1: a single global counter maintained via multiple independently-updated deltas whose sum-invariant is not enforced by any single source of truth.

### Impact Explanation
`TotalNetWeight` / `TotalEnergyWeight` are used as the divisor when computing every account's per-block bandwidth/energy limit (e.g. `dynamicStore.getTotalNetLimit() / dynamicStore.getTotalNetWeight()` seen throughout `DelegateResourceProcessor`, `UnDelegateResourceActuator`, `UnDelegateResourceProcessor`): [8](#0-7) [9](#0-8) 

If this accumulator drifts to an incorrect (too small, zero, or theoretically negative when `allowNewReward()` is disabled) value through the combination of independently-computed deltas across the many call sites above, every account's resource-limit computation network-wide becomes corrupted — either granting excess bandwidth/energy (free resource abuse / consensus-relevant accounting corruption) or starving accounts of their entitled resource (denial of service), reachable purely by broadcasting ordinary Freeze/Unfreeze/Delegate/UnDelegate transactions or by TVM contracts self-destructing. This matches the "resource and reward accounting" and "consensus divergence / DoS via protocol implementation" categories explicitly in scope.

### Likelihood Explanation
Medium. Triggering requires driving the accounts and code paths into states where the "old weight" and "new weight" are computed from stale or divergent account fields (e.g., legacy v1 vs v2 freeze migration, `oldTronPowerIsInvalid()`/`getAllowTvmSolidity059()` branch mismatches, or contract suicide bypassing the standard decrement path) across chain upgrades where the relevant feature flags (`allowNewReward`, `supportAllowNewResourceModel`, `allowTvmSolidity059`) transition. This is analogous to the Sushi report's own acknowledgment that the concrete PoC needed correction while the underlying issue (two independently-maintained counters lacking an invariant check) was accepted as valid. I was not able to fully trace every legacy/v2 migration transition combination within the available tool budget, so I cannot provide a fully worked, step-by-step numeric PoC establishing a definite drift — this should be verified with a live Devin session that can build/run java-tron and fuzz the freeze/unfreeze/delegate/suicide sequences across flag combinations.

### Recommendation
Enforce the zero-floor clamp (`max(0, ...)`) unconditionally in `addTotalNetWeight`/`addTotalEnergyWeight`/`addTotalTronPowerWeight` regardless of `allowNewReward()`, and, more importantly, replace the pattern of "compute weight delta independently in N call sites" with a single authoritative recomputation function invoked by every freeze/unfreeze/delegate/undelegate/suicide path, so the global aggregate can never diverge from the sum of per-account weights. Add invariant assertions (e.g., periodic full recomputation and comparison against the incrementally-maintained value in maintenance/cycle-boundary code) to detect drift early, analogous to how the Sushi fix required correcting the fee-growth invariant check itself rather than patching individual crossing sites.

### Proof of Concept
Not independently reproduced within the available tool budget (read-only code search; no build/execution environment). A concrete PoC would need to:
1. Freeze balance for BANDWIDTH/ENERGY under the legacy (v1) model.
2. Trigger the network upgrade sequence that flips `allowNewReward`, `supportAllowNewResourceModel`, and `allowTvmSolidity059` between the freeze and unfreeze operations.
3. Unfreeze via the v1 `UnfreezeBalanceActuator` (which selects its weight-delta formula based on the *current* `allowNewReward()` value rather than the value at freeze time) and observe divergence between `DynamicPropertiesStore.getTotalNetWeight()`/`getTotalEnergyWeight()` and the true sum of all accounts' frozen balances.
This should be executed and confirmed in a Devin session with a running java-tron test harness (as already partially covered by `UnfreezeBalanceActuatorTest`/`UnfreezeBalanceV2ActuatorTest`/`FreezeV2Test`) before treating this as a confirmed exploit rather than a structural analog.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2269-2293)
```java
  //The unit is trx
  public void addTotalNetWeight(long amount) {
    if (amount == 0) {
      return;
    }
    long totalNetWeight = getTotalNetWeight();
    totalNetWeight += amount;
    if (allowNewReward()) {
      totalNetWeight = max(0, totalNetWeight, disableJavaLangMath());
    }
    saveTotalNetWeight(totalNetWeight);
  }

  //The unit is trx
  public void addTotalEnergyWeight(long amount) {
    if (amount == 0) {
      return;
    }
    long totalEnergyWeight = getTotalEnergyWeight();
    totalEnergyWeight += amount;
    if (allowNewReward()) {
      totalEnergyWeight = max(0, totalEnergyWeight, disableJavaLangMath());
    }
    saveTotalEnergyWeight(totalEnergyWeight);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L113-150)
```java
      AccountCapsule receiverCapsule = accountStore.get(receiverAddress);

      if (dynamicStore.getAllowTvmConstantinople() == 0 ||
          (receiverCapsule != null && receiverCapsule.getType() != AccountType.Contract)) {
        switch (unfreezeBalanceContract.getResource()) {
          case BANDWIDTH:
            long oldNetWeight = receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth() / 
                    TRX_PRECISION;
            if (dynamicStore.getAllowTvmSolidity059() == 1
                && receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth()
                < unfreezeBalance) {
              oldNetWeight = unfreezeBalance / TRX_PRECISION;
              receiverCapsule.setAcquiredDelegatedFrozenBalanceForBandwidth(0);
            } else {
              receiverCapsule.addAcquiredDelegatedFrozenBalanceForBandwidth(-unfreezeBalance);
            }
            long newNetWeight = receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth() / 
                    TRX_PRECISION;
            decrease = newNetWeight - oldNetWeight;
            break;
          case ENERGY:
            long oldEnergyWeight = receiverCapsule.getAcquiredDelegatedFrozenBalanceForEnergy() / 
                    TRX_PRECISION;
            if (dynamicStore.getAllowTvmSolidity059() == 1
                && receiverCapsule.getAcquiredDelegatedFrozenBalanceForEnergy() < unfreezeBalance) {
              oldEnergyWeight = unfreezeBalance / TRX_PRECISION;
              receiverCapsule.setAcquiredDelegatedFrozenBalanceForEnergy(0);
            } else {
              receiverCapsule.addAcquiredDelegatedFrozenBalanceForEnergy(-unfreezeBalance);
            }
            long newEnergyWeight = receiverCapsule.getAcquiredDelegatedFrozenBalanceForEnergy() / 
                    TRX_PRECISION;
            decrease = newEnergyWeight - oldEnergyWeight;
            break;
          default:
            //this should never happen
            break;
        }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L241-260)
```java
    }
    
    long weight = dynamicStore.allowNewReward() ? decrease : -unfreezeBalance / TRX_PRECISION;
    switch (unfreezeBalanceContract.getResource()) {
      case BANDWIDTH:
        dynamicStore
            .addTotalNetWeight(weight);
        break;
      case ENERGY:
        dynamicStore
            .addTotalEnergyWeight(weight);
        break;
      case TRON_POWER:
        dynamicStore
            .addTotalTronPowerWeight(weight);
        break;
      default:
        //this should never happen
        break;
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L274-301)
```java
  public void updateTotalResourceWeight(AccountCapsule accountCapsule,
                                        final UnfreezeBalanceV2Contract unfreezeBalanceV2Contract,
                                        long unfreezeBalance) {
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    switch (unfreezeBalanceV2Contract.getResource()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(-unfreezeBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        dynamicStore.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(-unfreezeBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        dynamicStore.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(-unfreezeBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        dynamicStore.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        //this should never happen
        break;
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L178-205)
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
  }
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

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L588-609)
```java
  private void transferDelegatedResourceToInheritor(byte[] ownerAddr, byte[] inheritorAddr, Repository repo) {

    // delegated resource from sender to owner, just abandon
    // in order to making that sender can unfreeze their balance in future
    // nothing will be deleted

    // delegated resource from owner to receiver
    // there cannot be any resource when suicide

    AccountCapsule ownerCapsule = repo.getAccount(ownerAddr);

    // transfer owner`s frozen balance for bandwidth to inheritor
    long frozenBalanceForBandwidthOfOwner = 0;
    // check if frozen for bandwidth exists
    if (ownerCapsule.getFrozenCount() != 0) {
      frozenBalanceForBandwidthOfOwner = ownerCapsule.getFrozenList().get(0).getFrozenBalance();
    }
    repo.addTotalNetWeight(-frozenBalanceForBandwidthOfOwner / TRX_PRECISION);

    long frozenBalanceForEnergyOfOwner =
        ownerCapsule.getAccountResource().getFrozenBalanceForEnergy().getFrozenBalance();
    repo.addTotalEnergyWeight(-frozenBalanceForEnergyOfOwner / TRX_PRECISION);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L63-66)
```java
        long netUsage = (long) (ownerCapsule.getNetUsage() * TRX_PRECISION * ((double)
            (repo.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));

        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage, disableJavaLangMath);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L116-117)
```java
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalNetLimit() / repo.getTotalNetWeight());
```
