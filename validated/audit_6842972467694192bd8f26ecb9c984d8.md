Based on my investigation, this is a legitimate but well-known/limited-severity finding. Confirming the exploit path:

### Title
Unbounded per-account delegate index growth enables O(N) DoS on getdelegatedresourceaccountindex RPC/HTTP query - ([File: chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java])

### Summary
An unprivileged funded account can call `DelegateResourceContract` (or the TVM native `delegateresource` contract) repeatedly against many distinct freshly-created receiver addresses using the 1-TRX minimum delegation, causing unbounded growth of the `toAccounts`/`fromAccounts` lists tracked per address. Every subsequent read via `Wallet.getDelegatedResourceAccountIndex`/`getDelegatedResourceAccountIndexV2`, exposed unauthenticated through `GetDelegatedResourceAccountIndexServlet.doGet` and the gRPC `getDelegatedResourceAccountIndex` API, performs O(N) work with no pagination, producing a growing response payload and processing cost per query.

### Finding Description
`DelegateResourceActuator.delegateResource` (legacy path) and `DelegateResourceProcessor` (native-contract path via `DelegateResourceContract`) both add entries into `DelegatedResourceAccountIndexStore`/`DelegatedResourceAccountIndexCapsule` without any bound on the number of distinct (from,to) pairs per account [1](#0-0) . The only validation gate is a 1-TRX minimum delegate balance check in `DelegateResourceActuator.validate()` and `DelegateResourceProcessor.validate()` [2](#0-1) ; there is no cap on how many unique receivers an owner account can delegate to.

For the newer (post-optimization) code path, `DelegatedResourceAccountIndexStore.getWithPrefix` performs a `prefixQuery` scan across all `(V2_FROM_PREFIX|address)` and `(V2_TO_PREFIX|address)` keys, materializing and sorting the full list into a `DelegatedResourceAccountIndexCapsule` on every read [3](#0-2) . This is invoked from `Wallet.getDelegatedResourceAccountIndex`/`getDelegatedResourceAccountIndexV2` [4](#0-3) , which is reachable unauthenticated via `GetDelegatedResourceAccountIndexServlet.doGet` (HTTP GET) [5](#0-4)  and via the gRPC `getDelegatedResourceAccountIndex` handler in `RpcApiService` [6](#0-5) . No pagination or size limit exists in either handler.

None of the existing checks stop this: `DecodeUtil.addressValid` only validates address format, not uniqueness/count; `validateSignature` and permission checks only ensure the caller owns the sending key, not that the number of delegate targets is bounded; and `ForkController` fork gates only control feature activation (`supportDR`, `supportUnfreezeDelay`), not per-account list size.

### Impact Explanation
This matches TRON bounty's "DoS via RPC-API" class: an attacker can grow the size of a single account's `DelegatedResourceAccountIndex` (and correspondingly the underlying store's prefix-scanned keys) arbitrarily, so any client (including legitimate wallets/explorers) querying `/wallet/getdelegatedresourceaccountindex` or the gRPC equivalent for that address incurs O(N) DB scan, deserialization, sorting, and JSON/protobuf serialization cost per request — with N unbounded and attacker-controlled. This is a resource-exhaustion/performance-degradation vector on API-serving nodes, not a consensus-breaking or fund-theft bug.

### Likelihood Explanation
Cost to the attacker is only the sum of `1 TRX`-equivalent frozen V2 balance needed to satisfy the minimum per delegation (reusable/withdrawable via `UnDelegateResourceContract`, so the same 1 TRX principal can be redelegated to a fresh new receiver over and over, or a modest amount of TRX for N receivers) plus normal per-transaction bandwidth/fee costs and the cost of creating N receiver accounts (`CREATE_NEW_ACCOUNT_FEE_IN_SYSTEM_CONTRACT`, small). No SR/witness/committee privilege is needed — this is fully executable by any funded ordinary account. Repeatability is straightforward: broadcast N `DelegateResourceContract` transactions to N unique receivers, then issue the unauthenticated GET query. The main cost bottleneck is normal transaction fees/bandwidth for N broadcasts, which scale linearly and are a real but non-prohibitive expense (TRON's fee is far cheaper than equivalent DoS costs on similarly-shaped chains).

### Recommendation
Add a maximum cap on the number of distinct receiver/owner entries tracked per account in `DelegatedResourceAccountIndexCapsule`/`DelegatedResourceAccountIndexStore` (reject new delegations once the cap is reached, or expire/evict old entries), and add pagination (offset/limit) parameters to `Wallet.getDelegatedResourceAccountIndex(V2)` and the corresponding HTTP/gRPC handlers so a single query cannot force unbounded server-side work in one call.

### Proof of Concept
```java
// JUnit-style PoC sketch, framework/src/test/java/org/tron/core/actuator/DelegateResourceActuatorTest.java pattern
@Test
public void testUnboundedDelegateIndexGrowth() {
  freezeBandwidthForOwner(); // gives OWNER_ADDRESS large FrozenV2BalanceForBandwidth
  int N = 50_000; // attacker-controlled, no cap enforced
  for (int i = 0; i < N; i++) {
    byte[] receiver = generateFreshAddress(i); // create+fund a fresh account
    dbManager.getAccountStore().put(receiver, new AccountCapsule(...));
    DelegateResourceActuator actuator = new DelegateResourceActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getDelegateContractForBandwidth(OWNER_ADDRESS,
            ByteArray.toHexString(receiver), TRX_PRECISION)); // 1 TRX minimum
    TransactionResultCapsule ret = new TransactionResultCapsule();
    actuator.validate();
    actuator.execute(ret);
  }
  long start = System.nanoTime();
  Protocol.DelegatedResourceAccountIndex index =
      wallet.getDelegatedResourceAccountIndex(
          ByteString.copyFrom(ByteArray.fromHexString(OWNER_ADDRESS)));
  long elapsed = System.nanoTime() - start;
  // assert index.getToAccountsCount() == N (unbounded growth)
  // assert elapsed grows ~linearly with N when repeated for increasing N,
  // demonstrating unbounded per-query cost with no pagination or cap.
}
```
Equivalent HTTP reproduction: broadcast N `DelegateResourceContract` transactions from address A to N unique receivers, then `curl http://node:8090/wallet/getdelegatedresourceaccountindex?value=<A_hex>` and observe response size/latency scaling with N.

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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L52-55)
```java
    long delegateBalance = param.getDelegateBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
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

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1040-1064)
```java
  public DelegatedResourceAccountIndex getDelegatedResourceAccountIndex(ByteString address) {
    if (address == null || address.size() != DecodeUtil.ADDRESS_SIZE / 2) {
      return DelegatedResourceAccountIndex.getDefaultInstance();
    }
    DelegatedResourceAccountIndexCapsule accountIndexCapsule =
        chainBaseManager.getDelegatedResourceAccountIndexStore().getIndex(address.toByteArray());
    if (accountIndexCapsule != null) {
      return accountIndexCapsule.getInstance();
    } else {
      return DelegatedResourceAccountIndex.getDefaultInstance();
    }
  }

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

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexServlet.java (L24-35)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      String address = request.getParameter("value");
      if (visible) {
        address = Util.getHexAddress(address);
      }
      fillResponse(ByteString.copyFrom(ByteArray.fromHexString(address)), visible, response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L1917-1927)
```java
    @Override
    public void getDelegatedResourceAccountIndex(BytesMessage request,
        StreamObserver<org.tron.protos.Protocol.DelegatedResourceAccountIndex> responseObserver) {
      try {
        responseObserver
          .onNext(wallet.getDelegatedResourceAccountIndex(request.getValue()));
      } catch (Exception e) {
        responseObserver.onError(getRunTimeException(e));
      }
      responseObserver.onCompleted();
    }
```
