### Title
Rounding-down of proportional `transferUsage` in `UnDelegateResourceActuator`/`UnDelegateResourceProcessor` lets a user un-delegate resource in small increments to bypass usage reallocation, permanently desynchronizing bandwidth/energy accounting between owner and receiver - (File: `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java`, `actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java`)

### Summary
Both the legacy actuator and the TVM native-contract path for `UnDelegateResource` compute the amount of consumed bandwidth/energy usage (`transferUsage`) that must move from the receiver back to the owner, proportional to the arbitrary, caller-chosen `unDelegateBalance`. The calculation truncates (floors) toward zero via a `double`→`long` cast, exactly like the `virtualRewardsToRemove` rounding in the referenced `StakingRewards.sol` finding. By repeatedly un-delegating in small enough increments, the caller can keep `transferUsage` at `0` on every call while still fully reclaiming 100% of the delegated TRX balance, permanently skipping the usage-reallocation step.

### Finding Description
In `UnDelegateResourceActuator.execute`: [1](#0-0) 

and identically in the TVM path `UnDelegateResourceProcessor.execute`: [2](#0-1) 

`transferUsage` is computed as:
```java
transferUsage = (long) (receiverCapsule.getNetUsage()
    * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
```
This is a direct structural analog of the `StakingRewards._decreaseUserShare` bug: a proportional quantity (`virtualRewardsToRemove` there, `transferUsage` here) is derived from a ratio of a user-controlled, arbitrarily small "decrease amount" (`decreaseShareAmount` there, `unDelegateBalance` here) over a running total (`user.userShare` there, `receiverCapsule.getAllFrozenBalanceForBandwidth()` here), then truncated toward zero. The only guard on `unDelegateBalance` is `unDelegateBalance > 0` (see validate methods), exactly like Salty's `decreaseShareAmount != 0` guard - there is no minimum-size check that would prevent the ratio from truncating to `0`.

Whenever `transferUsage` computes to `0`, the subsequent transfer step is skipped entirely: [3](#0-2) 
`if (Objects.nonNull(receiverCapsule) && transferUsage > 0) { processor.unDelegateIncrease(...); }` - so neither `receiverCapsule.netUsage` is decremented, nor is `ownerCapsule.netUsage`/window-size updated via `unDelegateIncrease`, even though `unDelegateBalance` (and the backing `AcquiredDelegatedFrozenV2Balance...`) is unconditionally reduced on the receiver: [4](#0-3) 

Because the balance-side reduction (`addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance)`) happens unconditionally and cumulatively (so it is exact across N partial calls), while the usage-side reduction (`transferUsage`) is recomputed independently each call from the *current* (shrinking) `getAllFrozenBalanceForBandwidth()` denominator and floored each time, the two accounting legs drift apart with every sufficiently small call. An owner can drain the entire delegated balance via many small `unDelegateBalance` calls chosen so that `receiverCapsule.getNetUsage() * unDelegateBalance / receiverCapsule.getAllFrozenBalanceForBandwidth()` is `< 1` every time, ending with the owner having reclaimed 100% of the frozen TRX/resource capacity while `receiverCapsule.netUsage` (and correspondingly the owner's own usage/window-size) was never correspondingly adjusted at all.

### Impact Explanation
This produces a persistent, unrecoverable divergence between an account's tracked resource *usage* (`netUsage`/`energyUsage`, and the derived `windowSize`/`windowSizeV2`) and its backing frozen/delegated balance, which underlies bandwidth/energy availability calculations, e.g. in `BandwidthProcessor`/`EnergyProcessor`. The victim receiver is left holding "usage debt" that should have followed the reclaimed capital back to the owner, while the owner escapes taking on the corresponding usage liability it should inherit. This is an invalid-state/divergence bug in on-chain resource accounting reachable by any unprivileged account performing an ordinary `UnDelegateResourceContract` (or TVM `unDelegateResource`) call, satisfying the "invalid-state/divergence" impact category (analogous to the value-extraction impact of the original finding, but manifesting as a permanent bookkeeping desync of a public, priced resource - bandwidth/energy - rather than a token transfer).

### Likelihood Explanation
Likelihood is high: `unDelegateBalance` is fully attacker-controlled with only a `> 0` validation check (mirroring Salty's `!= 0` check called out in the original report), and no minimum threshold is enforced relative to `receiverCapsule.getAllFrozenBalanceForBandwidth()`/`getAllFrozenBalanceForEnergy()`. Any account that has delegated resource to a receiver with a sufficiently large frozen-balance denominator (a very common, unprivileged, normal-usage scenario) can trivially choose small `unDelegateBalance` values across multiple transactions to keep `transferUsage` at `0` throughout the drain.

### Recommendation
Round the `transferUsage` (and the analogous `unDelegateMaxUsage`) calculations up (ceiling) rather than down, consistent with the "round in favor of the protocol/other party" mitigation applied for `virtualRewardsToRemove` in the referenced Salty fix, or use exact `BigInteger` arithmetic (as already done elsewhere in this codebase, e.g. `ResourceProcessor.divideCeilExact`) so the sum of `transferUsage` across any sequence of partial `unDelegateResource` calls converges to the same result as a single full-balance call.

### Proof of Concept
1. Owner delegates a large `frozenBalance` of BANDWIDTH resource to a receiver; receiver accumulates non-trivial `netUsage` from consuming that bandwidth.
2. Owner repeatedly calls `UnDelegateResourceContract` (or TVM `unDelegateResource`) with a very small `unDelegateBalance` each time (e.g. such that `receiverCapsule.getNetUsage() * unDelegateBalance / receiverCapsule.getAllFrozenBalanceForBandwidth() < 1` on every call - see the floor computation at `UnDelegateResourceProcessor.java:118-119`).
3. On every call, `transferUsage == 0`, so the `if (transferUsage > 0)` guard at `UnDelegateResourceProcessor.java:171` skips `unDelegateIncrease`, meaning `receiverCapsule.netUsage` is never decremented and the owner's usage/window-size is never incremented, while `receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance)` (line 122) and the owner's `addFrozenBalanceForBandwidthV2(unDelegateBalance)` (line 168) execute unconditionally each time.
4. After enough repetitions, the owner has fully reclaimed the entire delegated balance, but `receiverCapsule.netUsage` remains exactly as inflated as before any un-delegation occurred - demonstrating the permanent usage/balance accounting divergence.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L80-88)
```java
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalNetLimit()) / dynamicStore.getTotalNetWeight()));
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L170-187)
```java
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
```
