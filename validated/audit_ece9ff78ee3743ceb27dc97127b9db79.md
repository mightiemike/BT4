### Title
Existing Frozen Balance Lockup Silently Shortened/Reset on Repeat `FreezeBalanceContract` (V1 Freeze) — Timelock Bypass Analog - (File: `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java`)

### Summary
`FreezeBalanceActuator.execute()` is the closest java-tron analog to the `SapienVault.sol` `stake()`/`increaseAmount()` bug. When an account that already has frozen TRX (with a previously computed `expireTime`) submits another `FreezeBalanceContract` transaction to add more balance to the *same* resource type (bandwidth/energy/tron-power), the actuator combines the balances but **unconditionally overwrites** the stored expiration with a freshly computed `now + duration` — it never checks or preserves the remaining lock time of the existing frozen balance.

### Finding Description
In `execute()`:
```
long expireTime = now + duration;
...
long newFrozenBalanceForBandwidth = frozenBalance + accountCapsule.getFrozenBalance();
accountCapsule.setFrozenForBandwidth(newFrozenBalanceForBandwidth, expireTime);
``` [1](#0-0) 

The same pattern repeats for `ENERGY` and `TRON_POWER`: [2](#0-1) 

`validate()` never inspects the account's existing frozen expiration when merging balances — it only checks `frozenBalance`, `frozenCount` (0 or 1), sufficient balance, and that `frozenDuration` falls in `[minFrozenTime, maxFrozenTime]`: [3](#0-2) 

Because `expireTime` is computed purely from the *new* transaction's `frozenDuration` and simply replaces the stored expiration for the combined (old + new) balance, a user can effectively reset/shorten the unlock time of an existing, still-locked stake by submitting a second small freeze with a shorter allowed duration. This mirrors the reported bug class: adding to an existing locked position without validating/preserving the original lock, letting the combined balance unlock earlier than the original commitment implied.

### Impact Explanation
If exploitable, an account could shorten the effective lock time of already-frozen TRX voting/resource power by re-freezing a small additional amount with a shorter duration, then immediately unfreezing the full combined balance once the new (shorter) expiration passes — effectively bypassing the originally intended lock period for TRON_POWER/bandwidth/energy stakes. This would allow premature unfreezing of resources that were meant to remain locked longer, undermining the freeze/vote-weight locking guarantees that back witness voting and resource allocation.

### Likelihood Explanation
This is constrained in practice: `FreezeBalanceContract` (V1) is the legacy freeze mechanism, and `validate()` explicitly rejects V1 freeze transactions once `dynamicStore.supportUnfreezeDelay()` is enabled (i.e., after the network migrates to Freeze V2): [4](#0-3) 
Also, `frozenDuration` is validated only against a global `[minFrozenTime, maxFrozenTime]` range that is typically fixed (e.g., 3 days) rather than user-arbitrary in most network configurations, which limits the practical window for shortening. On networks/testnets where V1 freeze is still active and min/max duration bounds allow variable durations, this remains reachable via an ordinary signed `FreezeBalanceContract` broadcast transaction from any account — no privileged role needed.

### Recommendation
When merging a new freeze into an existing frozen balance of the same resource type, take `expireTime = max(now + duration, existingExpireTime)` (or reject/require the new duration to be at least the remaining time of the existing lock, as already done for delegated resources in `DelegateResourceActuator.validRemainTime`) instead of unconditionally overwriting the expiration with the new transaction's shorter value.

### Proof of Concept
1. Account A calls `FreezeBalanceContract` with `frozenBalance=X`, `frozenDuration=maxFrozenTime` (long lock), resource=BANDWIDTH → `expireTime1 = now + maxFrozenTime*FROZEN_PERIOD` is stored via `setFrozenForBandwidth`.
2. Later (before `expireTime1`), account A calls `FreezeBalanceContract` again with `frozenBalance=Y` (small), `frozenDuration=minFrozenTime` (short lock), same resource → `execute()` computes `expireTime2 = now2 + minFrozenTime*FROZEN_PERIOD` (earlier than `expireTime1`), and calls `setFrozenForBandwidth(X+Y, expireTime2)`, overwriting the stored expiration for the entire `X+Y` balance.
3. Once `expireTime2` passes (well before the originally committed `expireTime1`), account A can submit `UnfreezeBalanceContract`, which checks only `next.getExpireTime() <= now` per `Frozen` entry, and unfreezes the full `X+Y` balance early. [5](#0-4) 

Note: I was unable to fully confirm the runtime values of `minFrozenTime`/`maxFrozenTime` (i.e., whether they are fixed equal to 3 in mainnet config, which would eliminate any variable-duration window) before running out of tool budget — this bounds the real-world likelihood and should be verified against `DynamicPropertiesStore.getMinFrozenTime()`/`getMaxFrozenTime()` defaults and any governance proposals altering them.

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

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L187-214)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L271-274)
```java
    if (dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException(
              "freeze v2 is open, old freeze is closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L199-213)
```java
          long now = dynamicStore.getLatestBlockHeaderTimestamp();
          while (iterator.hasNext()) {
            Frozen next = iterator.next();
            if (next.getExpireTime() <= now) {
              unfreezeBalance += next.getFrozenBalance();
              iterator.remove();
            }
          }

          accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
              .setBalance(oldBalance + unfreezeBalance)
              .clearFrozen().addAllFrozen(frozenList).build());
          long newNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          decrease = newNetWeight - oldNetWeight;
          break;
```
