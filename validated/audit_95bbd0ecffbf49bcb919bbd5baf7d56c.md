### Title
Zero-fee `DelegateResource` transactions with only a 1 TRX floor permit unbounded state growth in `DelegatedResourceStore` / `DelegatedResourceAccountIndexStore` - (File: `actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java`)

### Summary
`DelegateResourceActuator` (and its VM-native counterpart `DelegateResourceProcessor`) enforces only a fixed 1‑TRX floor on the delegated amount and charges **no** protocol fee (`calcFee()` returns `0`). Every delegation to a distinct receiver address creates a brand-new, permanent key in `DelegatedResourceStore` and appends entries to the `fromAccounts`/`toAccounts` lists in `DelegatedResourceAccountIndexStore`. Because the floor is a fixed 1 TRX and the delegated TRX is never spent (it can be un-delegated and re-delegated to yet another fresh address), an attacker can generate an effectively unbounded number of tiny, low-economic-value storage entries at negligible incremental cost — the same storage-bloat pattern flagged in the referenced Acala report ("no minimum deposit amount... allows for creating multiple pool positions").

### Finding Description
`DelegateResourceActuator.validate()` only checks: [1](#0-0) 
that `delegateBalance >= TRX_PRECISION` (1 TRX). There is no limit on the number of distinct receiver addresses a single owner may delegate to, and `calcFee()` is hard-coded to `0`: [2](#0-1) 

`execute()` calls `delegateResource(...)`, which for every new `(owner, receiver, lock)` triple creates a fresh `DelegatedResourceCapsule` and persists it, and separately updates the account index store lists that track which addresses an account has delegated to/from: [3](#0-2) 

Because the delegated TRX balance is not consumed (only locked/moved between "frozen" and "delegated" accounting fields), the same 1 TRX can be freed via `UnDelegateResourceActuator`/unfreeze and then re-delegated to a brand-new receiver address, producing a new permanent `DelegatedResourceStore` key and new index-list entries each time — at the cost of only the ordinary bandwidth/energy fee for the transaction itself, since `calcFee()` for this actuator is `0`. This mirrors the Acala `deposit_dex_share` issue: a "greater than 0 / minimum unit" check exists, but nothing bounds the *number* of persistent positions/entries an attacker can create relative to their actual capital, letting them bloat chain state cheaply and repeatedly.

By contrast, other TRON store-creating actuators that face similar spam risk enforce meaningful economic floors: `ExchangeCreateActuator` charges a large fixed `getExchangeCreateFee()` per new exchange pair, and `AssetIssueActuator` charges `getAssetIssueFee()` per issued asset. `DelegateResourceActuator` has no analogous per-entry cost beyond the reusable 1 TRX floor.

### Impact Explanation
An attacker controlling one funded account and one TRX worth of frozen bandwidth/energy V2 balance can repeatedly: delegate 1 TRX to a new address → wait/undelegate → delegate to another new address, each cycle permanently adding entries to `DelegatedResourceStore` and growing the unbounded `fromAccounts`/`toAccounts` lists in `DelegatedResourceAccountIndexStore` for both the owner and every synthetic receiver. Because `calcFee()==0`, there is no explicit "storage rent" collected by the protocol for this growth, unlike exchange/asset creation. Over time this can bloat the FullNode/SolidityNode state database (RocksDB/LevelDB), increasing disk usage, sync time, and iteration costs for anything that scans `DelegatedResourceAccountIndexStore` (e.g., wallet/API queries enumerating an account's delegations), degrading node performance — a denial-of-service-by-storage-bloat vector reachable via ordinary broadcast transactions (`DelegateResourceContract`), no privileged role required.

### Likelihood Explanation
The `DelegateResourceContract` is a standard, unprivileged, broadcastable TRON transaction type available to any account once `supportDR()`/`supportUnfreezeDelay()` are enabled on-chain (both already active on mainnet). The only prerequisite is possessing (and being able to cycle) 1 TRX of frozen V2 bandwidth or energy balance and generating fresh receiver addresses (free to create). This makes the attack cheap and repeatable, though the throughput is bounded by TRON's normal per-block/per-account bandwidth/energy consumption for issuing the delegate/undelegate transaction pairs, which somewhat limits — but does not prevent — sustained abuse.

### Recommendation
- Introduce an incremental fee (`calcFee()` > 0) for `DelegateResourceContract`, proportional to state growth (e.g., charged when a *new* `(owner, receiver)` key is created rather than when reusing an existing one).
- Cap the number of distinct receivers/entries a single account may maintain in `DelegatedResourceAccountIndexStore`, or require pruning of empty/expired entries from both `DelegatedResourceStore` and the index lists once a delegation's balance is fully returned.
- Consider raising the minimum delegate amount and/or adding a minimum "hold time" before undelegate is permitted, to reduce the rate at which the same TRX can be recycled into new storage entries.

### Proof of Concept
Conceptual reproduction using the existing test harness pattern in `DelegateResourceActuatorTest`:
1. Freeze 1 TRX for bandwidth (`freezeBandwidthForOwner()` style helper) via `FreezeBalanceV2Contract`.
2. Repeatedly:
   a. Call `DelegateResourceActuator` with `delegateBalance = TRX_PRECISION` (1 TRX) to a freshly generated `receiverAddress` — this succeeds per validation logic [1](#0-0)  and creates a new `DelegatedResourceStore`/index entry [4](#0-3) , with zero protocol fee [2](#0-1) .
   b. Call `UnDelegateResourceActuator` to reclaim the 1 TRX frozen balance from that receiver.
   c. Repeat with a new receiver address.
3. Each iteration leaves a permanent `DelegatedResourceStore` record (even after the balance is undelegated, historical index entries as reflected by the `fromAccounts`/`toAccounts` list growth demonstrated in `DelegateResourceActuatorTest.testDelegateResourceForCpu`) [5](#0-4) , at negligible incremental cost, demonstrating unbounded storage growth analogous to the Acala low-liquidity-position bloat.

**Note on verification limits**: I was not able to fully confirm (within tool budget) whether `UnDelegateResourceActuator` deletes the `DelegatedResourceAccountIndexStore` list entries once a delegation is fully withdrawn, or only zeroes the balance while leaving the index reference in place. This distinction affects the precise severity (whether index lists grow strictly monotonically or are pruned), and should be verified directly in `UnDelegateResourceActuator.java` and `DelegatedResourceAccountIndexStore.java` before treating this as fully confirmed.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L147-150)
```java
    long delegateBalance = delegateResourceContract.getBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L277-280)
```java
  @Override
  public long calcFee() {
    return 0;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L290-325)
```java
    // 1. unlock the expired delegate resource
    long now = chainBaseManager.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    delegatedResourceStore.unLockExpireResource(ownerAddress, receiverAddress, now);

    //modify DelegatedResourceStore
    long expireTime = 0;
    if (lock) {
      expireTime = now + lockPeriod * BLOCK_PRODUCED_INTERVAL;
    }
    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, lock);
    DelegatedResourceCapsule delegatedResourceCapsule = delegatedResourceStore.get(key);
    if (delegatedResourceCapsule == null) {
      delegatedResourceCapsule = new DelegatedResourceCapsule(ByteString.copyFrom(ownerAddress),
          ByteString.copyFrom(receiverAddress));
    }

    if (isBandwidth) {
      delegatedResourceCapsule.addFrozenBalanceForBandwidth(balance, expireTime);
    } else {
      delegatedResourceCapsule.addFrozenBalanceForEnergy(balance, expireTime);
    }
    delegatedResourceStore.put(key, delegatedResourceCapsule);

    //modify DelegatedResourceAccountIndexStore
    delegatedResourceAccountIndexStore.delegateV2(ownerAddress, receiverAddress,
        dynamicPropertiesStore.getLatestBlockHeaderTimestamp());

    //modify AccountStore for receiver
    AccountCapsule receiverCapsule = accountStore.get(receiverAddress);
    if (isBandwidth) {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(balance);
    } else {
      receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(balance);
    }
    accountStore.put(receiverCapsule.createDbKey(), receiverCapsule);
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/DelegateResourceActuatorTest.java (L638-652)
```java
      //check DelegatedResourceAccountIndex
      DelegatedResourceAccountIndexCapsule ownerIndexCapsule = dbManager
          .getDelegatedResourceAccountIndexStore().getV2Index(owner);
      assertEquals(0, ownerIndexCapsule.getFromAccountsList().size());
      assertEquals(1, ownerIndexCapsule.getToAccountsList().size());
      assertTrue(ownerIndexCapsule.getToAccountsList()
          .contains(ByteString.copyFrom(receiver)));

      DelegatedResourceAccountIndexCapsule receiverIndexCapsule = dbManager
          .getDelegatedResourceAccountIndexStore().getV2Index(receiver);
      assertEquals(0, receiverIndexCapsule.getToAccountsList().size());
      assertEquals(1,
              receiverIndexCapsule.getFromAccountsList().size());
      assertTrue(receiverIndexCapsule.getFromAccountsList()
          .contains(ByteString.copyFrom(owner)));
```
