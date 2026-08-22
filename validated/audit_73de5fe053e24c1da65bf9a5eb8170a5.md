Based on my research, I found a legitimate analog in java-tron's resource delegation mechanism.

### Title
Delegated resource lock time can be shortened by re-delegating with a shorter/zero duration - (File: chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java)

### Summary
`DelegatedResourceCapsule.addFrozenBalanceForBandwidth`/`addFrozenBalanceForEnergy` unconditionally overwrite the stored `expireTimeForBandwidth`/`expireTimeForEnergy` with whatever new `expireTime` is passed in, exactly like the vulnerable `_timelockMint` in the referenced NFTX report, without checking that the new expiry is later than the existing one. [1](#0-0) [2](#0-1) 

### Finding Description
Both the legacy `FreezeBalanceActuator` (RPC-broadcast `FreezeBalanceContract`) and the TVM native contract `FreezeBalanceProcessor` (reachable via a contract call, e.g. Solidity `freezeBalance` precompile call) delegate resources by computing `expireTime = now + frozenDuration * FROZEN_PERIOD` and calling `addFrozenBalanceForBandwidth`/`addFrozenBalanceForEnergy` on the existing `DelegatedResourceCapsule` for the (owner, receiver) pair: [3](#0-2) 

The setter always replaces the expiry field with the freshly computed value, regardless of whether a prior, later-expiring delegation already exists for that key: [2](#0-1) 

Because `frozenDuration` is fully controller-chosen on every call, an owner who has an existing delegation with a long remaining lock (e.g. after freezing with a large `frozenDuration`) can issue a second `FreezeBalanceContract`/TVM `freezeBalance` call to the same receiver with the minimum allowed `frozenDuration`, which recomputes `expireTime = now + minimalDuration` and overwrites the previously-longer `expireTimeForBandwidth`/`expireTimeForEnergy` for the *entire* accumulated delegated balance, not just the newly added portion. This is structurally identical to the NFTX bug where a fresh `deposit` with a short lock length overwrote a longer existing timelock in `XTokenUpgradeable._timelockMint`.

Notably, the newer `DelegateResourceActuator` (v2, TRC-10 "lock" delegation) already implements the correct mitigation via `validRemainTime`, which rejects a new lock period shorter than the remaining time of the current lock: [4](#0-3) 
This confirms the project is aware of exactly this class of issue for the v2 path, but the same guard is absent from the legacy `FreezeBalanceActuator`/`FreezeBalanceProcessor` delegation path that manipulates `DelegatedResourceCapsule.addFrozenBalanceForBandwidth/Energy` directly.

The corresponding unfreeze validation (`UnfreezeBalanceProcessor.validate` and `UnfreezeBalanceActuator`) only checks `expireTimeForBandwidth`/`expireTimeForEnergy` against `now`, so once the expiry is shortened via the above overwrite, the owner (or account triggering unfreeze) can reclaim/unlock the delegated TRX and its associated resource weight earlier than the lock duration originally committed to: [5](#0-4) 

### Impact Explanation
Any account that delegated resources (bandwidth/energy) to another address with an intended long lock can silently shorten that lock to the chain's minimum freeze duration by issuing another small freeze/delegate transaction to the same receiver. This breaks the guarantee that delegated resources remain locked for the declared duration, undermines protocols/receivers relying on that lock (e.g., resource-rental or staking-commitment logic built on top of delegation), and lets the owner reclaim the underlying TRX (via `UnfreezeBalance`) earlier than promised, causing incorrect resource/asset accounting and consensus-visible state divergence between expected and actual lock duration.

### Likelihood Explanation
Likelihood is moderate-to-high: any account with delegation ability (broadcasting a plain `FreezeBalanceContract`, or calling the `freezeBalance` native/precompiled contract from a smart contract) can trigger this with no privileged access, no crafted signature abuse, and minimal cost — just a second freeze/delegate transaction to the same receiver with a smaller `frozenDuration`.

### Recommendation
In `DelegatedResourceCapsule.addFrozenBalanceForBandwidth`/`addFrozenBalanceForEnergy` (and any equivalent code path), only update the stored expire time if the newly computed `expireTime` is greater than the currently stored one, mirroring the fix pattern applied in `DelegateResourceActuator.validRemainTime` for the v2 lock path:
```java
public void addFrozenBalanceForBandwidth(long bandwidth, long expireTime) {
  long currentExpire = this.delegatedResource.getExpireTimeForBandwidth();
  this.delegatedResource = this.delegatedResource.toBuilder()
      .setFrozenBalanceForBandwidth(this.delegatedResource.getFrozenBalanceForBandwidth() + bandwidth)
      .setExpireTimeForBandwidth(Math.max(currentExpire, expireTime))
      .build();
}
```
Apply the analogous fix for `addFrozenBalanceForEnergy`, and consider adding an explicit validation step (like `validRemainTime`) in `FreezeBalanceActuator` and `FreezeBalanceProcessor` to reject a new `frozenDuration` shorter than the remaining lock time when a prior delegation to the same receiver still exists.

### Proof of Concept
1. Account A freezes/delegates 1,000 TRX of bandwidth to account B with `frozenDuration` = long value (e.g. many `FROZEN_PERIOD`s), producing `expireTimeForBandwidth = now + longDuration` in the `DelegatedResourceCapsule` for (A,B). [6](#0-5) 
2. Account A immediately issues a second `FreezeBalanceContract`/`freezeBalance` call, delegating a minimal additional amount (e.g. 1 TRX) to the same receiver B with the minimum allowed `frozenDuration`.
3. `addFrozenBalanceForBandwidth` is invoked again and overwrites `expireTimeForBandwidth` to `now + minimalDuration`, shortening the lock for the full 1,001 TRX balance. [2](#0-1) 
4. Once `now >= expireTimeForBandwidth` (which is now much sooner than the original commitment), account A calls `UnfreezeBalance`, which only checks `expireTimeForBandwidth <= now` and allows full reclamation of the funds far earlier than intended. [5](#0-4)

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java (L63-75)
```java
  public void setFrozenBalanceForEnergy(long energy, long expireTime) {
    this.delegatedResource = this.delegatedResource.toBuilder()
        .setFrozenBalanceForEnergy(energy)
        .setExpireTimeForEnergy(expireTime)
        .build();
  }

  public void addFrozenBalanceForEnergy(long energy, long expireTime) {
    this.delegatedResource = this.delegatedResource.toBuilder()
        .setFrozenBalanceForEnergy(this.delegatedResource.getFrozenBalanceForEnergy() + energy)
        .setExpireTimeForEnergy(expireTime)
        .build();
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java (L90-103)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L73-99)
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L45-55)
```java
      // validate args @frozenBalance and @expireTime
      switch (param.getResourceType()) {
        case BANDWIDTH:
          // validate frozen balance
          if (delegatedResourceCapsule.getFrozenBalanceForBandwidth() <= 0) {
            throw new ContractValidateException("no delegatedFrozenBalance(BANDWIDTH)");
          }
          // check if it is time to unfreeze
          if (delegatedResourceCapsule.getExpireTimeForBandwidth() > now) {
            throw new ContractValidateException("It's not time to unfreeze(BANDWIDTH).");
          }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L69-95)
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
        addTotalWeight(BANDWIDTH, dynamicStore, frozenBalance, increment);
```
