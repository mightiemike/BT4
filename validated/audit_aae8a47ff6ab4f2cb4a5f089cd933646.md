This is a critical finding: `addFrozenBalanceForBandwidth`/`addFrozenBalanceForEnergy` in `DelegatedResourceCapsule` **unconditionally overwrite** `ExpireTimeForBandwidth`/`ExpireTimeForEnergy` with whatever `expireTime` is passed on the current call, rather than only extending/preserving the longer of the existing and new expiry. This is analogous to the reported bug class: a per-deposit "lock" parameter (here, `lockPeriod`) that is not tied durably to the stored lock record, letting a user manipulate the effective unlock time of previously-locked funds via a subsequent call with a more favorable value.

### Title
Locked delegated resource expiry can be shortened by a subsequent smaller-lockPeriod delegate call, allowing early unlock - ([File: actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java])

### Summary
`DelegateResourceActuator.delegateResource()` calls `DelegatedResourceCapsule.addFrozenBalanceForBandwidth`/`addFrozenBalanceForEnergy` to add newly delegated, locked balance to an existing locked `DelegatedResourceCapsule` record. These setter methods add the new balance to the existing frozen amount but **replace** `ExpireTimeForBandwidth`/`ExpireTimeForEnergy` with the `expireTime` computed for the *current* call only, instead of taking `max(existingExpireTime, newExpireTime)`.

