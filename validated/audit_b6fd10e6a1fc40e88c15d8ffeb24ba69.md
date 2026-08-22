### Title
Unbounded `prefixQuery` in `DelegatedResourceAccountIndexStore.getIndex`/`getV2Index` enables RPC-handler DoS via attacker-inflated delegation index - ([File: chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java])

### Summary
`DelegatedResourceAccountIndexStore.getWithPrefix` (used by `getIndex`/`getV2Index`) performs two unbounded `prefixQuery` scans over `FROM_PREFIX`/`TO_PREFIX`/`V2_FROM_PREFIX`/`V2_TO_PREFIX` keys for a target address, loads every matching entry into an `ArrayList`, and sorts it by timestamp with no cap on result size. An attacker who repeatedly broadcasts `DelegateResourceContract` transactions to/from many distinct counterpart addresses can grow the number of index entries tied to a single address, making every subsequent `wallet.getDelegatedResourceAccountIndex(V2)` RPC call against that address proportionally more expensive to serve.

### Finding Description
`getWithPrefix` is implemented as: [1](#0-0) 
Each call performs `prefixQuery` (a linear iterator scan bounded only by the key-prefix match) twice, materializes all matches into `ArrayList`s, and sorts them — an O(N log N) operation with no limit, where N is the number of `delegate`/`delegateV2` entries recorded for that address.

Entries are created by `DelegateResourceActuator.execute()` via `delegatedResourceAccountIndexStore.delegate(...)`/`.delegate(...)` (V2), which writes one key per unique `(from, to)` pair: [2](#0-1) 
Because the key is `prefix + from + to`, an attacker must use **distinct counterparty addresses** to add new entries (repeating the same `from`/`to` pair only overwrites the same key, it does not grow N).

The reachable, unprivileged path to trigger the expensive read is `Wallet.getDelegatedResourceAccountIndex`/`getDelegatedResourceAccountIndexV2`, exposed via HTTP/gRPC servlets (`GetDelegatedResourceAccountIndexServlet`, `GetDelegatedResourceAccountIndexV2Servlet`, and Solidity/PBFT node equivalents) with no address-specific check on the store, and no pagination/limit parameter: [3](#0-2) 

Importantly, the premise that `UnDelegateResourceActuator.validate()`/`execute()` triggers this unbounded prefix scan is **incorrect**. That actuator only performs O(1) key lookups (`delegatedResourceStore.get(key)`, `delegatedResourceAccountIndexStore.unDelegateV2(...)` which deletes by exact key) and never calls `getIndex`/`getV2Index`/`getWithPrefix`: [4](#0-3) [5](#0-4) 
So there is no in-consensus/actuator DoS surface here; the only reachable amplification is the read-only RPC/HTTP query path.

### Impact Explanation
This falls under "DoS via RPC-API": an attacker-controllable input (number of prior delegation relationships for an address) directly drives the CPU/memory cost of a specific, unauthenticated, paginated-free RPC call (`getDelegatedResourceAccountIndex`/`V2`). Repeated queries against a heavily-inflated address can consume disproportionate CPU on the node serving the RPC, degrading responsiveness for that endpoint. It does **not** affect block validation, consensus, or `UnDelegateResourceContract` processing, since those paths use O(1) key access, not `prefixQuery`.

### Likelihood Explanation
Preconditions: attacker needs (a) an address with frozen `FreezeBalanceV2` bandwidth/energy balance, and (b) N distinct existing, funded counterparty addresses (since `DelegateResourceActuator.validate()` requires `receiverAddress` to be an existing account and `delegateBalance >= 1 TRX`). Growing N to a size that meaningfully slows a `prefixQuery` (tens/hundreds of thousands of entries) requires the attacker to broadcast that many on-chain `DelegateResourceContract` transactions, each consuming bandwidth/energy and requiring ≥1 TRX of available frozen V2 balance to be locked (recoverable later, not burned) — a real but linearly-scaling cost, publicly visible on-chain. There is a generic QPS rate limiter (`RateLimiterInterceptor`/`RpcApiAccessInterceptor`) that can throttle call frequency, but it does not cap the cost of a single call against an already-inflated address, so the vulnerability is exploitable but requires nontrivial upfront capital lock-up and transaction volume to reach an impactful N.

### Recommendation
Add pagination/limit parameters to `getWithPrefix`/`getIndex`/`getV2Index` (e.g., cap number of scanned/returned entries, or require a start-key/offset for iteration), and/or maintain a bounded/paginated on-disk structure instead of an unbounded prefix scan returned as a single response. At minimum, enforce a maximum result count for the `wallet.getDelegatedResourceAccountIndex(V2)` RPC/HTTP handlers and document/enforce it independent of caller-controlled address history.

### Proof of Concept
```java
// JUnit-style PoC (framework/src/test/java/org/tron/core/db/DelegatedResourceAccountIndexStoreTest.java style)
@Test
public void testGetWithPrefixGrowsUnbounded() {
  byte[] victim = ByteArray.fromHexString(owner1);
  long start = System.nanoTime();
  for (int i = 0; i < N; i++) { // N = 100_000 in a stress test
    byte[] counterparty = randomAddress(i);
    delegatedResourceAccountIndexStore.delegateV2(victim, counterparty, i + 1L);
  }
  long t0 = System.nanoTime();
  DelegatedResourceAccountIndexCapsule result =
      delegatedResourceAccountIndexStore.getV2Index(victim);
  long elapsed = System.nanoTime() - t0;
  // Assert elapsed grows ~linearly/superlinearly with N and exceeds
  // an acceptable per-RPC-call budget (e.g. > single-digit ms target).
  Assert.assertEquals(N, result.getToAccountsList().size());
}
```
Raw sequence: broadcast N `DelegateResourceContract` transactions from a single address to N distinct existing addresses (minimum 1 TRX each, previously frozen via `FreezeBalanceV2Contract`), then repeatedly call `wallet/getdelegatedresourceaccountindexv2` (HTTP) or `GetDelegatedResourceAccountIndexV2` (gRPC) for that address and measure response latency growth as N increases.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L63-89)
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

  public void delegateV2(byte[] from, byte[] to, long time) {
    byte[] fromKey = Bytes.concat(V2_FROM_PREFIX, from, to);
    DelegatedResourceAccountIndexCapsule toIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(to));
    toIndexCapsule.setTimestamp(time);
    this.put(fromKey, toIndexCapsule);

    byte[] toKey = Bytes.concat(V2_TO_PREFIX, to, from);
    DelegatedResourceAccountIndexCapsule fromIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(from));
    fromIndexCapsule.setTimestamp(time);
    this.put(toKey, fromIndexCapsule);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L118-137)
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

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L255-263)
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
```
