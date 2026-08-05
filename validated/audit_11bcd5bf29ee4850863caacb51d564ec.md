### Title
TVM `unDelegateResource` precompile reverts on expired-lock delegations that the equivalent on-chain transaction actuator would successfully unlock - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java])

### Summary
`UnDelegateResourceProcessor.validate()`/`execute()` only reads and mutates the unlocked-bucket `DelegatedResourceCapsule` (`createDbKeyV2(owner, receiver, false)`) and never checks the locked bucket or calls `DelegatedResourceStore.unLockExpireResource`, unlike `UnDelegateResourceActuator`, which explicitly migrates expired-lock balance from the locked bucket to the unlocked bucket via `unLockExpireResource` before validating and executing. As a result, if an owner only ever delegated with `lock=true` (no unlock-bucket record exists) and the lock has since expired, a TVM contract call to the `unDelegateResource` precompile fails validation with `"delegated Resource does not exist"`, even though an ordinary `UnDelegateResourceContract` transaction on identical state would succeed.

### Finding Description
The plain-transaction path, `UnDelegateResourceActuator.execute()`, always calls `delegatedResourceStore.unLockExpireResource(ownerAddress, receiverAddress, dynamicStore.getLatestBlockHeaderTimestamp())` before reading the unlock bucket [1](#0-0) , and its `validate()` additionally inspects the locked bucket directly, adding `lockResourceCapsule.getFrozenBalanceForBandwidth()/Energy()` to the available balance whenever `getExpireTimeForBandwidth()/getExpireTimeForEnergy() < now` [2](#0-1) .

In contrast, `UnDelegateResourceProcessor.validate()` (invoked from the TVM `unDelegateResource` native precompile) only fetches the unlock-bucket key (`lock=false`) via `repo.getDelegatedResource(key)` and throws `"delegated Resource does not exist"` if that capsule is `null`, with no reference to the lock bucket or `unLockExpireResource` at all [3](#0-2) . `execute()` mirrors this: it re-fetches the same unlock-bucket key and mutates only that capsule [4](#0-3) , never touching or migrating the locked bucket.

Given the precondition — owner calls `freezeBalanceV2` then `delegateResource(lock=true)`, and the lock subsequently expires without any prior `UnDelegateResourceContract` transaction having been sent (so the unlock-bucket capsule for that `(owner, receiver)` pair was never created) — the locked-bucket capsule exists and holds the entire balance, but the unlock-bucket capsule is `null`. A contract invoking the `unDelegateResource` precompile in this state hits `delegatedResourceCapsule == null` and reverts with `"delegated Resource does not exist"`, whereas an ordinary `UnDelegateResourceContract` transaction on the same state would first call `unLockExpireResource` to migrate the expired balance into the unlock bucket and then succeed.

### Impact Explanation
This is a functional divergence between the two execution paths for what should be equivalent semantics: a legitimate, valid un-delegate request issued from within a smart contract is incorrectly rejected purely because it goes through the TVM precompile rather than a top-level transaction. Any protocol or wallet-contract logic that relies on the `unDelegateResource` precompile to reclaim expired locked resources will unexpectedly revert, breaking composability/automation built on top of resource delegation (e.g., contracts managing delegation for third parties, or contract accounts that are the resource owner and thus cannot issue a normal signed transaction as themselves). This is not able to be fixed by any other action from within the same TVM call, since the precompile is the only interface available to contract code.

### Likelihood Explanation
Fully reachable by any unprivileged account: freeze with `lock=true`, delegate, wait past the lock's expiry, then have any contract (deployed by anyone) invoke the `unDelegateResource` precompile. No special privileges are required, and the divergence is deterministic and 100% reproducible whenever no unlock-bucket record already exists for that `(owner, receiver)` pair at the time of the call.

### Recommendation
Update `UnDelegateResourceProcessor.validate()` and `execute()` to mirror `UnDelegateResourceActuator`: call `repo`'s equivalent of `DelegatedResourceStore.unLockExpireResource` (or otherwise account for/migrate the locked-bucket balance when `getExpireTimeForBandwidth()/getExpireTimeForEnergy() < now`) before checking existence and balance, so TVM-triggered un-delegation of expired locked balances behaves identically to the plain-transaction actuator.

### Proof of Concept
Differential JUnit test in `actuator` module:
1. Set up a `Repository`/`ChainBaseManager` test fixture; create `AccountCapsule` for owner and receiver; freeze via `freezeBalanceV2`-equivalent state setup, then create a `DelegatedResourceCapsule` at `createDbKeyV2(owner, receiver, true)` with `frozenBalanceForBandwidth = X` and `expireTimeForBandwidth` set in the past (already expired). Ensure no capsule exists at `createDbKeyV2(owner, receiver, false)`.
2. Path A: Build an `UnDelegateResourceContract` for `(owner, receiver, balance=X, BANDWIDTH)`, run `UnDelegateResourceActuator.validate()` then `execute()` — assert it returns successfully and owner's `FrozenBalanceForBandwidthV2` increases by `X`.
3. Path B: On identical initial state, build an `UnDelegateResourceParam` with the same values and call `UnDelegateResourceProcessor.validate(param, repo)` — assert it throws `ContractValidateException("delegated Resource does not exist")`.
4. Assertion confirms the divergence: identical on-chain state produces success via the actuator path and failure via the TVM processor path, demonstrating that TVM-triggered un-delegation of expired-lock-only balances is incorrectly blocked.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L255-304)
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
      break;
      default:
        throw new ContractValidateException(
            "ResourceCode error.valid ResourceCode[BANDWIDTH、Energy]");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L61-66)
```java
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule delegatedResourceCapsule = repo.getDelegatedResource(key);
    if (delegatedResourceCapsule == null) {
      throw new ContractValidateException(
          "delegated Resource does not exist");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L160-165)
```java
    // modify owner Account
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule delegatedResourceCapsule = repo.getDelegatedResource(key);
    switch (param.getResourceType()) {
      case BANDWIDTH: {
        delegatedResourceCapsule.addFrozenBalanceForBandwidth(-unDelegateBalance, 0);
```
