### Title
FreezeBalanceActuator delegateResource re-locks the entire delegated balance to a new (possibly shorter) expire time, silently shortening the previously committed lock period - ([File: actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java])

### Summary
`FreezeBalanceActuator.delegateResource()` calls `DelegatedResourceCapsule.addFrozenBalanceForBandwidth`/`addFrozenBalanceForEnergy`, which unconditionally overwrites the stored `expireTimeForBandwidth`/`expireTimeForEnergy` with the newly computed value while adding the new amount to the *existing* accumulated `frozenBalance`. This is the same bug class as the reported StakedCitadelVester issue: a new "vest" (freeze/delegate) call resets the unlock timeline of the whole existing balance instead of only the newly added portion, without any check that the new timeline is not earlier than the prior commitment.

### Finding Description
When an owner delegates frozen resources to a receiver via `FreezeBalanceContract` (v1 freeze/delegate path), `FreezeBalanceActuator.execute()` computes `expireTime = now + duration` and calls `delegateResource(...)`, which in turn calls: [1](#0-0) 

`DelegatedResourceCapsule.addFrozenBalanceForBandwidth`/`addFrozenBalanceForEnergy` always overwrite the expire time field with the value passed in, regardless of any previously stored expire time for the balance already on record: [2](#0-1) 

If the owner freezes/delegates additional balance to the same receiver a second time with a shorter `frozenDuration`, the entire accumulated `frozenBalanceForBandwidth`/`frozenBalanceForEnergy` (old + new) is re-timestamped to the new, shorter expire time. `UnfreezeBalanceActuator.validate()` only checks this single, latest stored expire time before permitting an unfreeze: [3](#0-2) 

There is no accounting split that preserves the original commitment period for the portion already delegated — exactly analogous to the reported bug where `vest()` resets `unlockBegin`/`unlockEnd` for the full `lockedAmounts` (including amounts already vested/claimed) instead of only the newly added amount.

By contrast, the newer `DelegateResourceActuator` (v2, lock-period delegation) explicitly guards against this by validating that a new lock period cannot be shorter than the remaining time of the existing lock: [4](#0-3) 

confirming that the legacy `FreezeBalanceActuator` delegation path (still reachable via `FreezeBalanceContract` broadcast transactions where `getResource()` is BANDWIDTH/ENERGY and a `receiverAddress` is set) lacks this safeguard.

### Impact Explanation
An owner who has delegated frozen TRX-based resources (bandwidth/energy) to a receiver can unilaterally shorten the effective lock/unfreeze-eligibility time for the entire delegated balance (including amounts delegated earlier under a longer-duration commitment) by issuing a second `FreezeBalanceContract` delegation to the same receiver with a minimal `frozenDuration`. This corrupts the resource-accounting guarantee that delegated resources remain locked for their originally committed duration, letting the owner reclaim (unfreeze) resources — and correspondingly strip the receiver's acquired bandwidth/energy — earlier than intended. This is an accounting/state-integrity flaw reachable purely through ordinary broadcast transactions (`FreezeBalanceContract`), with no privileged access required.

### Likelihood Explanation
High likelihood of exploitation: any account can call `FreezeBalanceContract` twice against the same receiver — first with a long `frozenDuration`, then with the minimum allowed duration — to shrink the unlock time of the combined delegated balance. No special permissions, races, or node compromise are required; it only depends on the actuator/capsule logic being reachable via standard transaction broadcast, which it is.

### Recommendation
When adding to an existing delegated balance, do not blindly overwrite the stored expire time for the whole accumulated amount. Either:
- Track expire time per delegation increment (similar to the `FreezeV2`/`UnFreezeV2` list model), or
- Enforce (as `DelegateResourceActuator` v2 already does for `lock=true`) that a new delegation's expire time cannot be earlier than the remaining time of the currently stored expire time, rejecting/adjusting delegations that would shorten the existing lock commitment.

### Proof of Concept
1. Owner `A` sends `FreezeBalanceContract` with `resource=BANDWIDTH`, `receiverAddress=B`, `frozenDuration=N` (long duration) — `DelegatedResourceCapsule` for (A,B) now has `frozenBalanceForBandwidth = X1`, `expireTimeForBandwidth = now + N*FROZEN_PERIOD`.
2. Owner `A` sends a second `FreezeBalanceContract` with `resource=BANDWIDTH`, `receiverAddress=B`, `frozenDuration=1` (minimum) and a small additional `frozenBalance = X2`.
3. `FreezeBalanceActuator.execute()` → `delegateResource()` → `DelegatedResourceCapsule.addFrozenBalanceForBandwidth(X2, now + 1*FROZEN_PERIOD)` sets `frozenBalanceForBandwidth = X1+X2` and overwrites `expireTimeForBandwidth = now + 1*FROZEN_PERIOD`, discarding the original longer commitment for `X1`.
4. After 1 `FROZEN_PERIOD`, `UnfreezeBalanceActuator.validate()`'s check `delegatedResourceCapsule.getExpireTimeForBandwidth() > now` passes, allowing `A` to unfreeze the entire `X1+X2` balance — far earlier than the originally committed `N` periods for `X1`.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java (L70-103)
```java
  public void addFrozenBalanceForEnergy(long energy, long expireTime) {
    this.delegatedResource = this.delegatedResource.toBuilder()
        .setFrozenBalanceForEnergy(this.delegatedResource.getFrozenBalanceForEnergy() + energy)
        .setExpireTimeForEnergy(expireTime)
        .build();
  }

  public long getFrozenBalanceForBandwidth() {
    return this.delegatedResource.getFrozenBalanceForBandwidth();
  }

  public long getFrozenBalance(boolean isBandwidth) {
    if (isBandwidth) {
      return getFrozenBalanceForBandwidth();
    } else {
      return getFrozenBalanceForEnergy();
    }

  }

  public void setFrozenBalanceForBandwidth(long bandwidth, long expireTime) {
    this.delegatedResource = this.delegatedResource.toBuilder()
        .setFrozenBalanceForBandwidth(bandwidth)
        .setExpireTimeForBandwidth(expireTime)
        .build();
  }

  public void addFrozenBalanceForBandwidth(long bandwidth, long expireTime) {
    this.delegatedResource = this.delegatedResource.toBuilder()
        .setFrozenBalanceForBandwidth(this.delegatedResource.getFrozenBalanceForBandwidth()
            + bandwidth)
        .setExpireTimeForBandwidth(expireTime)
        .build();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L396-398)
```java
          if (delegatedResourceCapsule.getExpireTimeForBandwidth() > now) {
            throw new ContractValidateException("It's not time to unfreeze.");
          }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L211-241)
```java
    boolean lock = delegateResourceContract.getLock();
    if (lock && dynamicStore.supportMaxDelegateLockPeriod()) {
      long lockPeriod = getLockPeriod(true, delegateResourceContract);
      long maxDelegateLockPeriod = dynamicStore.getMaxDelegateLockPeriod();
      if (lockPeriod < 0 || lockPeriod > maxDelegateLockPeriod) {
        throw new ContractValidateException(
            "The lock period of delegate resource cannot be less than 0 and cannot exceed "
                + maxDelegateLockPeriod + "!");
      }

      byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, true);
      DelegatedResourceCapsule delegatedResourceCapsule = delegatedResourceStore.get(key);
      long now = dynamicStore.getLatestBlockHeaderTimestamp();
      if (delegatedResourceCapsule != null) {
        switch (delegateResourceContract.getResource()) {
          case BANDWIDTH: {
            validRemainTime(BANDWIDTH, lockPeriod,
                delegatedResourceCapsule.getExpireTimeForBandwidth(), now);
          }
          break;
          case ENERGY: {
            validRemainTime(ENERGY, lockPeriod,
                delegatedResourceCapsule.getExpireTimeForEnergy(), now);
          }
          break;
          default:
            throw new ContractValidateException(
                "ResourceCode error, valid ResourceCode[BANDWIDTH、ENERGY]");
        }
      }
    }
```
