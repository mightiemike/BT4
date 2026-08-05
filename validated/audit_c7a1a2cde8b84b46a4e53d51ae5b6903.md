### Title
Re-delegating resource to the same receiver overwrites (instead of extending/maxing) the shared `expireTime`, corrupting lock-duration accounting for previously delegated balance - (File: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java`)

### Summary
`DelegatedResourceCapsule` aggregates all bandwidth/energy delegated by an owner to a given receiver into a single record keyed by `(from, to)`. Each time `FreezeBalanceActuator` performs a new delegation to the same receiver, the frozen balance is summed but the single `expireTimeForBandwidth`/`expireTimeForEnergy` field is unconditionally overwritten with the newest freeze's expiry — never compared or maxed against the previous value, exactly the same class of bug as Ajna's `LenderActions.transferLPs` overwriting `depositTime` when multiple deposits are merged into one accounting record.

### Finding Description
`addFrozenBalanceForBandwidth`/`addFrozenBalanceForEnergy` add the new balance to the existing frozen balance but replace the expire time field wholesale with the new call's `expireTime`, with no `max()` comparison against the currently stored expiry: [1](#0-0) 

This is invoked from `FreezeBalanceActuator.delegateResource`, which is called any time an owner delegates bandwidth/energy to the same receiver — the new balance is added to the existing `DelegatedResourceCapsule` and the combined `expireTime` is replaced: [2](#0-1) 

`expireTime` itself is derived purely from the duration of the *current* freeze call (`now + duration`), independent of how much time remains on the balance already accumulated in the record: [3](#0-2) 

As with Ajna's `PositionManager`, which centralizes multiple lenders' LP positions under a single on-chain "lender" identity and then merges `depositTime` incorrectly, `DelegatedResourceStore` centralizes multiple independent freeze operations (potentially made at very different times, with very different intended lock durations) under a single `(from,to)` record and loses per-freeze timing granularity — the single stored `expireTime` no longer accurately reflects the lock commitment attached to the balance frozen earlier.

### Impact Explanation
The combined `frozenBalanceForBandwidth`/`frozenBalanceForEnergy` — including balance frozen earlier with a longer intended duration — is now unlockable at the (possibly much earlier) expiry of the most recent freeze call. Because `UnfreezeBalanceActuator` unfreezes the *entire* aggregated `delegatedResourceCapsule` balance for a resource type in one shot (it reads `getFrozenBalanceForBandwidth()`/`getFrozenBalanceForEnergy()` as a whole and zeroes it out), this causes the actual lock duration guaranteed for previously-frozen funds to silently diverge from what was originally committed: [4](#0-3) 

This is a concrete state/accounting-divergence issue: the on-chain `expireTimeForBandwidth`/`expireTimeForEnergy` field is supposed to gate when frozen TRX can be unlocked, but the aggregation logic makes it reflect only the last freeze operation rather than the maximum (latest-maturing) commitment across all merged freezes to that receiver, weakening the lock-duration guarantee the freeze/unfreeze mechanism is meant to enforce.

### Likelihood Explanation
This is trivially reachable by any account that delegates resources to the same receiver more than once — a normal, unprivileged, and common usage pattern (e.g. periodically topping-up delegated bandwidth/energy to the same receiver). No special permissions or edge-case setup are required beyond issuing two `FreezeBalanceContract` transactions with a receiver address, so likelihood of occurrence (even accidental) is high, though the practical exploit motivation (shortening one's own lock) is limited to the owner's own funds since the delegator and delegatee addresses in the DB key are fixed per record.

### Recommendation
When merging balances into an existing `DelegatedResourceCapsule`, take `Math.max(existingExpireTime, newExpireTime)` instead of unconditionally overwriting, so that the combined lock always reflects the latest-maturing commitment among all freezes recorded under the same `(from, to)` key — mirroring the correct fix pattern that should also have been applied in Ajna's `LenderActions.transferLPs`.

### Proof of Concept
1. Owner `A` calls `FreezeBalanceContract` delegating `X` TRX of bandwidth to receiver `B` with `frozenDuration = 30` (long lock).
2. `DelegatedResourceCapsule(A,B)` is created with `frozenBalanceForBandwidth = X`, `expireTimeForBandwidth = now + 30*FROZEN_PERIOD`.
3. Shortly after, `A` calls `FreezeBalanceContract` again, delegating an additional `Y` TRX to the same receiver `B` with `frozenDuration = 3` (minimum allowed).
4. `delegateResource` is invoked again; `addFrozenBalanceForBandwidth(Y, now + 3*FROZEN_PERIOD)` is executed: [1](#0-0) 
5. The stored record now has `frozenBalanceForBandwidth = X + Y` but `expireTimeForBandwidth = now + 3*FROZEN_PERIOD` — the original 30-period lock on `X` has been silently reduced to 3 periods.
6. After 3 periods, `A` calls `UnfreezeBalanceContract` and can withdraw the entire `X + Y` balance, even though `X` was originally committed for a 30-period lock.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java (L97-103)
```java
  public void addFrozenBalanceForBandwidth(long bandwidth, long expireTime) {
    this.delegatedResource = this.delegatedResource.toBuilder()
        .setFrozenBalanceForBandwidth(this.delegatedResource.getFrozenBalanceForBandwidth()
            + bandwidth)
        .setExpireTimeForBandwidth(expireTime)
        .build();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L69-76)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    long duration = freezeBalanceContract.getFrozenDuration() * FROZEN_PERIOD;

    long newBalance = accountCapsule.getBalance() - freezeBalanceContract.getFrozenBalance();

    long frozenBalance = freezeBalanceContract.getFrozenBalance();
    long expireTime = now + duration;
    byte[] ownerAddress = freezeBalanceContract.getOwnerAddress().toByteArray();
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L296-317)
```java
    byte[] key = DelegatedResourceCapsule.createDbKey(ownerAddress, receiverAddress);
    //modify DelegatedResourceStore
    DelegatedResourceCapsule delegatedResourceCapsule = delegatedResourceStore
        .get(key);
    if (delegatedResourceCapsule != null) {
      if (isBandwidth) {
        delegatedResourceCapsule.addFrozenBalanceForBandwidth(balance, expireTime);
      } else {
        delegatedResourceCapsule.addFrozenBalanceForEnergy(balance, expireTime);
      }
    } else {
      delegatedResourceCapsule = new DelegatedResourceCapsule(
          ByteString.copyFrom(ownerAddress),
          ByteString.copyFrom(receiverAddress));
      if (isBandwidth) {
        delegatedResourceCapsule.setFrozenBalanceForBandwidth(balance, expireTime);
      } else {
        delegatedResourceCapsule.setFrozenBalanceForEnergy(balance, expireTime);
      }

    }
    delegatedResourceStore.put(key, delegatedResourceCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L97-111)
```java
      switch (unfreezeBalanceContract.getResource()) {
        case BANDWIDTH:
          unfreezeBalance = delegatedResourceCapsule.getFrozenBalanceForBandwidth();
          delegatedResourceCapsule.setFrozenBalanceForBandwidth(0, 0);
          accountCapsule.addDelegatedFrozenBalanceForBandwidth(-unfreezeBalance);
          break;
        case ENERGY:
          unfreezeBalance = delegatedResourceCapsule.getFrozenBalanceForEnergy();
          delegatedResourceCapsule.setFrozenBalanceForEnergy(0, 0);
          accountCapsule.addDelegatedFrozenBalanceForEnergy(-unfreezeBalance);
          break;
        default:
          //this should never happen
          break;
      }
```
