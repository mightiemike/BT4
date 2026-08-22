### Title
Loss of delegated-resource deficit tracking in `UnDelegateResourceActuator`/`UnDelegateResourceProcessor` causes acquired-resource accounting corruption - (File: actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java)

### Summary
When a receiver's `AcquiredDelegatedFrozenV2Balance{Bandwidth,Energy}` is smaller than the amount being un-delegated (a legitimate edge case explicitly acknowledged in the code as happening "when a TVM contract suicides and is re-created"), the code silently clamps the field to `0` instead of tracking the negative remainder. When new delegations are subsequently made to that same receiver, `DelegateResourceActuator`/`DelegateResourceProcessor` simply add the new delegated amount on top of the already-zeroed field, with no reconciliation of the previously lost deficit. This is the same bug class as the Olympus `ohmDeployed`/`circulatingOhmBurned` issue: a running balance is clamped to zero on underflow, and a "debt" marker is either not created or, if created elsewhere, is not consumed on the next credit, so the tracked balance ends up overstating the real backing amount.

### Finding Description
In `UnDelegateResourceActuator.execute()` (and the TVM-native equivalent `UnDelegateResourceProcessor.execute()`), the receiver's acquired-delegated balance is checked against the amount being undelegated: [1](#0-0) 

If `getAcquiredDelegatedFrozenV2BalanceForBandwidth() < unDelegateBalance`, the field is force-set to `0` rather than being reduced by the actual (smaller) amount, and — critically — the shortfall (`unDelegateBalance - acquired`) is discarded entirely; no compensating "debt" state is recorded anywhere. The identical pattern exists for `ENERGY`: [2](#0-1) 

and in the TVM-callable processor used by `DelegateResourceProcessor`'s counterpart, `UnDelegateResourceProcessor.execute()`: [3](#0-2) 

Later, when a new delegation is made to the same receiver, `DelegateResourceActuator.delegateResource()` unconditionally adds the newly delegated amount to the receiver's acquired balance with no adjustment for the earlier clamp-to-zero event: [4](#0-3) 

This mirrors the Olympus Silo bug exactly: a legitimate "more was withdrawn/undelegated than was tracked" event zeroes out the running counter but drops the surplus/deficit information, and the very next credit to that same counter fails to net out the previously lost amount, permanently skewing the account's tracked resource state relative to reality.

### Impact Explanation
`AcquiredDelegatedFrozenV2BalanceForBandwidth/Energy` is used to compute `transferUsage` (proportional bandwidth/energy usage transferred back to the delegator) and is subtracted in `FreezeV2Util.getV2NetUsage`/`getV2EnergyUsage`, which feed into `queryDelegatableResource` and delegation validation (`DelegateResourceActuator.validate()`). Because the field can be silently reset to `0` and never "topped back up" to reflect the deficit, a receiver's bookkeeping under-represents (or, after subsequent re-delegations, effectively resets/erases) previously acquired resource usage attribution. This can let a malicious or buggy sequence of delegate → suicide/recreate → undelegate → re-delegate operations desynchronize the true global backing of bandwidth/energy from the value the protocol thinks is delegated to a receiver, corrupting resource/energy accounting used across the network (analogous to `maximumToDeploy`/`ohmDeployed` drift in the report) and enabling under- or over-attribution of consumed bandwidth/energy relative to the frozen TRX actually backing it.

### Likelihood Explanation
The triggering condition is explicitly called out in the code comments ("A TVM contract suicide, re-create will produce this situation"), meaning the developers know this underflow branch is reachable through ordinary, permissionless TVM contract lifecycle actions (`SUICIDE`/recreate at the same address) combined with the `DelegateResource`/`UnDelegateResource` contracts, both of which are regular broadcast transactions available to any account. No privileged role is required.

### Recommendation
Track the shortfall explicitly (e.g., an `acquiredDeficit` field per resource) whenever `AcquiredDelegatedFrozenV2Balance* < unDelegateBalance`, and have `DelegateResourceActuator`/`DelegateResourceProcessor` consume that deficit before crediting `addAcquiredDelegatedFrozenV2BalanceFor*` on subsequent delegations — the same fix pattern the report recommends: net out the pending deficit before applying new credits.

### Proof of Concept
1. Account `R` (a TVM contract) is delegated bandwidth/energy from `O` via `DelegateResourceContract`, incrementing `R.AcquiredDelegatedFrozenV2BalanceForBandwidth`.
2. `R` calls `SUICIDE` and is subsequently re-created at the same address, or otherwise reaches a state where its acquired-balance bookkeeping is smaller than what `O` later attempts to undelegate.
3. `O` calls `UnDelegateResourceContract` for an amount greater than `R`'s current `AcquiredDelegatedFrozenV2BalanceForBandwidth`; the actuator clamps the field to `0`, silently dropping the shortfall [5](#0-4) .
4. `O` (or any other delegator) delegates more resource to `R` via `DelegateResourceContract`; `R.AcquiredDelegatedFrozenV2BalanceForBandwidth` is incremented from `0` with no correction for the earlier lost deficit [4](#0-3) .
5. `R`'s tracked acquired balance now diverges from the true amount of TRX actually backing its bandwidth/energy limit, corrupting subsequent `transferUsage`/`getV2NetUsage`/`getV2EnergyUsage` calculations for `R`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L75-88)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L94-111)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L110-123)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L317-324)
```java
    //modify AccountStore for receiver
    AccountCapsule receiverCapsule = accountStore.get(receiverAddress);
    if (isBandwidth) {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(balance);
    } else {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(balance);
    }
    accountStore.put(receiverCapsule.createDbKey(), receiverCapsule);
```
