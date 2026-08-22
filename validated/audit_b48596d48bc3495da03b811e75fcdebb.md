## Title
Unbounded growth of `DelegatedResourceStore` via TVM `UnDelegateResourceProcessor` (native contract) — zero-balance delegation records are never deleted - (File: `actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java`)

### Summary
The direct-transaction path for undelegating resources (`UnDelegateResourceActuator`) correctly deletes a `DelegatedResourceCapsule` entry from `DelegatedResourceStore` once both `frozenBalanceForBandwidth` and `frozenBalanceForEnergy` reach zero. However, the equivalent logic reachable from smart-contract execution (the TVM native contract path, `UnDelegateResourceProcessor`) never deletes the record — it always writes it back with `repo.updateDelegatedResource(key, delegatedResourceCapsule)`, even when the balance is zero. Because `delegateResource`/`unDelegateResource` can be invoked repeatedly, permissionlessly, and cheaply by any contract to any non-contract receiver address, this creates the same unbounded-storage-growth condition described in the referenced Aleo `delegated[]` finding.

### Finding Description
`UnDelegateResourceActuator.execute()` (direct transaction path) explicitly removes the delegated-resource key when the balance reaches 0: [1](#0-0) 

The `UnfreezeBalanceActuator` legacy path does the same: [2](#0-1) 

In contrast, the TVM native-contract processor `UnDelegateResourceProcessor.execute()` only clears the (separately-keyed) `DelegatedResourceAccountIndex` entries but *always* calls `repo.updateDelegatedResource(key, delegatedResourceCapsule)` on the now-zero-balance capsule — it never calls a delete/remove operation: [3](#0-2) 

`RepositoryImpl.updateDelegatedResource` simply caches the capsule with `Type.DIRTY`, which on commit is persisted via a `put`, not a `delete`, to the underlying `DelegatedResourceStore`: [4](#0-3) 

Meanwhile, `DelegateResourceProcessor` (the corresponding native "delegate" path) freely creates new `DelegatedResourceCapsule` entries for any valid, non-contract receiver address, requiring only 1 TRX minimum: [5](#0-4) [6](#0-5) 

Because `delegateResource`/`unDelegateResource` TVM native contracts are callable from any smart contract via a normal transaction, and the receiver only needs to be a valid, non-contract, existing account (no relationship to the caller, no witness/committee membership required), an attacker-controlled contract can cycle: delegate 1 TRX to address A → undelegate 1 TRX from address A → delegate 1 TRX to address B → undelegate … reusing the same frozen TRX balance for every iteration. Each such cycle through the TVM path leaves a permanent zero-value `DelegatedResourceCapsule` entry keyed by `(owner, receiver)` in `DelegatedResourceStore`, which is never cleaned up.

### Impact Explanation
`DelegatedResourceStore` is a `TronStoreWithRevoking` (persistent LevelDB/RocksDB-backed store) that every full node and light node must maintain and sync. Since this leak is only reachable via the TVM/native-contract call path (as opposed to the actuator path used for ordinary `DelegateResourceContract`/`UnDelegateResourceContract` transactions, which is correctly cleaned up), it represents an inconsistency where a specific invocation route causes irreversible on-chain storage bloat. Repeated exploitation by any account controlling (or deploying) a smart contract can grow the store indefinitely, increasing disk usage and node sync/startup overhead across the entire network over time — a persistent, unmitigable state-growth DoS vector analogous to the Aleo `delegated[]` issue, since there is no existing mechanism in the codebase to prune or garbage-collect these residual zero-balance entries.

### Likelihood Explanation
The attack requires only: (1) deploying (or using) a contract capable of invoking the `delegateResource`/`unDelegateResource` native TVM contracts, (2) freezing a minimal amount (≥1 TRX) for bandwidth or energy, and (3) repeatedly delegating/undelegating to a large set of distinct receiver addresses (which merely need to be existing, non-contract accounts — trivially satisfiable, e.g. any funded normal account, including throwaway accounts the attacker controls). The TRX used for delegation is reusable after each undelegate cycle, so the marginal cost per newly created permanent record is only the bandwidth/energy fee for the two contract calls — no fee is charged for the `DelegateResourceContract`/`UnDelegateResourceContract` operations themselves (`calcFee()` returns 0). This makes the attack cheap and repeatable indefinitely by any single account, requiring no elevated privileges.

### Recommendation
In `UnDelegateResourceProcessor.execute()`, mirror the actuator's behavior: when both `frozenBalanceForBandwidth` and `frozenBalanceForEnergy` reach zero, delete the `DelegatedResourceCapsule` from the store instead of persisting it via `updateDelegatedResource`. This requires adding a delete/remove capability to the `Repository`/`RepositoryImpl` caching layer for delegated resources (equivalent to `DelegatedResourceStore.delete`), and invoking it from `UnDelegateResourceProcessor` under the same zero-balance condition already checked at line 195.

### Proof of Concept
1. Deploy or use a smart contract `C` with sufficient frozen `BANDWIDTH`/`ENERGY` balance (≥1 TRX, `TRX_PRECISION`).
2. From `C`, invoke the TVM native "delegate resource" call to send 1 TRX worth of bandwidth to receiver address `R1` (any existing, non-contract account) — this creates a `DelegatedResourceCapsule` at key `(C, R1)` via `DelegateResourceProcessor.delegateResource` (`actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java:146-191`).
3. From `C`, invoke the TVM native "undelegate resource" call for the same 1 TRX from `R1` — `UnDelegateResourceProcessor.execute` reduces `frozenBalanceForBandwidth` to 0 (`actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java:165`), clears only the `DelegatedResourceAccountIndex` entries (lines 195-206), but still calls `repo.updateDelegatedResource(key, delegatedResourceCapsule)` (line 208) — persisting the now-empty capsule rather than deleting it.
4. Repeat steps 2–3 with a new receiver address `R2, R3, …, Rn` reusing the same 1 TRX frozen balance each time.
5. After `n` iterations, `DelegatedResourceStore` contains `n` permanent zero-balance entries that can never be removed, while the equivalent flow through `UnDelegateResourceActuator` (ordinary transaction, not via TVM) would have deleted each entry (`actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java:170-176`), demonstrating the inconsistency and the storage leak specific to the TVM-callable path.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L170-176)
```java
    if (unlockResource.getFrozenBalanceForBandwidth() == 0
        && unlockResource.getFrozenBalanceForEnergy() == 0) {
      delegatedResourceStore.delete(unlockKey);
      unlockResource = null;
    } else {
      delegatedResourceStore.put(unlockKey, unlockResource);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L158-161)
```java
      if (delegatedResourceCapsule.getFrozenBalanceForBandwidth() == 0
          && delegatedResourceCapsule.getFrozenBalanceForEnergy() == 0) {
        delegatedResourceStore.delete(key);

```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L152-159)
```java
          break;
        default:
          //this should never happen
          break;
      }
      repo.updateAccount(receiverCapsule.createDbKey(), receiverCapsule);
    }

```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L195-209)
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

    repo.updateDelegatedResource(key, delegatedResourceCapsule);
    repo.updateAccount(ownerCapsule.createDbKey(), ownerCapsule);
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L587-592)
```java
  @Override
  public void updateDelegatedResource(byte[] word,
      DelegatedResourceCapsule delegatedResourceCapsule) {
    delegatedResourceCache.put(Key.create(word),
        Value.create(delegatedResourceCapsule, Type.DIRTY));
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L52-55)
```java
    long delegateBalance = param.getDelegateBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
    }
```
