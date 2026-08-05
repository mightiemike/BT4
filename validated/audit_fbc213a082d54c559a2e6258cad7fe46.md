## Finding

I found a structural analog to the NFTX "bypass timelock via shorter re-lock" bug class in java-tron's legacy TRX freezing/staking accounting code. I was unable to fully confirm one link in the exploit chain within my remaining tool budget (see "Uncertainty" below), so I'm flagging that explicitly rather than overstating confidence.

### Title
Freeze-duration timelock can be silently shortened/overwritten instead of extended - (File: `actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java`, `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java`)

### Summary
The NFTX bug class is: a timelock is recomputed as `now + newDuration` and unconditionally overwrites the previous, longer-remaining timelock, letting a user "re-lock" with a short duration to escape a longer lock. In java-tron's frozen-balance accounting, `AccountCapsule.setFrozenForBandwidth` / `setFrozenForEnergy` / `setFrozenForTronPower` always replace the stored `expireTime` with the newly computed one, with no comparison against the previously stored (potentially further-in-the-future) expire time.

### Finding Description
`AccountCapsule.setFrozenForBandwidth` builds a new `Frozen` record with the caller-supplied `expireTime` and unconditionally replaces the existing entry: [1](#0-0) 

The same unconditional-overwrite pattern exists for energy: [2](#0-1) 

The TVM-facing native-contract path, `FreezeBalanceProcessor` (invoked for smart-contract-triggered freezing), computes `expireTime = now + frozenDuration * FROZEN_PERIOD` and passes it straight into these setters — and critically, unlike the classic `FreezeBalanceActuator`, its `validate()` method performs **no bounds check at all** on `frozenDuration`: [3](#0-2) [4](#0-3) 

By contrast, the ordinary `FreezeBalanceActuator.validate()` does bound `frozenDuration` between `dynamicStore.getMinFrozenTime()` and `getMaxFrozenTime()`, but only when a `needCheckFrozeTime` flag (explicitly commented "for test") is enabled: [5](#0-4) [6](#0-5) 

Because the setter always overwrites `expireTime` (never `max(old, new)`), any code path that lets a user pick an arbitrarily short `frozenDuration` for a second freeze call — while an existing frozen balance is still locked further into the future — would let the user collapse the whole pooled balance's unlock time down to the new, shorter value, exactly mirroring the NFTX `_timelockMint` root cause.

### Impact Explanation
If reachable with an attacker-controlled short duration, this would let a staker unfreeze (and reclaim/vote-manipulate/bandwidth-recycle) TRX earlier than the network's intended minimum freeze period, undermining the resource-staking economic model and vote-weight/withdraw timing guarantees — a genuine accounting/invalid-state impact category.

### Likelihood Explanation
**Uncertain / not fully confirmed.** The legacy `FreezeBalanceActuator` (the ordinary user transaction path) does enforce `minFrozenTime`/`maxFrozenTime` bounds under normal (non-test) configuration, and I could not determine within my available searches whether `minFrozenTime == maxFrozenTime` on mainnet (which would make the duration effectively fixed and thus non-exploitable through that path, since a fixed duration reapplied at a later timestamp can only extend, never shorten, an existing expiry). Separately, `FreezeBalanceProcessor` (the TVM/native-contract path) has no such bound check at all, but I was unable to conclusively confirm, in my last search iteration, that this processor is actually wired to a reachable TVM opcode/precompile invoked from `Program.java` for ordinary contracts — a prior broader grep suggested related matches in `Program.java`, but a follow-up targeted grep for `FreezeBalanceParam`/`freezeBalance(` in that file returned no matches, so this link is unverified.

### Recommendation
Regardless of which path is confirmed reachable, harden the shared root cause: in `AccountCapsule.setFrozenForBandwidth`, `setFrozenForEnergy`, and `setFrozenForTronPower`, only update `expireTime` when the newly computed value is greater than the currently stored one (mirroring the NFTX fix — take `max(existingExpire, now + newDuration)` rather than unconditional overwrite). Additionally, add explicit `frozenDuration` bounds validation to `FreezeBalanceProcessor.validate()` so the TVM-triggered freeze path cannot supply an arbitrarily short duration.

### Proof of Concept
Conceptual (not fully verified against a confirmed reachable entrypoint):
1. Attacker freezes a large TRX balance for the maximum allowed duration via the normal path, obtaining `expireTime = now + maxDuration`.
2. Attacker triggers a second freeze contributing additional (even minimal) frozen balance with the minimum allowed duration through a path where `frozenDuration` is not clamped to match/exceed the remaining lock (e.g., `FreezeBalanceProcessor`, if reachable, since it performs no bound check at all).
3. `setFrozenForBandwidth`/`setFrozenForEnergy` overwrites `expireTime` to `now + minDuration`, shortening the unlock time for the entire pooled frozen balance.
4. Attacker calls unfreeze once the shorter `expireTime` has passed, reclaiming the full pooled balance earlier than the originally committed lock period.

**Caveat:** step 2's exploitability hinges on (a) `minFrozenTime != maxFrozenTime` in the standard actuator path, or (b) confirmed reachability of the unchecked `FreezeBalanceProcessor` path from ordinary smart contracts — neither of which I was able to fully verify with the remaining tool calls. A Devin session with full repo/build access could confirm the default `DynamicPropertiesStore` min/max frozen-time values and trace `Program.java`'s native-contract dispatch table to settle this.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L21-71)
```java
  public void validate(FreezeBalanceParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    // validate arg @frozenBalance
    byte[] ownerAddress = param.getOwnerAddress();
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddress);
    long frozenBalance = param.getFrozenBalance();
    if (frozenBalance <= 0) {
      throw new ContractValidateException("FrozenBalance must be positive");
    } else if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("FrozenBalance must be greater than or equal to 1 TRX");
    } else if (frozenBalance > ownerCapsule.getBalance()) {
      throw new ContractValidateException("FrozenBalance must be less than or equal to accountBalance");
    }

    // validate frozen count of owner account
    int frozenCount = ownerCapsule.getFrozenCount();
    if (frozenCount != 0 && frozenCount != 1) {
      throw new ContractValidateException("FrozenCount must be 0 or 1");
    }

    // validate arg @resourceType
    switch (param.getResourceType()) {
      case BANDWIDTH:
      case ENERGY:
        break;
      default:
        throw new ContractValidateException(
            "Unknown ResourceCode, valid ResourceCode[BANDWIDTH、ENERGY]");
    }

    // validate for delegating resource
    byte[] receiverAddress = param.getReceiverAddress();
    if (!FastByteComparisons.isEqual(ownerAddress, receiverAddress)) {
      param.setDelegating(true);

      // check if receiver account exists. if not, then create a new account
      AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
      if (receiverCapsule == null) {
        receiverCapsule = repo.createNormalAccount(receiverAddress);
      }

      // forbid delegating resource to contract account
      if (receiverCapsule.getType() == Protocol.AccountType.Contract) {
        throw new ContractValidateException(
            "Do not allow delegate resources to contract addresses");
      }
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L73-116)
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
    }
```

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
