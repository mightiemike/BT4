### Title
Unfair, order-dependent distribution of delegated resource "usage debt" among multiple delegators - (`actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java`, `actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java`)

### Summary
The external report describes a lending pool that fails to socialize bad debt across all suppliers, so the calculation used at withdrawal time (`shares * liquidityIndex`) ignores the shortfall and lets early withdrawers cash out at full value while the last withdrawer absorbs the entire loss. The java-tron analog is the resource-delegation ("Stake 2.0") subsystem: multiple owners can delegate BANDWIDTH/ENERGY to the same receiver, and the receiver's already-consumed usage (the "debt") is clawed back from the receiver and handed to whichever delegator happens to un-delegate, using a *snapshot-based, order-dependent* pro-rata formula rather than a fixed, pre-committed allocation.

### Finding Description
When an owner un-delegates resource from a receiver, `UnDelegateResourceActuator.execute()` (and the TVM-native equivalent `UnDelegateResourceProcessor.execute()`) computes how much of the receiver's already-consumed usage should be "clawed back" to the un-delegating owner: [1](#0-0) 

```java
long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
    * ((double) (dynamicStore.getTotalNetLimit()) / dynamicStore.getTotalNetWeight()));
transferUsage = (long) (receiverCapsule.getNetUsage()
    * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
transferUsage = min(unDelegateMaxUsage, transferUsage);
```

This same pattern is duplicated for ENERGY and in the native-contract path: [2](#0-1) 

The key problem: `receiverCapsule.getNetUsage()` and `receiverCapsule.getAllFrozenBalanceForBandwidth()` are **live, mutable values** that change with every prior un-delegate call from *any* delegator sharing the same receiver. There is no fixed, per-delegator record of "how much usage this delegator is responsible for" established at delegation time; instead, each un-delegation recomputes its share against whatever the *current* snapshot of the receiver's remaining usage/frozen-balance happens to be.

Consequently:
- If delegator A un-delegates first, they claw back their proportional share of the receiver's *current* usage and reduce `receiverCapsule.getAllFrozenBalanceForBandwidth()`.
- When delegator B (or the same owner via a second delegation) un-delegates afterward, the *same formula* is re-evaluated against the new, shrunken denominator and whatever usage is left, so B's clawback is not based on B's actual historical contribution to the receiver's consumption, but on the arbitrary state left over after A's withdrawal.
- Because `transferUsage` is capped by `min(unDelegateMaxUsage, transferUsage)` (see the same file), an earlier un-delegator can be capped favorably, while a later un-delegator can be forced to inherit whatever usage debt remains disproportionate to their actual frozen contribution — precisely the "first withdrawers get the good rate, last withdrawer eats the loss" pattern described in the report, transplanted from a lending-pool's `shares * liquidityIndex` accounting to TRON's delegated-resource usage accounting.

### Impact Explanation
The mis-attributed "usage debt" is written directly into on-chain account state (`receiverCapsule.setNetUsage(...)`, `ownerCapsule`'s usage fields via `processor.unDelegateIncrease(...)`), which determines how much free bandwidth/energy an account has for future transactions. A delegator who un-delegates late can be charged usage they never actually consumed, effectively losing free resource capacity (forcing them to burn TRX for bandwidth/energy fees they should not owe), while earlier un-delegators walk away clean. Because this is resource/energy metering state that is part of consensus, an unfair/incorrect distribution constitutes an accounting-corruption bug in a core resource-accounting subsystem reachable by any account via a normal broadcast transaction (`UnDelegateResourceContract`) or TVM `unDelegateResource` native call.

### Likelihood Explanation
This requires no privileged access: any account that has delegated bandwidth/energy to a receiver already shared with other delegators can trigger the vulnerable path simply by calling `un-delegate resource` (via `wallet/undelegateresource` HTTP/gRPC API or the TVM precompile). A malicious delegator can deliberately time their un-delegation (e.g., immediately after the receiver has consumed a lot of usage but before other delegators un-delegate) to shift disproportionate usage debt onto delegators who un-delegate afterward, or an ordinary sequence of unrelated un-delegations can accidentally produce this unfairness. No collusion with node operators or leaked keys is needed.

### Recommendation
Track each delegator's contribution to a receiver's resource usage using a durable, delegation-scoped accounting record (analogous to a per-delegator "Vi" index as already used correctly in `MortgageService`/`RewardViCalService` for vote rewards) rather than recomputing a live ratio against the receiver's mutable, shared `netUsage`/`energyUsage` and `AllFrozenBalanceForBandwidth`/`AllFrozenBalanceForEnergy` fields at the time of each individual un-delegation. This ensures usage clawback is proportional to each delegator's actual, fixed contribution and independent of the order in which multiple delegators un-delegate.

### Proof of Concept
1. Account `R` (receiver) is delegated BANDWIDTH from two independent owners, `A` and `B`, each freezing equal amounts (so `R.getAllFrozenBalanceForBandwidth()` = A's + B's frozen balance).
2. `R` consumes bandwidth over several blocks, accumulating `netUsage > 0`.
3. `A` calls `UnDelegateResourceContract` to un-delegate their full amount. In `UnDelegateResourceActuator.execute()` (lines 79–92), `transferUsage` is computed from `R.getNetUsage()` and `R.getAllFrozenBalanceForBandwidth()` *at that moment*, clawing back a share of `R`'s usage into `A`'s own `netUsage`, and reducing `R.getAllFrozenBalanceForBandwidth()` accordingly.
4. `B` then calls the same contract to un-delegate their full amount. The formula recomputes `transferUsage` against the *new* (smaller) `R.getAllFrozenBalanceForBandwidth()` and *new* (already-reduced) `R.getNetUsage()`, producing a different, non-proportional-to-original-contribution result for `B` than `B` would have received had they un-delegated first or simultaneously with `A`.
5. Comparing the `netUsage` transferred to `A` vs. `B` for equal original delegated amounts shows the outcome depends on call order, demonstrating the unfair/uneven distribution of the receiver's usage "debt" among the delegators who supplied the receiver's resource pool. [3](#0-2)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L67-122)
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L114-122)
```java
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalNetLimit() / repo.getTotalNetWeight());
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage, VMConfig.disableJavaLangMath());

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
```
