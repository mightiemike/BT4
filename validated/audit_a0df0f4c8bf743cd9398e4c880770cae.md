## Analysis

I identified a valid analog: in `FreezeBalanceActuator.execute` (`actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java`), when a user freezes additional TRX for `BANDWIDTH`/`ENERGY` (non-delegated case), the account's frozen balance is *accumulated* (`frozenBalance + accountCapsule.getFrozenBalance()` / `getEnergyFrozenBalance()`), but the corresponding `expireTime` is unconditionally overwritten with `now + duration` from the *new* transaction only — with no comparison against the remaining lock time of the balance already frozen. [1](#0-0) 

This mirrors the report's bug class exactly: an amount-like field is extended/increased (`frozenBalance`), but the paired duration/expiry field is not correspondingly recalculated to preserve the previously committed lock — it is simply reset by the latest, possibly shorter, `frozenDuration`. Unlike `DelegateResourceActuator`, which enforces `validRemainTime` before relocking to prevent shortening an existing lock period, `FreezeBalanceActuator`'s self-freeze path (`setFrozenForBandwidth`/`setFrozenForEnergy`) has no such check. [2](#0-1) [3](#0-2) 

`validate()` only checks `frozenDuration >= minFrozenTime` (currently 3 days) against the *new* freeze — it never checks the *existing* `Frozen.getExpireTime()` on the account to prevent a new, shorter-duration freeze from truncating the previous lock.

### Title
Frozen-balance expireTime is overwritten (not extended) on repeat freeze, allowing early unlock of previously committed TRX Power/resources - (File: actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java)

### Summary
`FreezeBalanceActuator.execute` accumulates the frozen balance for BANDWIDTH/ENERGY across repeated `FreezeBalanceContract` calls but always sets the resulting `Frozen.expireTime` to `now + newFrozenDuration`, discarding the remaining lock time of the balance that was already frozen.

### Finding Description
When a user calls `freezebalance` a second time for the same resource (BANDWIDTH or ENERGY, no `receiverAddress`/delegation), the actuator computes:
```
long newFrozenBalanceForBandwidth = frozenBalance + accountCapsule.getFrozenBalance();
accountCapsule.setFrozenForBandwidth(newFrozenBalanceForBandwidth, expireTime);
```
where `expireTime = now + freezeBalanceContract.getFrozenDuration() * FROZEN_PERIOD` is derived solely from the current transaction's requested duration. `setFrozenForBandwidth`/`setFrozenForEnergy` in `AccountCapsule` fully replace the stored `Frozen` message (balance + expireTime) rather than taking the max of old and new expireTime. [4](#0-3) [5](#0-4) 

`validate()` only enforces `frozenDuration >= minFrozenTime` against the new request; it never compares this to the remaining time on the account's existing `Frozen.expireTime`. This is the exact bug class described in the report: an amount field is increased (frozen balance grows) but the associated time/period field is not properly extended/reconciled — instead it is reset to a value that can be *shorter* than what was previously committed.

By contrast, `DelegateResourceActuator.validRemainTime` explicitly guards against this for the delegation flow, confirming the project is aware such a check is necessary but it is missing from the plain (non-delegated) self-freeze path in `FreezeBalanceActuator`. [2](#0-1) 

### Impact Explanation
An account can freeze a large amount of TRX for a long duration (e.g., 3 days minimum, or a longer amount used to gain TRON Power/bandwidth/energy weight and cast votes), then later submit a second `FreezeBalanceContract` freezing the minimum additional 1 TRX with the minimum duration. This resets the expire time of the *entire* accumulated frozen balance to the new, much shorter duration, letting the account unfreeze (and reclaim) the full balance — and lose associated voting power / bandwidth / energy weight — far earlier than the network's resource-accounting design intends. This corrupts resource/TRON-Power weight accounting and lock-period guarantees relied upon for consensus voting stability (`TotalNetWeight`/`TotalEnergyWeight`/`TotalTronPowerWeight` and vote counting), which is an accounting-correctness/consensus-integrity issue reachable from any account via a normal broadcast transaction.

### Likelihood Explanation
The scenario requires only the account owner (or someone with the account's active permission) to submit two `FreezeBalanceContract` transactions to their own account for the same resource type; no privileged role or malicious peer is needed. This actuator is reachable via any RPC/HTTP broadcast transaction. The main friction is that `FreezeBalanceContract` is a deprecated flow relative to `FreezeBalanceV2Contract`; whether it can still be triggered depends on chain configuration (`allowNewResourceModel`) which I could not fully verify was disabled network-wide in this codebase version — the actuator class itself remains present, registered, and unconditionally executable based on the code reviewed.

### Recommendation
When accumulating additional frozen balance for an existing `Frozen` entry, compute the new `expireTime` as `max(now + newDuration, existing Frozen.getExpireTime())` rather than unconditionally overwriting it, mirroring the `validRemainTime` check already used in `DelegateResourceActuator`. Apply the same fix to the ENERGY case and to `FreezeBalanceProcessor.execute` (TVM native freeze path), which has the identical pattern. [6](#0-5) 

### Proof of Concept
1. Account A calls `FreezeBalanceContract` with `frozenBalance = 1_000_000_000`, `frozenDuration = 30` (days) for `ENERGY`. `expireTime` = now + 30 days is stored via `setFrozenForEnergy`.
2. Before day 30, Account A calls `FreezeBalanceContract` again with `frozenBalance = 1_000_000` (1 TRX), `frozenDuration = 3` (minimum) for `ENERGY`.
3. In `execute`, `newFrozenBalanceForEnergy = 1_000_000 + 1_000_000_000` and `expireTime = now + 3 days` fully replace the stored `Frozen` entry, per [7](#0-6) .
4. Three days later (day 33, still short of the originally committed day 30+ lock from step 1's remaining schedule, or well before an equivalent "30-day" commitment made afterward), Account A can call `UnfreezeBalanceContract`/`UnfreezeBalanceActuator`, whose validation only checks `frozenForEnergy.getExpireTime() > now`, per [8](#0-7) , allowing the entire accumulated balance to be unlocked far earlier than the original 30-day commitment implied.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L69-94)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    long duration = freezeBalanceContract.getFrozenDuration() * FROZEN_PERIOD;

    long newBalance = accountCapsule.getBalance() - freezeBalanceContract.getFrozenBalance();

    long frozenBalance = freezeBalanceContract.getFrozenBalance();
    long expireTime = now + duration;
    byte[] ownerAddress = freezeBalanceContract.getOwnerAddress().toByteArray();
    byte[] receiverAddress = freezeBalanceContract.getReceiverAddress().toByteArray();

    long increment;
    switch (freezeBalanceContract.getResource()) {
      case BANDWIDTH:
        if (!ArrayUtils.isEmpty(receiverAddress)
            && dynamicStore.supportDR()) {
          increment = delegateResource(ownerAddress, receiverAddress, true,
                  frozenBalance, expireTime);
          accountCapsule.addDelegatedFrozenBalanceForBandwidth(frozenBalance);
        } else {
          long oldNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          long newFrozenBalanceForBandwidth =
              frozenBalance + accountCapsule.getFrozenBalance();
          accountCapsule.setFrozenForBandwidth(newFrozenBalanceForBandwidth, expireTime);
          long newNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          increment = newNetWeight - oldNetWeight;
        }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L103-111)
```java
        } else {
          long oldEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          long newFrozenBalanceForEnergy =
              frozenBalance + accountCapsule.getEnergyFrozenBalance();
          accountCapsule.setFrozenForEnergy(newFrozenBalanceForEnergy, expireTime);
          long newEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          increment = newEnergyWeight - oldEnergyWeight;
        }
        addTotalWeight(ENERGY, dynamicStore, frozenBalance, increment);
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L152-215)
```java
  @Override
  public boolean validate() throws ContractValidateException {
    if (this.any == null) {
      throw new ContractValidateException(ActuatorConstant.CONTRACT_NOT_EXIST);
    }
    if (chainBaseManager == null) {
      throw new ContractValidateException(ActuatorConstant.STORE_NOT_EXIST);
    }
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    if (!any.is(FreezeBalanceContract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [FreezeBalanceContract],real type[" + any
              .getClass() + "]");
    }

    final FreezeBalanceContract freezeBalanceContract;
    try {
      freezeBalanceContract = this.any.unpack(FreezeBalanceContract.class);
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }
    byte[] ownerAddress = freezeBalanceContract.getOwnerAddress().toByteArray();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
    if (accountCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
    }

    long frozenBalance = freezeBalanceContract.getFrozenBalance();
    if (frozenBalance <= 0) {
      throw new ContractValidateException("frozenBalance must be positive");
    }
    if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("frozenBalance must be greater than or equal to 1 TRX");
    }

    int frozenCount = accountCapsule.getFrozenCount();
    if (!(frozenCount == 0 || frozenCount == 1)) {
      throw new ContractValidateException("frozenCount must be 0 or 1");
    }
    if (frozenBalance > accountCapsule.getBalance()) {
      throw new ContractValidateException("frozenBalance must be less than or equal to accountBalance");
    }

    long frozenDuration = freezeBalanceContract.getFrozenDuration();
    long minFrozenTime = dynamicStore.getMinFrozenTime();
    long maxFrozenTime = dynamicStore.getMaxFrozenTime();

    boolean needCheckFrozeTime = CommonParameter.getInstance()
        .getCheckFrozenTime() == 1;//for test
    if (needCheckFrozeTime && !(frozenDuration >= minFrozenTime
        && frozenDuration <= maxFrozenTime)) {
      throw new ContractValidateException(
          "frozenDuration must be less than " + maxFrozenTime + " days "
              + "and more than " + minFrozenTime + " days");
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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L448-457)
```java
        case ENERGY:
          Frozen frozenBalanceForEnergy = accountCapsule.getAccountResource()
              .getFrozenBalanceForEnergy();
          if (frozenBalanceForEnergy.getFrozenBalance() <= 0) {
            throw new ContractValidateException("no frozenBalance(Energy)");
          }
          if (frozenBalanceForEnergy.getExpireTime() > now) {
            throw new ContractValidateException("It's not time to unfreeze(Energy).");
          }

```
