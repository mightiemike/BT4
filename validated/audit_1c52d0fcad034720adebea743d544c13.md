### Title
Unlock-bucket-only lookup in `UnDelegateResourceProcessor.validate` causes spurious revert of TVM `unDelegateResource` for expired locked delegations - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java])

### Finding Description
`UnDelegateResourceProcessor.validate` and `execute` only look up the delegated-resource record via `DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false)` (the unlock/`lock=false` bucket), and never call `DelegatedResourceStore.unLockExpireResource` to migrate expired `lock=true` balances into the unlock bucket before checking availability. [1](#0-0) 

By contrast, `UnDelegateResourceActuator.validate` explicitly reads both the lock (`lock=true`) and unlock (`lock=false`) buckets and adds the locked balance to the eligible amount whenever `getExpireTimeForBandwidth()/getExpireTimeForEnergy() < now`, and `UnDelegateResourceActuator.execute` calls `delegatedResourceStore.unLockExpireResource(...)` prior to mutating state, which moves the expired locked amount into the unlock record. [2](#0-1) [3](#0-2) 

The migration logic lives in `DelegatedResourceStore.unLockExpireResource`, which is only invoked from the plain-transaction actuator path (and `DelegateResourceActuator`), never from `UnDelegateResourceProcessor`. [4](#0-3) 

Consequently, if an owner delegates BANDWIDTH/ENERGY with `lock=true` and the lock subsequently expires without a normal `UnDelegateResourceContract` transaction ever running against that pair (so the unlock bucket at `lock=false` is still empty), a TVM contract calling `Program.unDelegateResource` (native-contract path calling `UnDelegateResourceProcessor.validate`) will find `repo.getDelegatedResource(key)` (for `lock=false`) returns `null` and revert with `"delegated Resource does not exist"`, even though the equivalent `UnDelegateResourceActuator` would succeed against the identical on-chain state.

### Impact Explanation
This is a functional-correctness/API-parity bug: a smart-contract-driven undelegate flow for resources with an expired lock will unconditionally revert, breaking any dApp/wallet logic that relies on Solidity-level `unDelegateResource` (via `Program.unDelegateResource`) to reclaim expired locked delegations. However, the underlying funds/resources are not permanently frozen: the same owner can still submit a normal `UnDelegateResourceContract` transaction, whose `execute` path calls `unLockExpireResource` and correctly reclaims the locked balance. The bug therefore causes a state/behavior divergence and transaction revert for the TVM entrypoint, not a permanent, unrecoverable loss of user funds or resources, since an alternate (non-contract) path recovers the funds.

### Likelihood Explanation
Highly reproducible: any account that (1) calls `freezeBalanceV2`, (2) `delegateResource` with `lock=true`, (3) waits past the lock's expire time, and (4) never issues a plain `UnDelegateResourceContract` for that owner/receiver pair before invoking `unDelegateResource` from a contract, will trigger the revert deterministically. This requires no privileged access — any user can trigger it through a deployed contract calling the precompiled/native `unDelegateResource` opcode path.

### Recommendation
Modify `UnDelegateResourceProcessor.validate` and `execute` to mirror `UnDelegateResourceActuator`: look up both the `lock=true` and `lock=false` `DelegatedResourceCapsule` records, treat balances in the lock bucket with expired `expireTimeForBandwidth`/`expireTimeForEnergy` as available, and invoke the equivalent of `DelegatedResourceStore.unLockExpireResource` (via the `Repository` abstraction) before checking/consuming the unlock-bucket balance, so that TVM-triggered undelegation of expired locked balances succeeds consistently with the plain-transaction actuator.

### Proof of Concept
Differential Java unit test plan (JUnit, using existing test infra similar to `FreezeV2Test`):
1. Set up chain state: `AccountCapsule` owner with sufficient TRX, call `freezeBalanceV2` equivalent to create `TRON_POWER`/frozen V2 balance.
2. Create a `DelegatedResourceCapsule` at `createDbKeyV2(owner, receiver, true)` with `frozenBalanceForBandwidth = X`, `expireTimeForBandwidth = T0` (in the past relative to `now`), and ensure no capsule exists at `createDbKeyV2(owner, receiver, false)`.
3. Set `dynamicStore.getLatestBlockHeaderTimestamp()` / `repo.getHeadSlot()` to a time after `T0`.
4. Branch A (actuator path): build `UnDelegateResourceContract` with `balance = X`, call `UnDelegateResourceActuator.validate()` then `.execute()`. Assert both succeed (`validate()` returns `true`, `execute()` returns `true`, and the owner's `FrozenV2BalanceForBandwidth` increases by `X`).
5. Branch B (TVM/native path): reset store to the identical initial state (only the lock-bucket capsule exists). Build `UnDelegateResourceParam` with the same owner/receiver/`unDelegateBalance = X`/`BANDWIDTH`, call `new UnDelegateResourceProcessor().validate(param, repo)`. Assert a `ContractValidateException` with message `"delegated Resource does not exist"` is thrown, demonstrating the revert.
6. Assertion of divergence: Branch A succeeds while Branch B throws on identical underlying `DelegatedResourceCapsule` state, proving the TVM path incorrectly rejects a valid undelegate that the plain-transaction actuator accepts.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L61-66)
```java
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule delegatedResourceCapsule = repo.getDelegatedResource(key);
    if (delegatedResourceCapsule == null) {
      throw new ContractValidateException(
          "delegated Resource does not exist");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L124-131)
```java
    // transfer lock delegate to unlock
    delegatedResourceStore.unLockExpireResource(ownerAddress, receiverAddress,
        dynamicStore.getLatestBlockHeaderTimestamp());

    byte[] unlockKey = DelegatedResourceCapsule
        .createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule unlockResource = delegatedResourceStore
        .get(unlockKey);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L255-299)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule unlockResourceCapsule = delegatedResourceStore.get(key);
    byte[] lockKey = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, true);
    DelegatedResourceCapsule lockResourceCapsule = delegatedResourceStore.get(lockKey);
    if (unlockResourceCapsule == null && lockResourceCapsule == null) {
      throw new ContractValidateException(
          "delegated Resource does not exist");
    }

    long unDelegateBalance = unDelegateResourceContract.getBalance();
    if (unDelegateBalance <= 0) {
      throw new ContractValidateException("unDelegateBalance must be more than 0 TRX");
    }
    switch (unDelegateResourceContract.getResource()) {
      case BANDWIDTH: {
        long delegateBalance = 0;
        if (unlockResourceCapsule != null) {
          delegateBalance += unlockResourceCapsule.getFrozenBalanceForBandwidth();
        }
        if (lockResourceCapsule != null
            && lockResourceCapsule.getExpireTimeForBandwidth() < now) {
          delegateBalance += lockResourceCapsule.getFrozenBalanceForBandwidth();
        }
        if (delegateBalance < unDelegateBalance) {
          throw new ContractValidateException(
              "insufficient delegatedFrozenBalance(BANDWIDTH), request="
                  + unDelegateBalance + ", unlock_balance=" + delegateBalance);
        }
      }
      break;
      case ENERGY: {
        long delegateBalance = 0;
        if (unlockResourceCapsule != null) {
          delegateBalance += unlockResourceCapsule.getFrozenBalanceForEnergy();
        }
        if (lockResourceCapsule != null
            && lockResourceCapsule.getExpireTimeForEnergy() < now) {
          delegateBalance += lockResourceCapsule.getFrozenBalanceForEnergy();
        }
        if (delegateBalance < unDelegateBalance) {
          throw new ContractValidateException("insufficient delegateFrozenBalance(Energy), request="
              + unDelegateBalance + ", unlock_balance=" + delegateBalance);
        }
      }
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
