### Title
Index-clearing in `UnDelegateResourceProcessor.execute` ignores coexisting locked delegation, orphaning it from V2 account-index enumeration - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java])

### Summary
`UnDelegateResourceProcessor.execute`, the native-contract (TVM) path for undelegating stake, wipes the `DelegatedResourceAccountIndexCapsule` FROM/TO entries whenever the unlocked (`lock=false`) `DelegatedResourceCapsule` reaches zero balance, without checking whether a separate locked (`lock=true`) capsule for the same owner/receiver pair still holds a nonzero balance. This diverges from the regular transaction path (`UnDelegateResourceActuator`), which explicitly guards this clearing with `lockResource == null && unlockResource == null`.

### Finding Description
In `UnDelegateResourceProcessor.execute`, the code fetches only the unlocked capsule via `DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false)`: [1](#0-0) 

After decrementing the balance, it decides whether to clear the account-index entries based solely on that unlocked capsule's balances being zero: [2](#0-1) 

It never loads or checks the `lock=true` capsule (`createDbKeyV2(ownerAddress, receiverAddress, true)`) before wiping the FROM/TO index entries to `new byte[0]`.

By contrast, the transaction-based `UnDelegateResourceActuator.execute` explicitly fetches both the unlock and lock capsules and only clears the index when **both** are absent/zero: [3](#0-2) 

This confirms the omission in the native-contract processor is a real logic gap relative to the intended invariant (index entries should persist as long as any locked-or-unlocked delegation balance for the pair exists), not an intentional design difference.

The native contract processor is invoked from the TVM `Program` execution path (confirmed by references to `UnDelegateResourceProcessor` in `actuator/src/main/java/org/tron/core/vm/program/Program.java`), meaning it is reachable by any unprivileged smart contract that calls the corresponding Stake 2.0 native/precompiled undelegate functionality — no special privilege is required beyond deploying/calling a contract.

### Impact Explanation
If an owner has both a `lock=true` and `lock=false` `DelegatedResourceCapsule` for the same receiver, and fully undelegates only the unlocked portion via the TVM native-contract path, the FROM/TO `DelegatedResourceAccountIndexCapsule` entries are overwritten with empty (`byte[0]`) lists even though the locked delegation with nonzero balance still exists. Any wallet, explorer, or API relying on the V2 account index (e.g. `GetDelegatedResourceServlet`, `Wallet` index-based enumeration) to discover delegated pairs for withdrawal/management UIs will no longer see this pair, even though a nonzero locked balance remains in the `DelegatedResourceStore`. This is a state-divergence bug: on-chain economic value (the locked delegated balance) still exists and is still functionally unfreezable/undelegatable by directly-keyed operations, but it becomes undiscoverable through the standard index-driven enumeration path, degrading data integrity for any tooling that trusts the index as the source of truth for delegated pairs.

### Likelihood Explanation
Preconditions are straightforward and fully attacker-controlled: the attacker (an owner account acting through a smart contract) must have delegated resources to the same receiver twice — once with `lock=true` and once with `lock=false` (a standard, permitted API usage pattern) — then fully undelegate the unlocked portion via the native-contract call path. No special permissions, races, or admin actions are required; it is deterministically reproducible on any node.

### Recommendation
Mirror the guard used in `UnDelegateResourceActuator`: before clearing the FROM/TO index entries in `UnDelegateResourceProcessor.execute`, also fetch the `lock=true` capsule via `DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, true)` and only clear the index when both the unlocked and locked capsules are null/zero-balance.

### Proof of Concept
Integration test (Java, similar to `UnDelegateResourceActuatorTest`):
1. Create `AccountCapsule` for owner and receiver; store both in `AccountStore`.
2. Create a `DelegatedResourceCapsule` with `lock=false` and nonzero `frozenBalanceForBandwidth` at key `createDbKeyV2(owner, receiver, false)`.
3. Create a second `DelegatedResourceCapsule` with `lock=true` and nonzero `frozenBalanceForBandwidth` at key `createDbKeyV2(owner, receiver, true)`.
4. Populate the V2 FROM/TO `DelegatedResourceAccountIndexCapsule` entries for owner/receiver with the pair present.
5. Build an `UnDelegateResourceParam` with `unDelegateBalance` equal to the full unlocked balance, and invoke `UnDelegateResourceProcessor.execute(param, repo)` through the `RepositoryImpl`/native-contract path used by TVM.
6. Assert: `delegatedResourceAccountIndexStore` (or `repo`'s equivalent) FROM/TO entries for owner/receiver are now empty (`byte[0]`) — reproducing the bug.
7. Assert: the `lock=true` capsule at `createDbKeyV2(owner, receiver, true)` still has nonzero `frozenBalanceForBandwidth` in `DelegatedResourceStore`, proving orphaned/undiscoverable locked balance despite intact index-less funds.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L161-162)
```java
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule delegatedResourceCapsule = repo.getDelegatedResource(key);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L195-206)
```java
    if (delegatedResourceCapsule.getFrozenBalanceForBandwidth() == 0
        && delegatedResourceCapsule.getFrozenBalanceForEnergy() == 0) {
      //modify DelegatedResourceAccountIndex
      byte[] fromKey = Bytes.concat(
          DelegatedResourceAccountIndexStore.getV2_FROM_PREFIX(), ownerAddress, receiverAddress);
      repo.updateDelegatedResourceAccountIndex(
          fromKey, new DelegatedResourceAccountIndexCapsule(new byte[0]));
      byte[] toKey = Bytes.concat(
          DelegatedResourceAccountIndexStore.getV2_TO_PREFIX(), receiverAddress, ownerAddress);
      repo.updateDelegatedResourceAccountIndex(
          toKey, new DelegatedResourceAccountIndexCapsule(new byte[0]));
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L178-185)
```java
    byte[] lockKey = DelegatedResourceCapsule
        .createDbKeyV2(ownerAddress, receiverAddress, true);
    DelegatedResourceCapsule lockResource = delegatedResourceStore
        .get(lockKey);
    if (lockResource == null && unlockResource == null) {
      //modify DelegatedResourceAccountIndexStore
      delegatedResourceAccountIndexStore.unDelegateV2(ownerAddress, receiverAddress);
    }
```
