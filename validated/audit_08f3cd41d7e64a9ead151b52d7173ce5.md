### Title
Unbounded, cost-free growth of `DelegatedResourceAccountIndexStore` entries enables anonymous DoS of resource-delegation reads and account state - (File: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java`)

### Summary
The reported Alchemix bug is caused by `VotingEscrow` having a fixed `MAX_DELEGATES` cap on a per-account delegate list with no minimum stake requirement, letting an attacker cheaply fill a victim's delegate slots and permanently block legitimate `createLock`/`delegate` calls for that victim. The java-tron analog is the inverse but equally exploitable defect: `DelegateResourceContract` allows **any** account to create an unlimited number of tiny (1 TRX, fully refundable) delegation records against an arbitrary victim address, and `DelegatedResourceAccountIndexStore` has **no upper bound** on the number of index entries it stores or returns for a given address.

### Finding Description
`DelegateResourceActuator.validate()` only enforces a floor of `delegateBalance >= TRX_PRECISION` (1 TRX) and that the delegated amount does not exceed the owner's available frozen balance [1](#0-0) . There is no limit on how many distinct delegation relationships (`from`/`to` pairs) can be created against a single receiver address.

When the delegate-optimization hard fork is active, each delegation is recorded as its own key/value row via `DelegatedResourceAccountIndexStore.delegate()`, keyed by `FROM_PREFIX/from/to` and `TO_PREFIX/to/from` with no size check whatsoever: [2](#0-1) . The legacy (pre-optimization) path is equally unbounded: it appends directly to an in-memory `List<ByteString>` stored as a single serialized capsule with no cap check before calling `addToAccount`/`addFromAccount` [3](#0-2) .

Because the balance requirement is only 1 TRX and can be fully recovered later via `UnDelegateResourceActuator`/`unfreezeV2`, an attacker can cheaply script thousands (or millions) of `DelegateResourceContract` broadcasts against a single victim address using disposable sender accounts, driving unbounded growth of that victim's index entries at near-zero net cost.

This unbounded list is then read in full whenever:
1. Any account state transition touches the address and the legacy `convert()` routine runs, which iterates the entire old list synchronously inside actuator execution [4](#0-3) .
2. Any RPC/HTTP client queries `getDelegatedResourceAccountIndex(V2)`, which performs an unbounded `prefixQuery`, sorts the full result set, and materializes the whole list into a protobuf response returned over gRPC/HTTP [5](#0-4)  exposed via `Wallet.getDelegatedResourceAccountIndexV2` [6](#0-5)  and the corresponding gRPC/HTTP endpoints [7](#0-6) .

### Impact Explanation
An unprivileged, anonymous actor who can broadcast transactions can inflate the delegation index of any target address without bound and at negligible net cost (the 1 TRX minimum is refundable). This causes:
- Increasing storage/compute cost on the victim's account for every subsequent freeze/delegate/unfreeze operation that touches the legacy conversion path.
- A read-side amplification/DoS on `GetDelegatedResourceAccountIndex`/`V2` RPC and HTTP endpoints, since the full unbounded list must be fetched, sorted, and serialized on every query — this is reachable anonymously from any RPC/HTTP client, matching the "DoS via RPC-API or protocol implementation" acceptance criterion.
- This is a griefing/DoS impact analogous to the reported bug: absence of any cap or minimum economic disincentive on a per-victim delegate/index list lets an attacker degrade or disrupt legitimate operations for a targeted account, without requiring privileged access.

### Likelihood Explanation
Likelihood is moderate-to-high: `DelegateResourceContract` is a standard, permissionless, low-fee transaction type; the only cost is temporarily locking 1 TRX per delegation (recoverable), so an attacker can cheaply and repeatedly target any address. The `getDelegatedResourceAccountIndex(V2)` RPC/HTTP calls are unauthenticated read endpoints on full/solidity/PBFT nodes, so exploitation of the read-amplification aspect is trivially reachable by anyone.

### Recommendation
- Impose a maximum number of delegation index entries (analogous to a `MAX_DELEGATES`-style cap) per address in `DelegatedResourceAccountIndexStore`/`DelegateResourceActuator.validate()`, and/or require a proportionally higher minimum `delegateBalance` to make spam economically costly.
- Add pagination/limits to `getDelegatedResourceAccountIndex`/`V2` so a single unauthenticated RPC/HTTP call cannot force a node to materialize and serialize an arbitrarily large result set.
- Consider charging a non-refundable fee (`calcFee()`) proportional to index-list growth for `DelegateResourceContract`, rather than fee = 0, to disincentivize spam delegation entries. (Note: this report was unable to confirm the exact `calcFee()` implementation for `DelegateResourceActuator` from the available index; this should be verified directly in the source before finalizing remediation.)

### Proof of Concept
Conceptual PoC (not executed, derived from code paths above):
1. Fund N disposable accounts with 1 TRX each; freeze 1 TRX for bandwidth in each via `FreezeBalanceV2Contract`.
2. From each of the N accounts, broadcast a `DelegateResourceContract` with `balance = 1 TRX` and `receiver_address = <victim>`.
3. Each transaction succeeds because `DelegateResourceActuator.validate()` only checks `delegateBalance >= TRX_PRECISION` and sufficient owner frozen balance [1](#0-0) ; no check exists against the receiver's accumulated index size.
4. Repeat for arbitrarily large N (limited only by attacker's patience/available disposable keys), then optionally `UnDelegateResourceContract` + unfreeze to recover the locked TRX, keeping net cost near zero.
5. Query `GetDelegatedResourceAccountIndexV2(victim)` via gRPC/HTTP — the node must execute an unbounded `prefixQuery` and sort/serialize all N entries [5](#0-4) , demonstrating the read-amplification DoS surface.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L147-150)
```java
    long delegateBalance = delegateResourceContract.getBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
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

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L63-75)
```java
  public void delegate(byte[] from, byte[] to, long time) {
    byte[] fromKey = Bytes.concat(FROM_PREFIX, from, to);
    DelegatedResourceAccountIndexCapsule toIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(to));
    toIndexCapsule.setTimestamp(time);
    this.put(fromKey, toIndexCapsule);

    byte[] toKey = Bytes.concat(TO_PREFIX, to, from);
    DelegatedResourceAccountIndexCapsule fromIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(from));
    fromIndexCapsule.setTimestamp(time);
    this.put(toKey, fromIndexCapsule);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L118-138)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L319-345)
```java
    //modify DelegatedResourceAccountIndexStore
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

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1053-1064)
```java
  public DelegatedResourceAccountIndex getDelegatedResourceAccountIndexV2(ByteString address) {
    if (address == null || address.size() != DecodeUtil.ADDRESS_SIZE / 2) {
      return DelegatedResourceAccountIndex.getDefaultInstance();
    }
    DelegatedResourceAccountIndexCapsule accountIndexCapsule = chainBaseManager
        .getDelegatedResourceAccountIndexStore().getV2Index(address.toByteArray());
    if (accountIndexCapsule != null) {
      return accountIndexCapsule.getInstance();
    } else {
      return DelegatedResourceAccountIndex.getDefaultInstance();
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L1929-1939)
```java
    @Override
    public void getDelegatedResourceAccountIndexV2(BytesMessage request,
        StreamObserver<org.tron.protos.Protocol.DelegatedResourceAccountIndex> responseObserver) {
      try {
        responseObserver
                .onNext(wallet.getDelegatedResourceAccountIndexV2(request.getValue()));
      } catch (Exception e) {
        responseObserver.onError(getRunTimeException(e));
      }
      responseObserver.onCompleted();
    }
```