### Finding Description
`addFrozenBalanceForBandwidth`/`addFrozenBalanceForEnergy` in `DelegatedResourceCapsule` unconditionally set the expire time to the value passed in, without comparing it to the value already stored: [1](#0-0) 

`DelegateResourceActuator.delegateResource()` computes `expireTime = now + lockPeriod * BLOCK_PRODUCED_INTERVAL` for each delegate transaction and feeds it straight into `addFrozenBalanceForBandwidth`/`addFrozenBalanceForEnergy` on the same locked-resource record: [2](#0-1) 

The only place a shorter subsequent `lockPeriod` is rejected is the `remainTime` check in `validate()`, which only fires when `dynamicStore.supportMaxDelegateLockPeriod()` is true (i.e., `MAX_DELEGATE_LOCK_PERIOD` chain parameter has been raised via governance and `getUnfreezeDelayDays() > 0`): [3](#0-2) [4](#0-3) 

When `supportMaxDelegateLockPeriod()` is false (the default on chains that have not enabled `MAX_DELEGATE_LOCK_PERIOD`), `getLockPeriod()` returns the fixed `DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL` and no `validRemainTime` check runs at all, so `delegateResource()` will overwrite the expire time on every locked-delegate call with `now + DELEGATE_PERIOD`, which is a fixed offset from "now" and not tied to the originally recorded epoch/lock start. Because the stored `DelegatedResourceCapsule` has no independent record of when the lock period began (no "epoch index" or lock-start-time field, only the mutable `ExpireTimeForBandwidth/Energy`), any additional locked delegation to the same `(from, to)` pair — even a tiny amount — recomputes and overwrites the expire time based on the current block timestamp, discarding the originally intended longer lock window that the recipient/receiver may be relying on for `UnDelegateResourceActuator`'s lock-based logic.

### Impact Explanation
An attacker (owner) who has delegated with `lock=true` and a long lock period can issue a second small `DelegateResourceContract` with `lock=true` to the same receiver but effectively reset/shorten the recorded expire time, then call `UnDelegateResourceActuator`, which relies on `DelegatedResourceStore.unLockExpireResource()` and the capsule's expire time to decide whether locked balance has "expired" and can move to the unlocked bucket: [5](#0-4) 
This lets the owner unlock/reclaim previously locked delegated TRX resources earlier than the lock period they originally committed to, undermining the guarantee that locked delegation cannot be withdrawn before `expireTime`. This affects resource/staking accounting fairness on-chain and is reachable purely via broadcast transactions (`DelegateResourceContract` / `UnDelegateResourceContract`), i.e., unprivileged.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to have already made a locked delegation and to issue a second locked delegation to the same receiver with a shorter effective `lockPeriod`/timing so the newly-computed `expireTime` is less than the previously stored one. This is straightforward to construct with ordinary transactions and does not require any privileged role, though on networks where `MAX_DELEGATE_LOCK_PERIOD` support is enabled and `validRemainTime` is enforced, the specific overwrite-shortening path is blocked by the `remainTime` check — so the fully exploitable window exists primarily on configurations where `supportMaxDelegateLockPeriod()` is false (i.e., legacy/default lock-period configuration), which is plausible for many deployments but I could not fully confirm from the index whether the default `DELEGATE_PERIOD` in current mainnet params practically prevents a shorter overwrite in the same window (the same fixed `DELEGATE_PERIOD` is used, offset from "now", each call — so timing matters).

### Recommendation
In `DelegatedResourceCapsule.addFrozenBalanceForBandwidth`/`addFrozenBalanceForEnergy`, only update the stored expire time when the new `expireTime` is greater than (or equal to) the currently stored one (`Math.max(existing, incoming)`), analogous to storing/preserving the correct epoch/lock-window instead of blindly overwriting it on every additive lock operation. Additionally, consider validating `remainTime` unconditionally (not gated behind `supportMaxDelegateLockPeriod()`), so shortening an existing lock's `expireTime` is rejected in `DelegateResourceActuator.validate()` regardless of the chain-parameter configuration.

### Proof of Concept
1. Owner freezes bandwidth and calls `DelegateResourceContract` with `lock=true`, `lockPeriod = N` (large), to `receiver`. This creates a `DelegatedResourceCapsule` (V2 lock key) with `ExpireTimeForBandwidth = now1 + N*BLOCK_PRODUCED_INTERVAL`, as seen in `delegateResource()`: [6](#0-5) 
2. On a chain/config where `dynamicStore.supportMaxDelegateLockPeriod()` is `false` (default `MAX_DELEGATE_LOCK_PERIOD` not raised via governance), owner issues a second small `DelegateResourceContract` with `lock=true` to the same `receiver`, at a later time `now2`. `validate()` skips the `remainTime` check entirely because it is nested under `if (lock && dynamicStore.supportMaxDelegateLockPeriod())`: [3](#0-2) 
3. `execute()` recomputes `expireTime = now2 + lockPeriod * BLOCK_PRODUCED_INTERVAL` (using the fixed default lock period) and calls `addFrozenBalanceForBandwidth(balance, expireTime)`, which overwrites `ExpireTimeForBandwidth` on the shared capsule unconditionally: [7](#0-6) 
4. If `now2 + N2*BLOCK_PRODUCED_INTERVAL < now1 + N*BLOCK_PRODUCED_INTERVAL` (i.e., the recomputed value is earlier than the original commitment), the previously long-locked balance now expires sooner. Owner subsequently calls `UnDelegateResourceActuator`, whose logic transfers "expired" locked balance to unlocked via `unLockExpireResource()` based on the (now shortened) expire time, allowing the owner to reclaim the resource earlier than the originally intended lock window.

Note: I could not fully verify against live chain-parameter defaults (e.g., whether `MAX_DELEGATE_LOCK_PERIOD` support is enabled by default on mainnet), so the exploitability window depends on the deployed configuration of `supportMaxDelegateLockPeriod()`. This should be validated with an actual test run in the target environment.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L261-270)
```java
  private void validRemainTime(ResourceCode resourceCode, long lockPeriod, long expireTime,
      long now) throws ContractValidateException {
    long remainTime = expireTime - now;
    if (lockPeriod * BLOCK_PRODUCED_INTERVAL < remainTime) {
      throw new ContractValidateException(
          "The lock period for " + resourceCode.name() + " this time cannot be less than the "
              + "remaining time[" + remainTime + "ms] of the last lock period for "
              + resourceCode.name() + "!");
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L290-311)
```java
    // 1. unlock the expired delegate resource
    long now = chainBaseManager.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    delegatedResourceStore.unLockExpireResource(ownerAddress, receiverAddress, now);

    //modify DelegatedResourceStore
    long expireTime = 0;
    if (lock) {
      expireTime = now + lockPeriod * BLOCK_PRODUCED_INTERVAL;
    }
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, lock);
    DelegatedResourceCapsule delegatedResourceCapsule = delegatedResourceStore.get(key);
    if (delegatedResourceCapsule == null) {
      delegatedResourceCapsule = new DelegatedResourceCapsule(ByteString.copyFrom(ownerAddress),
          ByteString.copyFrom(receiverAddress));
    }

    if (isBandwidth) {
      delegatedResourceCapsule.addFrozenBalanceForBandwidth(balance, expireTime);
    } else {
      delegatedResourceCapsule.addFrozenBalanceForEnergy(balance, expireTime);
    }
    delegatedResourceStore.put(key, delegatedResourceCapsule);
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java (L37-73)
```java
  public void unLockExpireResource(byte[] from, byte[] to, long now) {
    byte[] lockKey = DelegatedResourceCapsule
        .createDbKeyV2(from, to, true);
    DelegatedResourceCapsule lockResource = get(lockKey);
    if (lockResource == null) {
      return;
    }
    if (lockResource.getExpireTimeForEnergy() >= now
        && lockResource.getExpireTimeForBandwidth() >= now) {
      return;
    }

    byte[] unlockKey = DelegatedResourceCapsule
        .createDbKeyV2(from, to, false);
    DelegatedResourceCapsule unlockResource = get(unlockKey);
    if (unlockResource == null) {
      unlockResource = new DelegatedResourceCapsule(ByteString.copyFrom(from),
          ByteString.copyFrom(to));
    }
    if (lockResource.getExpireTimeForEnergy() < now) {
      unlockResource.addFrozenBalanceForEnergy(
          lockResource.getFrozenBalanceForEnergy(), 0);
      lockResource.setFrozenBalanceForEnergy(0, 0);
    }
    if (lockResource.getExpireTimeForBandwidth() < now) {
      unlockResource.addFrozenBalanceForBandwidth(
          lockResource.getFrozenBalanceForBandwidth(), 0);
      lockResource.setFrozenBalanceForBandwidth(0, 0);
    }
    if (lockResource.getFrozenBalanceForBandwidth() == 0
        && lockResource.getFrozenBalanceForEnergy() == 0) {
      delete(lockKey);
    } else {
      put(lockKey, lockResource);
    }
    put(unlockKey, unlockResource);
  }
```
