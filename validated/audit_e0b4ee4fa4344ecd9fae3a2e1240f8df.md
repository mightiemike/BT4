### Title
Unbounded read amplification in GetDelegatedResourceAccountIndexV2Servlet via unbounded cheap DelegateResourceContract fan-out - (File: framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java)

### Summary
An attacker can create an arbitrarily large number of `V2_FROM_PREFIX`/`V2_TO_PREFIX` index entries for one address by issuing many cheap `DelegateResourceContract` transactions to distinct freshly-generated receivers, each requiring only the 1 TRX minimum. Repeated calls to `GetDelegatedResourceAccountIndexV2Servlet.doGet` on that address then force `DelegatedResourceAccountIndexStore.getV2Index` → `getWithPrefix` to run an O(N) `prefixQuery` plus O(N log N) sort and JSON serialization per request, with no cost scaling relative to N.

### Finding Description
`DelegateResourceProcessor.delegateResource` writes one `V2_FROM_PREFIX` key for the owner and one `V2_TO_PREFIX` key for the receiver on every delegation [1](#0-0) , gated only by a minimum `delegateBalance >= 1 TRX` and available frozen V2 balance for the resource type [2](#0-1) . There is no cap on the number of distinct receivers an owner can delegate to, so an attacker can grow the from/to index for a single address to size N by repeating this with N distinct freshly-generated receiver accounts, each requiring only funding for the minimum freeze/delegate amount plus standard transaction fees.

`GetDelegatedResourceAccountIndexV2Servlet.doGet`/`doPost` take a public address parameter and call `Wallet.getDelegatedResourceAccountIndexV2`, which ultimately calls `DelegatedResourceAccountIndexStore.getV2Index` → `getWithPrefix` [3](#0-2) . This method performs two `prefixQuery` calls returning all N matching entries, converts each to an `ArrayList`, sorts by timestamp (`O(N log N)`), and streams/maps every element, all before the servlet layer serializes the full list to JSON in `fillResponse` [4](#0-3) . The endpoint only extends `RateLimiterServlet`, which enforces a flat per-request rate limit independent of the size of the underlying index (no query cost/size-based throttling was found for this endpoint).

Thus, the per-request cost of this public GET endpoint scales linearly (I/O) plus log-linearly (sort) with N, while the attacker's cost to grow N scales linearly with cheap (1 TRX minimum, no cap) transactions — a materially underpriced amount of public state-iteration work relative to the flat rate-limiter cost model.

### Impact Explanation
Once N is large (e.g., tens of thousands of receivers), each call to `/walletsolidity/getdelegatedresourceaccountindexv2` (or its full-node/JSON-RPC equivalents) performs a full prefix scan, sort, and JSON serialization over N entries. Repeated invocation of this cheap-to-trigger, expensive-to-serve endpoint degrades node CPU (sorting + serialization) and disk I/O (prefix scan) for any full node or solidity node serving the HTTP API, potentially causing service degradation/DoS for that endpoint and consuming node resources disproportionately to the flat per-request rate limit.

### Likelihood Explanation
The attacker only needs a funded account that can freeze/delegate the 1 TRX minimum repeatedly to distinct receiver addresses — no special privilege, admin access, or protocol vulnerability is required. Address generation is free and there is no cap on the number of distinct delegation relationships per address, so N can be grown arbitrarily over time at a cost proportional to the number of cheap transactions (bounded only by TRX cost of transaction fees/frozen balance requirements, which is intentionally minimized). This is a realistic and repeatable attack vector for a determined attacker with modest capital, and the effect compounds because the inflated index persists on-chain (until `unDelegateV2` calls are made, which cost equally cheap transactions and don't shrink the historical state efficiently).

### Recommendation
- Add a cap/pagination to `DelegatedResourceAccountIndexStore.getWithPrefix`/`getV2Index` (e.g., limit the number of returned from/to entries per query, or require pagination parameters on the HTTP endpoint) so a single request cannot force an unbounded prefix scan/sort.
- Consider rate-limiting or costing this endpoint proportionally to the size of the underlying index (e.g., cache the result and invalidate only on change, or limit query result size).
- Consider limiting the maximum number of distinct delegation relationships (receivers) allowed per owner address at the actuator/processor level to bound V2 index growth from cheap transactions.

### Proof of Concept
Java integration test plan (extending `DelegatedResourceAccountIndexStoreTest`/`DelegateResourceActuatorTest` style):
1. In a test using `DelegatedResourceAccountIndexStore`, loop `for i in 0..N` calling `store.delegateV2(ownerAddress, generateDistinctReceiver(i), timestamp)` for large N (e.g., 100,000), simulating N cheap `DelegateResourceContract` calls with 1 TRX each.
2. Measure wall-clock time and CPU of `store.getV2Index(ownerAddress)` (which calls `getWithPrefix`) for increasing N (e.g., N = 1,000 / 10,000 / 100,000) and assert the time grows super-linearly / is not bounded by a constant, contradicting the flat rate-limiter cost assumption enforced by `RateLimiterServlet`.
3. Extend to an HTTP-level integration test invoking `GetDelegatedResourceAccountIndexV2Servlet.doGet` repeatedly against the inflated address and assert that response latency scales with N while the servlet's rate limiter allows the same request rate regardless of N, demonstrating disproportionate CPU/disk cost per allowed request.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L52-55)
```java
    long delegateBalance = param.getDelegateBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L166-180)
```java
    //modify DelegatedResourceAccountIndex
    long now = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    byte[] fromKey = Bytes.concat(
        DelegatedResourceAccountIndexStore.getV2_FROM_PREFIX(), ownerAddress, receiverAddress);
    DelegatedResourceAccountIndexCapsule toIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(receiverAddress));
    toIndexCapsule.setTimestamp(now);
    repo.updateDelegatedResourceAccountIndex(fromKey, toIndexCapsule);

    byte[] toKey = Bytes.concat(
        DelegatedResourceAccountIndexStore.getV2_TO_PREFIX(), receiverAddress, ownerAddress);
    DelegatedResourceAccountIndexCapsule fromIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(ownerAddress));
    fromIndexCapsule.setTimestamp(now);
    repo.updateDelegatedResourceAccountIndex(toKey, fromIndexCapsule);
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L114-138)
```java
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
