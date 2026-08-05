Based on extensive searching, I found a strong analog in `DelegateResourceActuator`.

### Title
Live `MAX_DELEGATE_LOCK_PERIOD` parameter used instead of the value in effect when a lock was created, allowing inconsistent lock-period enforcement for existing delegations - (File: `actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java`)

### Summary
`DelegateResourceActuator.validate()` re-adds/extends a locked resource delegation by comparing the new `lockPeriod` against the *current* global `MAX_DELEGATE_LOCK_PERIOD` and the delegation's stored `expireTime`, rather than deriving guarantees from the value that was in force when the original lock was created. This is the same bug class as the Gondi finding: a value that should be pinned per-instance (the protocol/lock parameter applicable to a specific position) is instead re-read live from mutable global state on every subsequent action that touches that position.

### Finding Description
When a user delegates resources with `lock = true`, `DelegateResourceActuator.execute()` computes `expireTime = now + lockPeriod * BLOCK_PRODUCED_INTERVAL` [1](#0-0)  and persists only `expireTime` in `DelegatedResourceCapsule` — the `lockPeriod`/parameter cap that was valid at creation time is not stored. On a later `DelegateResourceContract` call to the same (owner, receiver) pair, `validate()` fetches the **current** `dynamicStore.getMaxDelegateLockPeriod()` and only checks that the new `lockPeriod` is within that current cap and that `remainTime` (derived from the previously stored `expireTime`) is not exceeded [2](#0-1) . `getLockPeriod()` also silently defaults to `DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL` whenever `supportMaxDelegateLockPeriod()` is false [3](#0-2) , and `supportMaxDelegateLockPeriod()` itself is a function of the live `MAX_DELEGATE_LOCK_PERIOD` and `UNFREEZE_DELAY_DAYS` [4](#0-3) . If governance changes `MAX_DELEGATE_LOCK_PERIOD` (via `ProposalService`, which just calls `saveMaxDelegateLockPeriod` directly with no per-position migration [5](#0-4) ) between the time a delegation was created and a later top-up/extension of the same delegation, the later call is validated against a parameter that has no relationship to the original commitment, letting a lock period be set that is inconsistent with what the protocol intended to allow for that specific delegation (either unduly restrictive, breaking legitimate follow-on delegations, or, if the cap increased, allowing a far longer lock than what receivers/owners contracted for when the position was first opened).

### Impact Explanation
Because the enforced cap changes retroactively for existing delegated positions, this diverges from the Gondi pattern's core issue: state (fees/parameters) that should be fixed per financial instrument is instead re-derived from a mutable global at each subsequent interaction, producing accounting/validation divergence across the lifetime of a single logical position. This can freeze legitimate resource top-ups (denial of expected functionality) or permit lock durations well beyond what was possible/intended at creation time, depending on the direction of the parameter change.

### Likelihood Explanation
`MAX_DELEGATE_LOCK_PERIOD` is a governance-controlled proposal parameter [6](#0-5)  that can legitimately change over the life of long-lived locked delegations (delegations can be locked for up to a year, per `ONE_YEAR_BLOCK_NUMBERS`). Any witness-approved change to this parameter while active locked delegations exist will trigger this divergence on the very next `DelegateResourceContract` targeting an existing (owner, receiver) pair — a normal, unprivileged user action.

### Recommendation
Store the lock-period cap (or an equivalent per-delegation constraint) that was in effect at the time each locked `DelegatedResourceCapsule` was created, and validate subsequent extensions/top-ups against that stored value rather than re-reading the live `dynamicStore.getMaxDelegateLockPeriod()`, mirroring the Gondi fix of using `_loan.protocolFee` instead of the live `protocolFee.fraction`.

### Proof of Concept
1. Governance approves `MAX_DELEGATE_LOCK_PERIOD = N1` (large).
2. User A delegates resources to B with `lock=true`, `lockPeriod` close to `N1`, creating a `DelegatedResourceCapsule` with `expireTime = now + lockPeriod * BLOCK_PRODUCED_INTERVAL` [7](#0-6) .
3. Governance later lowers `MAX_DELEGATE_LOCK_PERIOD = N2 < N1` via a new proposal, processed by `ProposalService` which unconditionally overwrites the stored parameter [5](#0-4) .
4. User A attempts to delegate more resources (top-up) to the same B with the same lock semantics; `validate()` now checks the new `lockPeriod` against `N2` and the stored `expireTime`/`remainTime` computed under the old regime [2](#0-1) , producing a validation outcome inconsistent with what was actually contracted for at step 2 — either an unexpected rejection or an inconsistent cap enforcement relative to the position's original terms.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L251-259)
```java
  private long getLockPeriod(boolean supportMaxDelegateLockPeriod,
      DelegateResourceContract delegateResourceContract) {
    long lockPeriod = delegateResourceContract.getLockPeriod();
    if (supportMaxDelegateLockPeriod) {
      return lockPeriod == 0 ? DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL : lockPeriod;
    } else {
      return DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL;
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L290-298)
```java
    // 1. unlock the expired delegate resource
    long now = chainBaseManager.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    delegatedResourceStore.unLockExpireResource(ownerAddress, receiverAddress, now);

    //modify DelegatedResourceStore
    long expireTime = 0;
    if (lock) {
      expireTime = now + lockPeriod * BLOCK_PRODUCED_INTERVAL;
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2872-2875)
```java
  public boolean supportMaxDelegateLockPeriod() {
    return (getMaxDelegateLockPeriod() > DELEGATE_PERIOD / BLOCK_PRODUCED_INTERVAL) &&
            getUnfreezeDelayDays() > 0;
  }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L355-358)
```java
        case MAX_DELEGATE_LOCK_PERIOD: {
          manager.getDynamicPropertiesStore().saveMaxDelegateLockPeriod(entry.getValue());
          break;
        }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L717-735)
```java
      case MAX_DELEGATE_LOCK_PERIOD: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_7_2)) {
          throw new ContractValidateException(
              "Bad chain parameter id [MAX_DELEGATE_LOCK_PERIOD]");
        }
        long maxDelegateLockPeriod = dynamicPropertiesStore.getMaxDelegateLockPeriod();
        if (value <= maxDelegateLockPeriod || value > ONE_YEAR_BLOCK_NUMBERS) {
          throw new ContractValidateException(
              "This value[MAX_DELEGATE_LOCK_PERIOD] is only allowed to be greater than "
                  + maxDelegateLockPeriod + " and less than or equal to " + ONE_YEAR_BLOCK_NUMBERS
                      + " !");
        }
        if (dynamicPropertiesStore.getUnfreezeDelayDays() == 0) {
          throw new ContractValidateException(
              "[UNFREEZE_DELAY_DAYS] proposal must be approved "
                  + "before [MAX_DELEGATE_LOCK_PERIOD] can be proposed");
        }
        break;
      }
```
