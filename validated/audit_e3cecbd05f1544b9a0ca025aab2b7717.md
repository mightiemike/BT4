### Title
Unchecked cascading subtraction in `UnDelegateResourceActuator`/`UnDelegateResourceProcessor` can drive receiver's resource usage negative - (File: actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java)

### Summary
The Suzaku report describes a cascading-subtraction pattern where an excess amount computed as a remainder is pushed into a second bucket without verifying that the second bucket can absorb it, causing `bucket - excess` to underflow. The closest reachable analog in java-tron is the bandwidth/energy usage transfer logic executed on `UnDelegateResourceContract`, where a proportionally-computed `transferUsage` is subtracted from the receiver's `netUsage`/`energyUsage` without ever checking that `transferUsage <= receiverCapsule.getNetUsage()`/`getEnergyUsage()`.

### Finding Description
In `UnDelegateResourceActuator.execute()`, when undelegating BANDWIDTH, `transferUsage` is derived from floating point proportional math and then subtracted directly from the receiver's current usage: [1](#0-0) 
The identical unchecked-subtraction pattern exists for ENERGY: [2](#0-1) 

The same logic is duplicated in the TVM-native path (`unDelegateResource` precompile), which is directly reachable by any smart contract, i.e. an unprivileged actor: [3](#0-2) [4](#0-3) 

`transferUsage` is computed as `receiverCapsule.getNetUsage() * (unDelegateBalance / receiverCapsule.getAllFrozenBalanceForBandwidth())`, a proportional split intended to move only the fraction of usage attributable to the balance being undelegated — structurally identical to the "remainder that cascades to the next bucket" pattern in the Suzaku bug. Unlike Solidity (which reverts on `uint` underflow), Java `long` subtraction silently wraps/produces a negative value instead of throwing, so `receiverCapsule.getNetUsage() - transferUsage` (or `getEnergyUsage() - transferUsage`) can be stored as a negative usage value with no runtime check anywhere in this code path or in `AccountCapsule.setNetUsage`/`setEnergyUsage`.

This class of computation is inherently fragile because:
- `unDelegateMaxUsage` and `transferUsage` are computed via `double` division/multiplication, which is not exact and can round in either direction depending on operand magnitude.
- `receiverCapsule.getAllFrozenBalanceForBandwidth()`/`getAllFrozenBalanceForEnergy()` (the denominator) is read from live, mutable account state at execution time, and can differ from what was checked at `validate()` time if it changed in between (e.g. due to a delegate/undelegate racing in the same block, or a prior op in the same transaction chain), letting the proportional fraction exceed the true bound intended when the formula was derived (`transferUsage <= netUsage`).

### Impact Explanation
Because Java does not revert on integer underflow of `long`, this does not cause a DoS as in the original Solidity report; instead it corrupts on-chain resource accounting state by producing a negative `NetUsage`/`EnergyUsage` for the receiver account. A negative usage value stored on-chain understates real resource consumption in subsequent bandwidth/energy recovery and limit calculations (`BandwidthProcessor`/`EnergyProcessor`), which use `usage` as an input to window/limit math. This can result in the affected account effectively obtaining free (underpriced) bandwidth/energy for subsequent transactions until the negative value is corrected by the natural decay/recovery formulas — an accounting/underpriced-public-work impact reachable by any unprivileged delegator/undelegator or any smart contract invoking the `unDelegateResource` precompile.

### Likelihood Explanation
Likelihood is moderate and requires reachable but non-trivial preconditions: the receiver must have non-zero `netUsage`/`energyUsage` at undelegation time and the proportional computation must round or diverge (due to double-precision arithmetic and/or a stale `AllFrozenBalanceForX` denominator relative to when the usage was measured) such that `transferUsage` exceeds the receiver's actual usage. This is far more likely to be triggered accidentally in edge cases (very small delegated balances, usage close to zero, frequent delegate/undelegate cycles) than through deliberate exploitation, but any account (owner) can trigger `UnDelegateResourceContract`/`unDelegateResource` unprivileged, making the attack surface directly reachable without special roles.

### Recommendation
Add an explicit floor check before storing the subtracted usage values in both `UnDelegateResourceActuator.execute()` and `UnDelegateResourceProcessor.execute()`, e.g.:
```java
long newNetUsage = Math.max(0, receiverCapsule.getNetUsage() - transferUsage);
```
and similarly for `newEnergyUsage`, mirroring the `max(0, ...)` guard already used elsewhere in the resource-processing code (e.g. `FreezeV2Util.getV2NetUsage`/`getV2EnergyUsage`). Additionally, consider clamping `transferUsage` to `min(transferUsage, receiverCapsule.getNetUsage()/getEnergyUsage())` before use, so the invariant `transferUsage <= currentUsage` is enforced regardless of floating-point rounding or state changes between computation points.

### Proof of Concept
Concrete PoC values require live account state (frozen balances, usage windows, dynamic total-weight parameters) that are only fully knowable at runtime; a definitive minimal repro was not constructed from static analysis alone. The conceptual scenario:
1. Delegate a resource balance `B` to receiver `R`.
2. `R` consumes bandwidth/energy such that `netUsage`/`energyUsage` is a specific value `U`.
3. Between the time `AllFrozenBalanceForBandwidth`/`AllFrozenBalanceForEnergy` was effectively fixed and undelegation execution, or purely due to floating-point rounding in `transferUsage = (long)(U * ((double) unDelegateBalance / AllFrozenBalance))`, the computed `transferUsage` rounds up to a value `> U`.
4. `newNetUsage = U - transferUsage` becomes negative and is persisted via `receiverCapsule.setNetUsage(newNetUsage)`, corrupting the account's resource-usage accounting without any revert.

Because I could not execute the code to force the specific floating-point rounding conditions, I recommend a background Devin session construct a JUnit test (mirroring the existing `UnDelegateResourceActuatorTest`) that fuzzes `unDelegateBalance`, `AllFrozenBalanceForBandwidth`, and `netUsage` values to demonstrate a case where `transferUsage > netUsage`, confirming the underflow into a negative stored value.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L80-92)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L103-115)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L114-126)
```java
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalNetLimit() / repo.getTotalNetWeight());
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage, VMConfig.disableJavaLangMath());

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }

          long newNetUsage = receiverCapsule.getNetUsage() - transferUsage;
          receiverCapsule.setNetUsage(newNetUsage);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L139-151)
```java
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
```
