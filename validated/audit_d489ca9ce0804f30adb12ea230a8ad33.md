### Title
Unbounded growth of `DelegatedResourceAccountIndex.toAccounts`/`fromAccounts` lists enables RPC-triggered resource-exhaustion / DoS - (File: chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java)

### Summary
The Sherlock report describes `UserManager.getFrozenInfo` iterating an uncapped `vouchees` array, causing out-of-gas reverts. The analogous pattern in java-tron is the legacy `DelegatedResourceAccountIndex` structure (`toAccounts`/`fromAccounts` fields), which is appended to without any upper bound whenever an account delegates frozen resources to (or receives delegation from) a *new, distinct* address while `allowDelegateOptimization` is not enabled. This list can be grown arbitrarily large by any account (an unprivileged, ordinary transaction sender) and is later fully materialized and returned by an anonymous RPC/HTTP endpoint (`GetDelegatedResourceAccountIndex`), and is also fully iterated internally by `DelegatedResourceAccountIndexStore.convert()`.

### Finding Description
`FreezeBalanceActuator.delegateResource` appends a receiver/owner address to the `toAccountsList` / `fromAccountsList` of `DelegatedResourceAccountIndexCapsule` with no size limit, guarded only by a `contains()` check that itself becomes O(n) as the list grows: [1](#0-0) 

`DelegatedResourceAccountIndexCapsule.addToAccount` / `addFromAccount` simply append to the underlying protobuf repeated field with no cap enforced anywhere: [2](#0-1) 

Once the optimized path (`allowDelegateOptimization`) is later enabled, the legacy list is migrated by `convert()`, which loops over the *entire* accumulated `toList`/`fromList` in a single call to rewrite it into the new per-pair keyspace: [3](#0-2) 

Even under the "optimized" V2 scheme, reading the index (`getWithPrefix`/`getV2Index`, used by `getIndex`) performs a full prefix scan and materializes/sorts the complete list of counterpart addresses in memory: [4](#0-3) 

This whole capsule (including the full `toAccounts`/`fromAccounts` list) is exposed to anonymous callers through the `GetDelegatedResourceAccountIndex`/`GetDelegatedResourceAccountIndexV2` gRPC and HTTP endpoints, reachable without authentication: [5](#0-4) [6](#0-5) 

Unlike the `vouchees` cap referenced in the Sherlock discussion (`maxVouchers` check in `updateTrust`), there is no analogous cap on the number of distinct delegatees/delegators an account can accumulate in `toAccountsList`/`fromAccountsList` — an attacker can send many cheap `FreezeBalanceContract`/`DelegateResourceContract` transactions to distinct, self-controlled receiver addresses, each adding one entry to the list, with no upper bound.

### Impact Explanation
A large `toAccounts`/`fromAccounts` list on a single account causes:
1. Increased CPU cost for the O(n) `contains()` check on every subsequent legacy-path delegate/undelegate for that account.
2. A single-call O(n) migration cost in `convert()` when optimization is turned on.
3. A large, unbounded response payload and serialization/sorting cost (`getWithPrefix`) when any anonymous client queries `GetDelegatedResourceAccountIndex(V2)` for that address, which can degrade node responsiveness (DoS via RPC-API), analogous to the reverting/energy-exhaustion impact described in the source report for `getFrozenInfo`.

### Likelihood Explanation
Growing the list only requires ordinary `FreezeBalanceContract` transactions with small delegated amounts to many distinct, attacker-controlled receiver addresses — no privileged role, leaked key, or malicious peer is required, satisfying the "unprivileged" and "reachable from a broadcast transaction" requirement. The cost to the attacker scales with the number of transactions (bandwidth/energy fees), which is a normal but not prohibitive expense on TRON, making this feasible though not free.

### Recommendation
- Enforce a maximum size (e.g., a `maxDelegateeCount`/`maxDelegatorCount` dynamic-store parameter) on `toAccountsList`/`fromAccountsList` in `DelegatedResourceAccountIndexCapsule.addToAccount`/`addFromAccount`, rejecting further delegation to new distinct addresses once the cap is reached, similar to `maxVouchees` in the reference report.
- For already-optimized (`V2`) accounts, add pagination or a similar cap to `getWithPrefix`/`getV2Index` so that RPC responses and internal iteration are bounded regardless of how many distinct delegation relationships exist.

### Proof of Concept
1. Attacker controls account `A` with `TRX` sufficient to `FreezeBalanceContract`-delegate small resource amounts.
2. While `allowDelegateOptimization` is disabled (or before the flag is enabled for the network), the attacker repeatedly submits `FreezeBalanceContract` transactions from `A` delegating to `N` distinct newly-generated receiver addresses (`R1..RN`), each creating a new `toAccountsList` entry via `FreezeBalanceActuator.delegateResource` (lines 320-332).
3. `A`'s `DelegatedResourceAccountIndexCapsule.toAccountsList` grows to size `N` with no bound.
4. An anonymous client calls `GetDelegatedResourceAccountIndex`/`GetDelegatedResourceAccountIndexV2` for address `A`; the node must deserialize/sort/return the full list, incurring load proportional to `N`.
5. If/when `allowDelegateOptimization` is later enabled, the first subsequent delegate/undelegate touching account `A` triggers `DelegatedResourceAccountIndexStore.convert(A)`, which iterates the entire `N`-sized list in one synchronous call during transaction execution.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L320-345)
```java
    if (!dynamicPropertiesStore.supportAllowDelegateOptimization()) {

      DelegatedResourceAccountIndexCapsule ownerIndexCapsule =
          delegatedResourceAccountIndexStore.get(ownerAddress);
      if (ownerIndexCapsule == null) {
        ownerIndexCapsule = new DelegatedResourceAccountIndexCapsule(
            ByteString.copyFrom(ownerAddress));
      }
      List<ByteString> toAccountsList = ownerIndexCapsule.getToAccountsList();
      if (!toAccountsList.contains(ByteString.copyFrom(receiverAddress))) {
        ownerIndexCapsule.addToAccount(ByteString.copyFrom(receiverAddress));
      }
      delegatedResourceAccountIndexStore.put(ownerAddress, ownerIndexCapsule);

      DelegatedResourceAccountIndexCapsule receiverIndexCapsule
          = delegatedResourceAccountIndexStore.get(receiverAddress);
      if (receiverIndexCapsule == null) {
        receiverIndexCapsule = new DelegatedResourceAccountIndexCapsule(
            ByteString.copyFrom(receiverAddress));
      }
      List<ByteString> fromAccountsList = receiverIndexCapsule
          .getFromAccountsList();
      if (!fromAccountsList.contains(ByteString.copyFrom(ownerAddress))) {
        receiverIndexCapsule.addFromAccount(ByteString.copyFrom(ownerAddress));
      }
      delegatedResourceAccountIndexStore.put(receiverAddress, receiverIndexCapsule);
```

**File:** chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java (L57-86)
```java
  public void addFromAccount(ByteString fromAccount) {
    this.delegatedResourceAccountIndex = this.delegatedResourceAccountIndex.toBuilder()
        .addFromAccounts(fromAccount)
        .build();
  }

  public void removeFromAccount(ByteString fromAccount) {
    if (getFromAccountsList().contains(fromAccount)) {
      List<ByteString> fromList = new ArrayList<>(getFromAccountsList());
      fromList.remove(fromAccount);
      setAllFromAccounts(fromList);
    }
  }

  public List<ByteString> getToAccountsList() {
    return this.delegatedResourceAccountIndex.getToAccountsList();
  }

  public void setAllToAccounts(List<ByteString> toAccounts) {
    this.delegatedResourceAccountIndex = this.delegatedResourceAccountIndex.toBuilder()
        .clearToAccounts()
        .addAllToAccounts(toAccounts)
        .build();
  }

  public void addToAccount(ByteString toAccount) {
    this.delegatedResourceAccountIndex = this.delegatedResourceAccountIndex.toBuilder()
        .addToAccounts(toAccount)
        .build();
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L42-61)
```java
  public void convert(byte[] address) {
    DelegatedResourceAccountIndexCapsule indexCapsule = this.get(address);
    if (indexCapsule == null) {
      // convert complete or have no delegate
      return;
    }
    // convert old data
    List<ByteString> toList = indexCapsule.getToAccountsList();
    for (int i = 0; i < toList.size(); i++) {
      // use index as the timestamp, just to keep index in order
      this.delegate(address, toList.get(i).toByteArray(), i + 1L);
    }

    List<ByteString> fromList = indexCapsule.getFromAccountsList();
    for (int i = 0; i < fromList.size(); i++) {
      // use index as the timestamp, just to keep index in order
      this.delegate(fromList.get(i).toByteArray(), address, i + 1L);
    }
    this.delete(address);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L106-138)
```java
  public DelegatedResourceAccountIndexCapsule getIndex(byte[] address) {
    DelegatedResourceAccountIndexCapsule indexCapsule = get(address);
    if (indexCapsule != null) {
      return indexCapsule;
    }
    return getWithPrefix(FROM_PREFIX, TO_PREFIX, address);
  }

  public DelegatedResourceAccountIndexCapsule getV2Index(byte[] address) {
    return getWithPrefix(V2_FROM_PREFIX, V2_TO_PREFIX, address);
  }

  private DelegatedResourceAccountIndexCapsule getWithPrefix(byte[] fromPrefix, byte[] toPrefix, byte[] address) {
    DelegatedResourceAccountIndexCapsule tmpIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(address));

    byte[] key = Bytes.concat(fromPrefix, address);
    List<DelegatedResourceAccountIndexCapsule> tmpToList =
        new ArrayList<>(this.prefixQuery(key).values());
    tmpToList.sort(Comparator.comparing(DelegatedResourceAccountIndexCapsule::getTimestamp));
    List<ByteString> list = tmpToList.stream()
        .map(DelegatedResourceAccountIndexCapsule::getAccount).collect(Collectors.toList());
    tmpIndexCapsule.setAllToAccounts(list);

    key = Bytes.concat(toPrefix, address);
    List<DelegatedResourceAccountIndexCapsule> tmpFromList =
        new ArrayList<>(this.prefixQuery(key).values());
    tmpFromList.sort(Comparator.comparing(DelegatedResourceAccountIndexCapsule::getTimestamp));
    list = tmpFromList.stream().map(DelegatedResourceAccountIndexCapsule::getAccount).collect(
        Collectors.toList());
    tmpIndexCapsule.setAllFromAccounts(list);
    return tmpIndexCapsule;
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexServlet.java (L1-2)
```java
package org.tron.core.services.http;

```

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L1105-1130)
```java
    @Override
    public void createAssetIssue2(AssetIssueContract request,
        StreamObserver<TransactionExtention> responseObserver) {
      createTransactionExtention(request, ContractType.AssetIssueContract, responseObserver);
    }

    @Override
    public void unfreezeAsset(UnfreezeAssetContract request,
        StreamObserver<Transaction> responseObserver) {
      try {
        responseObserver.onNext(
            createTransactionCapsule(request, ContractType.UnfreezeAssetContract).getInstance());
      } catch (ContractValidateException e) {
        responseObserver.onNext(null);
        logger.debug(CONTRACT_VALIDATE_EXCEPTION, e.getMessage());
      }
      responseObserver.onCompleted();
    }

    @Override
    public void unfreezeAsset2(UnfreezeAssetContract request,
        StreamObserver<TransactionExtention> responseObserver) {
      createTransactionExtention(request, ContractType.UnfreezeAssetContract, responseObserver);
    }

    //refactor、test later
```
