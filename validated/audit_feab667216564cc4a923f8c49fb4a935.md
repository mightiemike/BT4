### Title
Unprivileged shortening of the frozen-balance timelock via a second `FreezeBalanceContract` call - ([File: actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java])

### Summary
`FreezeBalanceActuator` merges every new freeze into the account's existing frozen balance for a resource, but recomputes the whole balance's `expireTime` from only the *current* transaction's requested duration, unconditionally overwriting the previously-stored (potentially longer) `expireTime`. This is the same root-cause class as the GNTDeposit bug: a subsequent "deposit" (freeze) into an already time-locked balance does not preserve/extend the existing lock — here it can actively **shorten** it, letting the whole combined balance be unfrozen earlier than the original lock committed to.

### Finding Description
When a user calls `FreezeBalanceContract` for BANDWIDTH/ENERGY/TRON_POWER without delegation, the actuator computes:

```java
long expireTime = now + duration;      // duration comes from THIS tx only
...
long newFrozenBalanceForBandwidth = frozenBalance + accountCapsule.getFrozenBalance();
accountCapsule.setFrozenForBandwidth(newFrozenBalanceForBandwidth, expireTime);
``` [1](#0-0) 

`setFrozenForBandwidth` (and the analogous `setFrozenForEnergy`/`setFrozenForTronPower`) always writes a *single* `Frozen` slot, replacing the old entry's `expireTime` with whatever is passed in, with no comparison against the previous, possibly-later expiry: [2](#0-1) 

The same pattern (single shared `Frozen`/balance value updated with the latest call's `expireTime`) exists for ENERGY and TRON_POWER in the same actuator [3](#0-2) , and is mirrored in the TVM-native path `FreezeBalanceProcessor.execute` [4](#0-3) .

`UnfreezeBalanceActuator.validate` then only checks the (single, shared) `expireTime` against `now` to decide if the whole combined balance may be unfrozen:
```java
long allowedUnfreezeCount = accountCapsule.getFrozenList().stream()
    .filter(frozen -> frozen.getExpireTime() <= now).count();
...
if (frozenBalanceForEnergy.getExpireTime() > now) { throw ... }
``` [5](#0-4) 

Because the code never enforces `expireTime = max(oldExpireTime, newExpireTime)`, and `frozenDuration` is only bounded by a configurable `[minFrozenTime, maxFrozenTime]` range (validated in `FreezeBalanceActuator.validate`) [6](#0-5) , whenever `minFrozenTime < maxFrozenTime` a user can:

1. Freeze a large amount X at `t0` choosing `maxFrozenTime`, producing `expireTime1 = t0 + maxFrozenTime`.
2. Later, near `t1` (before `expireTime1`), freeze a small additional amount at `minFrozenTime`, producing `expireTime2 = t1 + minFrozenTime`, which is **earlier** than `expireTime1` when `t1` is close enough to `expireTime1` and `minFrozenTime < maxFrozenTime - (t1 - t0)`.
3. `setFrozenForBandwidth`/`setFrozenForEnergy` overwrites the account's single `Frozen` entry with `expireTime2` for the *entire combined balance* (X + the small top-up).
4. `UnfreezeBalanceActuator` will now permit unfreezing the entire combined balance (including the large amount X) as soon as `expireTime2` passes — earlier than the `maxFrozenTime` lock the user/network originally committed to for X.

This exactly mirrors the GNTDeposit flaw: `onTokenReceived`/`FreezeBalanceActuator` both simply add to an aggregate balance without tracking or reconciling per-deposit locks, so a later, shorter-locked deposit resets (here, shortens) the effective lock applied to the whole pool.

### Impact Explanation
The freeze/timelock mechanism exists to prevent rapid freeze→vote/acquire-resource→unfreeze cycling (anti flash-freeze abuse for voting power and bandwidth/energy acquisition) — that's the reason `minFrozenTime`/`maxFrozenTime` and `expireTime` gating exist at all. This bug lets an unprivileged account defeat its own committed lock duration and withdraw (unfreeze) previously time-locked TRX earlier than intended, undermining that anti-gaming invariant and the accounting guarantee that frozen TRX remains locked for the duration chosen at freeze time. It is a state/accounting-integrity violation in a widely used, unprivileged-user-reachable actuator (`FreezeBalanceActuator`/`FreezeBalanceProcessor`), not a theoretical or trusted-role issue.

### Likelihood Explanation
The path is reachable by any account via a plain `FreezeBalanceContract` transaction (and via `freezeBalance` from the TVM native-contract precompile), requires no special privileges, and only depends on the network allowing `minFrozenTime < maxFrozenTime` (a governance-configurable range that the validate logic explicitly supports) [6](#0-5) . No race condition or privileged setup is needed — two sequential freeze transactions from the same account suffice.

### Recommendation
When combining an incoming freeze with an existing frozen balance, set the resulting `expireTime` to `Math.max(existingExpireTime, newExpireTime)` rather than unconditionally overwriting it, in `FreezeBalanceActuator.execute`, `FreezeBalanceProcessor.execute`, and the corresponding `AccountCapsule.setFrozenForBandwidth`/`setFrozenForEnergy`/`setFrozenForTronPower` setters. Alternatively, track per-deposit expire times (granular locking) instead of collapsing all freezes into one aggregate `Frozen` record.

### Proof of Concept
1. Committee/network configured with `minFrozenTime = 3` days, `maxFrozenTime = 30` days (values are configurable via `DynamicPropertiesStore`).
2. Account A sends `FreezeBalanceContract` for `1,000,000 TRX`, resource=BANDWIDTH, `frozenDuration = 30` → `expireTime1 = now + 30d` stored via `setFrozenForBandwidth` [7](#0-6) .
3. 27 days later, account A sends another `FreezeBalanceContract`, resource=BANDWIDTH, `frozenBalance = 1 TRX`, `frozenDuration = 3` → `expireTime2 = now + 3d`, i.e. 3 days from now, which is earlier than the original `expireTime1` (which would have been 3 days later than `expireTime2`).
4. The actuator overwrites the single `Frozen` slot for A with `{balance: 1,000,001 TRX, expireTime: expireTime2}` [2](#0-1) .
5. After `expireTime2` (3 days later, i.e., 3 days *before* the originally intended `expireTime1`), account A calls `UnfreezeBalanceContract` for BANDWIDTH; `UnfreezeBalanceActuator.validate` passes because `frozen.getExpireTime() <= now` [8](#0-7) , and the full `1,000,001 TRX` is returned to A's spendable balance — 3 days earlier than the 30-day lock originally chosen for the bulk of the funds.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L69-93)
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L73-115)
```java
  public void execute(FreezeBalanceParam param,  Repository repo) {
    // calculate expire time
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    long nowInMs = dynamicStore.getLatestBlockHeaderTimestamp();
    long expireTime = nowInMs + param.getFrozenDuration() * FROZEN_PERIOD;

    byte[] ownerAddress = param.getOwnerAddress();
    byte[] receiverAddress = param.getReceiverAddress();
    long frozenBalance = param.getFrozenBalance();
    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    // acquire or delegate resource
    if (param.isDelegating()) { // delegate resource
      switch (param.getResourceType()) {
        case BANDWIDTH:
          delegateResource(ownerAddress, receiverAddress,
              frozenBalance, expireTime, true, repo);
          accountCapsule.addDelegatedFrozenBalanceForBandwidth(frozenBalance);
          break;
        case ENERGY:
          delegateResource(ownerAddress, receiverAddress,
              frozenBalance, expireTime, false, repo);
          accountCapsule.addDelegatedFrozenBalanceForEnergy(frozenBalance);
          break;
        default:
          logger.debug("Resource Code Error.");
      }
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L436-457)
```java
      switch (unfreezeBalanceContract.getResource()) {
        case BANDWIDTH:
          if (accountCapsule.getFrozenCount() <= 0) {
            throw new ContractValidateException("no frozenBalance(BANDWIDTH)");
          }

          long allowedUnfreezeCount = accountCapsule.getFrozenList().stream()
              .filter(frozen -> frozen.getExpireTime() <= now).count();
          if (allowedUnfreezeCount <= 0) {
            throw new ContractValidateException("It's not time to unfreeze(BANDWIDTH).");
          }
          break;
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
