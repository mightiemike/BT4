## Finding

### Title
Repeated `FreezeBalance` (V1) calls overwrite and reset the unlock time of already-frozen TRX - (File: `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java`, `actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java`)

### Summary
The reported Vader bug is that `VaderBond.deposit()` overwrites a depositor's bond record on every call, resetting the vesting/unlock clock even for balance that was already close to being claimable. The equivalent pattern exists in java-tron's legacy freeze mechanism (`FreezeBalanceContract`/opcode `freeze`), where freezing additional TRX for an account that already has a pending frozen balance rewrites the single stored `Frozen.expireTime` field with the new call's `now + duration`, discarding the previous, possibly much closer, expiration time.

### Finding Description
`FreezeBalanceActuator.execute()` computes the new expiry as `now + duration` from the *current* transaction only, then calls `accountCapsule.setFrozenForBandwidth(newFrozenBalanceForBandwidth, expireTime)` (and the analogous `setFrozenForEnergy`/`setFrozenForTronPower`), passing the **combined** balance (`frozenBalance + accountCapsule.getFrozenBalance()`) together with the **new** `expireTime`: [1](#0-0) 

`AccountCapsule.setFrozenForBandwidth`/`setFrozenForEnergy` unconditionally replace the single `Frozen` entry (balance + `expireTime`) rather than tracking multiple independent lock periods: [2](#0-1) [3](#0-2) 

The same overwrite logic is duplicated in the TVM-reachable native contract path used by the `freeze` VM opcode (`Program.freeze()` → `FreezeBalanceProcessor.execute()`), which is invocable by any smart contract as long as `VMConfig.allowTvmFreezeV2()` is false: [4](#0-3) [5](#0-4) 

Because there is no check comparing the new `expireTime` to the existing one, and no requirement that a prior freeze be expired/claimed before adding more, any account (or contract, via the TVM opcode) that calls freeze again before its current lock naturally expires has the unlock time for its *entire* pooled frozen balance pushed forward to `now + newDuration` — exactly mirroring the Vader bond bug where a new `deposit()` silently resets the vesting clock for funds that were already close to being claimable.

By contrast, java-tron's own newer `DelegateResourceActuator` (used for locked resource delegation) explicitly guards against this exact class of bug: it validates that a new lock period cannot be shorter than the remaining time of a prior lock before allowing the overwrite (`validRemainTime`), showing the team is aware this pattern is a hazard and has patched it in the newer delegate-resource code path but not in the legacy V1 freeze path: [6](#0-5) [7](#0-6) 

### Impact Explanation
A user who freezes TRX in two (or more) transactions before the earlier freeze naturally unlocks has the unlock time of the combined balance forcibly extended to the most recent call's `now + duration`, with no user consent for the extension of the earlier portion. This is a self-inflicted, unprivileged-user accounting/lock-state issue: funds that should have become unfreezable at time T1 instead become unfreezable at the later T2, delaying the user's ability to reclaim TRX and, if `receiverAddress` differs, delaying/complicating delegated resource accounting as well. The bug is reachable both from a normal `FreezeBalanceContract` transaction and from any smart contract invoking the `freeze` TVM opcode.

### Likelihood Explanation
This is a public, unprivileged-user code path (no special role required) that fires deterministically any time an account issues two `FreezeBalance` calls for the same resource type before the first one's duration elapses — a common and expected usage pattern (e.g., topping up frozen balance for more bandwidth/energy). The affected path is legacy (disabled once `supportUnfreezeDelay()`/FreezeV2 is activated network-wide via `dynamicStore.supportUnfreezeDelay()` check in the actuator's `validate()`), but the TVM opcode path (`Program.freeze()`/`FreezeBalanceProcessor`) has no equivalent guard, so it remains exploitable by contracts as long as `allowTvmFreezeV2()` is false.

### Recommendation
When adding to an existing frozen balance, either (a) require the new `expireTime` to be at least as far out as necessary but never allow it to *shorten* the effective unlock guarantee unexpectedly while also not silently resetting an about-to-expire lock without user awareness, or (b) track freeze deposits as separate entries with independent expiry (similar to the `FreezeV2`/`UnFreezeV2` list model), or (c) mirror the `DelegateResourceActuator.validRemainTime` pattern by validating/rejecting re-freezes that would push the expiry later than the caller intends, and apply the same guard inside `FreezeBalanceProcessor` used by the TVM `freeze` opcode.

### Proof of Concept
1. Account A calls `FreezeBalanceContract` for `BANDWIDTH` with `frozenDuration = 3 days` at `t = 0`; `Frozen.expireTime` is set to `t + 3d`.
2. Before `t + 3d`, at `t = 1 day`, Account A calls `FreezeBalanceContract` again for `BANDWIDTH` with `frozenDuration = 3 days` (e.g., to add more bandwidth).
3. `FreezeBalanceActuator.execute()` computes `newFrozenBalanceForBandwidth = frozenBalance + accountCapsule.getFrozenBalance()` and calls `setFrozenForBandwidth(newFrozenBalanceForBandwidth, t=1d+3d=4d)`, overwriting the stored `Frozen` entry — the balance from the first freeze, which was going to unlock at `t=3d`, is now locked until `t=4d`.
4. At `t = 3d`, Account A attempts `UnfreezeBalanceContract`; it fails because the combined `Frozen.expireTime` is now `4d`, confirming the original balance's unlock was silently pushed back by the second deposit, with no opportunity given to unfreeze/claim the first portion first.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L87-94)
```java
        } else {
          long oldNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          long newFrozenBalanceForBandwidth =
              frozenBalance + accountCapsule.getFrozenBalance();
          accountCapsule.setFrozenForBandwidth(newFrozenBalanceForBandwidth, expireTime);
          long newNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          increment = newNetWeight - oldNetWeight;
        }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L1024-1041)
```java
  public void setFrozenForBandwidth(long frozenBalance, long expireTime) {
    Frozen newFrozen = Frozen.newBuilder()
        .setFrozenBalance(frozenBalance)
        .setExpireTime(expireTime)
        .build();

    long frozenCount = getFrozenCount();
    if (frozenCount == 0) {
      setInstance(getInstance().toBuilder()
          .addFrozen(newFrozen)
          .build());
    } else {
      setInstance(getInstance().toBuilder()
          .setFrozen(0, newFrozen)
          .build()
      );
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L1077-1089)
```java
  public void setFrozenForEnergy(long newFrozenBalanceForEnergy, long time) {
    Frozen newFrozenForEnergy = Frozen.newBuilder()
        .setFrozenBalance(newFrozenBalanceForEnergy)
        .setExpireTime(time)
        .build();

    AccountResource newAccountResource = getAccountResource().toBuilder()
        .setFrozenBalanceForEnergy(newFrozenForEnergy).build();

    this.account = this.account.toBuilder()
        .setAccountResource(newAccountResource)
        .build();
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L99-116)
```java
    } else { // acquire resource
      switch (param.getResourceType()) {
        case BANDWIDTH:
          accountCapsule.setFrozenForBandwidth(
              frozenBalance + accountCapsule.getFrozenBalance(),
              expireTime);
          break;
        case ENERGY:
          accountCapsule.setFrozenForEnergy(
              frozenBalance + accountCapsule.getAccountResource()
                  .getFrozenBalanceForEnergy()
                  .getFrozenBalance(),
              expireTime);
          break;
        default:
          logger.debug("Resource Code Error.");
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1916-1950)
```java
  public boolean freeze(DataWord receiverAddress, DataWord frozenBalance, DataWord resourceType) {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();
    byte[] receiver = receiverAddress.toTronAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, receiver,
        frozenBalance.longValue(), null,
        "freezeFor" + convertResourceToString(resourceType), nonce, null);

    FreezeBalanceParam param = new FreezeBalanceParam();
    param.setOwnerAddress(owner);
    param.setReceiverAddress(receiver);
    boolean needCheckFrozenTime = CommonParameter.getInstance()
        .getCheckFrozenTime() == 1; // for test
    param.setFrozenDuration(needCheckFrozenTime
        ? repository.getDynamicPropertiesStore().getMinFrozenTime() : 0);
    param.setResourceType(parseResourceCode(resourceType));
    try {
      FreezeBalanceProcessor processor = new FreezeBalanceProcessor();
      param.setFrozenBalance(frozenBalance.sValue().longValueExact());
      processor.validate(param, repository);
      processor.execute(param, repository);
      repository.commit();
      return true;
    } catch (ContractValidateException e) {
      logger.warn("TVM Freeze: validate failure. Reason: {}", e.getMessage());
    } catch (ArithmeticException e) {
      logger.warn("TVM Freeze: frozenBalance out of long range.");
    }
    if (internalTx != null) {
      internalTx.reject();
    }
    return false;
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
