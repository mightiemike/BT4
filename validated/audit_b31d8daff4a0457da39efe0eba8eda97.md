### Title
Repeated FreezeBalanceContract calls allow reducing (resetting) the unlock time of already-frozen TRX - (File: actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java)

### Summary
`FreezeBalanceActuator` recomputes the `expireTime` of a frozen-balance entry from the *current* block timestamp on every call and unconditionally overwrites the account's existing `Frozen`/`AccountResource.frozen_balance_for_energy`/`tron_power` entry with that new expire time. There is no check that the newly computed expire time is not earlier than the expire time already recorded for the existing lock, so a user can shorten the lock/unlock time of a large, long-duration freeze by issuing a subsequent `FreezeBalanceContract` with the (much smaller) minimum allowed duration.

### Finding Description
In `execute()`: [1](#0-0) 

`expireTime` is derived solely from `now` (the latest block header timestamp) plus the newly-submitted `frozenDuration`; it never reads or compares against the pre-existing `Frozen.expire_time` stored on the account. The subsequent call to `accountCapsule.setFrozenForBandwidth(...)` / `setFrozenForEnergy(...)` / `setFrozenForTronPower(...)` merges the balance (`frozenBalance + accountCapsule.getFrozenBalance()`) but replaces the stored `expire_time` with this freshly computed one: [2](#0-1) 

`validate()` only enforces that the submitted `frozenDuration` lies between `minFrozenTime` and `maxFrozenTime` — it does **not** compare against the account's currently recorded (still-active) `expire_time` for that resource: [3](#0-2) 

This is the same defect class described in the external report: the amount of "remaining lock time" is silently discarded and replaced by a freshly-computed, shorter value, because the actuator recomputes `now + duration` instead of validating against the previously committed unlock time. Notably, the sibling actuator `DelegateResourceActuator` *does* implement the correct guard for this exact scenario (comparing the new lock period against the remaining time of an existing lock before allowing an update): [4](#0-3) 

confirming that `FreezeBalanceActuator` is missing an equivalent check.

Exploit path:
1. Alice freezes `X` TRX for `maxFrozenTime` (e.g. 30 days) via `FreezeBalanceContract`, `expire_time = now + 30d`.
2. Before that period elapses (e.g. day 5), Alice submits another `FreezeBalanceContract` for a minimal additional amount (e.g. `1 TRX`) with `frozenDuration = minFrozenTime` (e.g. 3 days).
3. `execute()` computes `expireTime = now(day5) + 3d = day8` and calls `setFrozenForBandwidth(newTotalBalance, day8)`, which **overwrites** the stored `expire_time` for the *entire* frozen balance (the original `X` TRX plus the new 1 TRX) to `day 8` instead of `day 30`.
4. On day 8 Alice can call `UnfreezeBalanceActuator`, whose validation only checks `frozenExpireTime <= now`, and reclaim the full `X` TRX 22 days earlier than the lock she originally committed to.

This is reachable from any anonymous broadcast transaction containing a `FreezeBalanceContract`; no privileged role is required. It is only exploitable while `dynamicStore.supportUnfreezeDelay()` (i.e., Freeze v2 / `UNFREEZE_DELAY_DAYS`) is not yet enabled on the network, since `validate()` otherwise rejects the old contract type: [5](#0-4) 

### Impact Explanation
An attacker can freeze a large balance for the maximum duration (gaining maximum bandwidth/energy weight and voting power under the multi-day-lock economic assumption), then immediately shorten the effective lock via a trivial follow-up freeze transaction, unfreezing the entire balance far earlier than intended. This corrupts the network's frozen-balance/weight accounting invariants (`TotalNetWeight`/`TotalEnergyWeight`/`TotalTronPowerWeight` are computed assuming resources remain locked for the declared duration) and breaks the economic guarantee that resource/voting weight is backed by a correspondingly long-locked stake, letting an attacker double-dip: enjoy long-lock benefits while retaining short-lock liquidity.

### Likelihood Explanation
The bug is triggered by a normal, unprivileged transaction type (`FreezeBalanceContract`) that any account can broadcast, requires no special permissions, and needs only two ordinary transactions (an initial freeze followed by a smaller top-up freeze). The only precondition is that the network has not yet enabled `UNFREEZE_DELAY_DAYS` (Freeze v2), which is a governance-configurable, not universally enforced, state — legacy freeze remains part of the active codebase and is exercised in the actuator's own test suite, indicating it's still a live code path.

### Recommendation
In `FreezeBalanceActuator.execute()` (and equivalently for `TRON_POWER`), compute the new `expireTime` as `max(now + duration, existing expire_time)` — i.e., never permit the new expire time to be earlier than the currently recorded one — mirroring the guard already implemented in `DelegateResourceActuator.validRemainTime()`. Alternatively, reject the transaction in `validate()` if `now + duration < accountCapsule`'s existing `Frozen.expire_time` for that resource.

### Proof of Concept
1. Set `minFrozenTime = 3`, `maxFrozenTime = 30` (days) via `DynamicPropertiesStore`.
2. Account `A` submits `FreezeBalanceContract{frozenBalance=1000 TRX, frozenDuration=30, resource=BANDWIDTH}` at `t=0`. `AccountCapsule.frozen[0].expire_time = 30d`.
3. Advance `LatestBlockHeaderTimestamp` to `t=5d`.
4. Account `A` submits a second `FreezeBalanceContract{frozenBalance=1 TRX, frozenDuration=3, resource=BANDWIDTH}`.
5. Observe `FreezeBalanceActuator.execute()` sets `accountCapsule.frozen[0].expire_time = 5d + 3d = 8d` for the combined `1001 TRX`, instead of preserving `30d`.
6. Advance time to `t=8d` and submit `UnfreezeBalanceContract`; the full `1001 TRX` is released 22 days before the originally committed unlock date.

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

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L97-121)
```java
      case ENERGY:
        if (!ArrayUtils.isEmpty(receiverAddress)
            && dynamicStore.supportDR()) {
          increment = delegateResource(ownerAddress, receiverAddress, false,
                  frozenBalance, expireTime);
          accountCapsule.addDelegatedFrozenBalanceForEnergy(frozenBalance);
        } else {
          long oldEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          long newFrozenBalanceForEnergy =
              frozenBalance + accountCapsule.getEnergyFrozenBalance();
          accountCapsule.setFrozenForEnergy(newFrozenBalanceForEnergy, expireTime);
          long newEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          increment = newEnergyWeight - oldEnergyWeight;
        }
        addTotalWeight(ENERGY, dynamicStore, frozenBalance, increment);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenBalance() / TRX_PRECISION;
        long newFrozenBalanceForTronPower =
            frozenBalance + accountCapsule.getTronPowerFrozenBalance();
        accountCapsule.setFrozenForTronPower(newFrozenBalanceForTronPower, expireTime);
        long newTPWeight = accountCapsule.getTronPowerFrozenBalance() / TRX_PRECISION;
        increment = newTPWeight - oldTPWeight;
        addTotalWeight(TRON_POWER, dynamicStore, frozenBalance, increment);
        break;
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L203-214)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L271-274)
```java
    if (dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException(
              "freeze v2 is open, old freeze is closed");
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
