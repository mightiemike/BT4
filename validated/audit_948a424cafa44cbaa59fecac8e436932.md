Based on my analysis, I found a valid analog of this bug class in java-tron.

### Title
Inconsistent strict validation between legacy `UnfreezeBalanceActuator` and TVM native `UnfreezeBalanceProcessor` corrupts delegated-resource accounting - ([File: actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java])

### Summary
The legacy TRX-transaction path `UnfreezeBalanceActuator.execute()` performs a **strict, gated** decrement of a receiver's `AcquiredDelegatedFrozenBalanceForBandwidth/Energy` — it explicitly checks whether the acquired balance is less than the amount being taken back and, if so, clamps to `0` only under specific hard-fork/AllowTvmSolidity conditions [1](#0-0) . In contrast, the TVM native-contract path `UnfreezeBalanceProcessor.execute()` (reachable from a smart contract's `unfreezebalance` native call) unconditionally calls `safeAddAcquiredDelegatedFrozenBalanceForBandwidth/Energy`, which is a "lenient" helper that floors the result at `0` via `Maths.max(0, ..., useStrict)` regardless of whether the subtraction would have gone negative [2](#0-1) , backed by `AccountCapsule.safeAddAcquiredDelegatedFrozenBalanceForBandwidth`/`safeAddAcquiredDelegatedFrozenBalanceForEnergy` [3](#0-2) [4](#0-3) .

### Finding Description
Both code paths implement the same business operation — "receiver's `AcquiredDelegatedFrozenBalance` must be reduced by the unfreeze amount when a delegator unfreezes and takes back a delegated resource" — but validate/clamp inconsistently:

- `UnfreezeBalanceActuator` (legacy `UnfreezeBalanceContract`, broadcast as a normal transaction) explicitly branches: if `receiverCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth() < unfreezeBalance` (under `AllowTvmSolidity059`), it sets the field to `0` and recomputes `oldNetWeight`/`decrease` from `unfreezeBalance` so the total-weight accounting stays internally consistent [5](#0-4) .
- `UnfreezeBalanceProcessor` (invoked via the TVM native contract path, reachable from an arbitrary smart contract calling the unfreeze native precompile) instead always calls `safeAddAcquiredDelegatedFrozenBalanceForBandwidth(-unfreezeBalance, ...)`, which internally does `max(0, acquired + balance, useStrict)` — silently floors negative results to zero **without adjusting** `TotalNetWeight`/`TotalEnergyWeight` the way the legacy path does [6](#0-5) .

This mirrors the reported bug class exactly: one code path (`allocateToStrategy`-analog = legacy actuator) has explicit, weight-consistent handling of the shortfall case, while the other path (`transferAssetToUnstakingVault`-analog = TVM processor) has a lenient "just floor to zero and move on" helper that can silently desynchronize tracked balances (`AcquiredDelegatedFrozenBalanceForBandwidth/Energy`) from the real total resource weight (`TotalNetWeight`/`TotalEnergyWeight`), because the TVM path's weight decrement (`repo.addTotalNetWeight(-unfreezeBalance / TRX_PRECISION)` at line 193 of the processor) is computed directly from `unfreezeBalance`, not from the actual clamped delta, unlike the legacy actuator which recomputes `decrease` from the pre/post weight difference.

### Impact Explanation
If a receiver's `AcquiredDelegatedFrozenBalanceForBandwidth/Energy` can be driven below the amount that a delegator later reclaims (e.g., because the receiver already had part of that delegated resource forcibly reduced through a different flow, or due to floating accounting drift across the two divergent code paths), the TVM path floors the field to `0` and always subtracts the full `unfreezeBalance` from the global `TotalNetWeight`/`TotalEnergyWeight` counters. This can push the network's aggregate resource-weight accounting out of sync with the sum of individual accounts' resource balances — a form of resource/accounting corruption affecting bandwidth/energy pricing for the whole network, reachable purely from a smart contract calling the native unfreeze functionality, no privileged role required.

### Likelihood Explanation
Medium: the two divergent implementations exist for the same underlying resource-delegation state (`AcquiredDelegatedFrozenBalanceForBandwidth/Energy`), and there are legitimate multi-actor flows (delegate/undelegate/freeze/unfreeze combinations across V1 and V2 delegation, mixed with old-style `UnfreezeBalanceContract` delegation) that can create the acquired-balance-shortfall precondition without any single "malicious" account controlling the accounting mismatch alone; reaching the TVM processor path only requires deploying and calling a contract that invokes the native unfreeze functionality, which is an anonymous/unprivileged capability.

### Recommendation
Make `UnfreezeBalanceProcessor.execute()` handle the shortfall case the same way `UnfreezeBalanceActuator.execute()` does: explicitly detect when `receiverCapsule`'s acquired delegated balance is less than `unfreezeBalance`, clamp the field to `0`, and compute the weight decrease (`repo.addTotalNetWeight`/`addTotalEnergyWeight`) from the actual pre/post delta rather than always subtracting the full `unfreezeBalance`. Alternatively, make the "safe" setter itself return/report the clamped delta so callers can keep total-weight bookkeeping consistent with the per-account field it just adjusted.

### Proof of Concept
1. Establish state where a receiver's `AcquiredDelegatedFrozenBalanceForBandwidth` is smaller than the `FrozenBalanceForBandwidth` recorded in the corresponding `DelegatedResourceCapsule` (achievable through legitimate combinations of V1/V2 delegate and undelegate operations that leave the acquired counter lower than the delegator's recorded frozen balance).
2. Trigger unfreeze via the TVM native contract path (a smart contract invoking the native "unfreeze balance" functionality) instead of broadcasting a plain `UnfreezeBalanceContract` transaction.
3. Observe `UnfreezeBalanceProcessor.execute()` unconditionally call `safeAddAcquiredDelegatedFrozenBalanceForBandwidth(-unfreezeBalance, ...)`, flooring the receiver's field to `0`, while `repo.addTotalNetWeight(-unfreezeBalance / TRX_PRECISION)` still subtracts the full, unclamped amount from the global weight — producing a global/local accounting mismatch [7](#0-6) [8](#0-7) .

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L119-131)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L134-151)
```java
      // take back resource from receiver account
      AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
      if (receiverCapsule != null) {
        switch (param.getResourceType()) {
          case BANDWIDTH:
            receiverCapsule.safeAddAcquiredDelegatedFrozenBalanceForBandwidth(-unfreezeBalance,
                VMConfig.disableJavaLangMath());
            break;
          case ENERGY:
            receiverCapsule.safeAddAcquiredDelegatedFrozenBalanceForEnergy(-unfreezeBalance,
                VMConfig.disableJavaLangMath());
            break;
          default:
            //this should never happen
            break;
        }
        repo.updateAccount(receiverCapsule.createDbKey(), receiverCapsule);
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

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L405-410)
```java
  public void safeAddAcquiredDelegatedFrozenBalanceForBandwidth(long balance, boolean useStrict) {
    this.account = this.account.toBuilder().setAcquiredDelegatedFrozenBalanceForBandwidth(
        max(0, this.account.getAcquiredDelegatedFrozenBalanceForBandwidth() + balance,
            useStrict))
        .build();
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L503-513)
```java
  public void safeAddAcquiredDelegatedFrozenBalanceForEnergy(long balance, boolean useStrict) {
    AccountResource newAccountResource = getAccountResource().toBuilder()
        .setAcquiredDelegatedFrozenBalanceForEnergy(
            max(0, getAccountResource().getAcquiredDelegatedFrozenBalanceForEnergy() + balance,
                useStrict))
        .build();

    this.account = this.account.toBuilder()
        .setAccountResource(newAccountResource)
        .build();
  }
```
