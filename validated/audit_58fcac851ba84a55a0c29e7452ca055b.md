## Answer

Yes — this is a real, reachable underpriced-read-cost path.

### Reachable path

The public HTTP endpoint `GetDelegatedResourceAccountIndexServlet.doGet/doPost` (only wrapped by `RateLimiterServlet`, which limits request *rate*, not per-request *work*) calls `Wallet.getDelegatedResourceAccountIndex`, which reaches `DelegatedResourceAccountIndexStore.getIndex` → `getWithPrefix`, which calls `this.prefixQuery(key)` [1](#0-0) [2](#0-1) .

`prefixQuery` flows through `TronStoreWithRevoking.prefixQuery` → `IRevokingDB.prefixQuery` (Chainbase) → `Chainbase.prefixQuery`, which merges a root‑level LSM-tree seek (`prefixQueryRoot`, which is O(match) via LevelDB/RocksDB iterator seek+early break) with `prefixQuerySnapshot` [3](#0-2) .

`prefixQuerySnapshot` calls `SnapshotImpl.collect(all, key)`, which is the function in question:

```java
synchronized void collect(Map<WrappedByteArray, WrappedByteArray> all, byte[] prefix) {
  Snapshot next = getRoot().getNext();
  while (next != null) {
    Streams.stream(((SnapshotImpl) next).db).filter(e -> Bytes.indexOf(
            Objects.requireNonNull(e.getKey().getBytes()), prefix) == 0)
        .forEach(e -> all.put(...));
    next = next.getNext();
  }
}
``` [4](#0-3) 

This walks **every** uncommitted snapshot layer from the root's first "next" through the head, and for **every** dirty key in every layer applies `Bytes.indexOf` — a full linear scan/filter over the entire in-memory `db` (`HashDB`) of each layer, regardless of the prefix's match rate. There is no seek/index by prefix in the snapshot layers — only the persisted root store (`LevelDbDataSourceImpl.prefixQuery` / `RocksDbDataSourceImpl.prefixQuery`) uses an ordered iterator with `seek` and early-return-on-mismatch, giving proper O(match) cost [5](#0-4) .

### Cost/guard check

- `RateLimiterServlet` limits requests-per-second, not CPU work per request, so it does nothing to bound the cost of a single call scanning all dirty snapshot entries.
- There's no per-request cost accounting/fee for this HTTP read endpoint (unlike EVM opcodes charged with energy); this is a full-node query API.
- The same `collect`/`collect(all,prefix)` pattern is also used by `Chainbase.getNext` (account-trace) and `iterator()`, so any other prefixQuery/iterator-based public endpoint built on `Chainbase`/`SnapshotImpl` shares the issue, but the concretely reachable, unauthenticated instance is `GetDelegatedResourceAccountIndexServlet`/`GetDelegatedResourceAccountIndexV2Servlet` (and their PBFT/Solidity node mirrors) via `DelegatedResourceAccountIndexStore.getWithPrefix`.

An attacker can inflate the number of dirty entries across snapshot layers by sending many disjoint-key transactions (e.g., many `DelegateResource`/`UnDelegateResource` transactions creating distinct `DelegatedResourceAccountIndexStore` keys) before a block/snapshot solidifies, then repeatedly call the read-only HTTP endpoint with an address prefix that matches few or none of those entries. Each call causes `collect(all, prefix)` to stream and filter the full set of dirty keys in every uncommitted layer, so latency/CPU scales with total dirty-key count, not with the matched-prefix result size or the request's own rate-limit weight.

### Title
Public delegated-resource-index HTTP endpoint triggers unbounded prefix scan over all dirty snapshot layers via `SnapshotImpl.collect(Map, byte[])` - (File: chainbase/src/main/java/org/tron/core/db2/core/SnapshotImpl.java)

### Summary
`SnapshotImpl.collect(Map, byte[] prefix)` performs a full linear scan and `Bytes.indexOf` filter over every key in every uncommitted snapshot layer instead of seeking by prefix, and this is reachable from the public, only rate-limited (not cost-limited) HTTP endpoint `GetDelegatedResourceAccountIndexServlet` via `DelegatedResourceAccountIndexStore.getWithPrefix` → `prefixQuery` → `Chainbase.prefixQuerySnapshot`. An attacker who inflates the number of dirty snapshot keys and issues repeated narrow-prefix queries causes CPU/latency cost proportional to total dirty-key count rather than to the size of the matched-prefix result.

### Finding Description
`Chainbase.prefixQuery` combines an O(match)-cost root-store prefix seek with `prefixQuerySnapshot`, which invokes `SnapshotImpl.collect(all, key)` [6](#0-5) . `collect` walks the linked list of snapshot layers from `getRoot().getNext()` to head and, for each layer, uses `Streams.stream(db).filter(...).forEach(...)` to test every key in that layer's in-memory `HashDB` against the prefix via `Bytes.indexOf` [4](#0-3) . This is a full scan regardless of how selective the prefix is — cost is O(total dirty keys across all uncommitted layers), not O(matched keys). `DelegatedResourceAccountIndexStore.getWithPrefix` calls `this.prefixQuery(key)` twice per request (once for `fromPrefix`, once for `toPrefix`) [7](#0-6) , and this is invoked by the public, unauthenticated HTTP servlet `GetDelegatedResourceAccountIndexServlet`, which only applies a request-rate limiter, not per-request compute-cost accounting [8](#0-7) .

### Impact Explanation
An attacker can drive up node CPU usage on a read-only, public API path disproportionately to the number of requests sent (each request is "cheap" under the rate limiter but internally does O(total dirty keys) work), enabling a denial-of-service amplification against full/solidity/PBFT nodes exposing this HTTP API, degrading availability of read services for legitimate users.

### Likelihood Explanation
Feasible and repeatable: requires only sending disjoint-key transactions (e.g., many delegate/undelegate resource transactions creating distinct `DelegatedResourceAccountIndexStore` keys) within recent, uncommitted blocks to build up dirty snapshot layers, then repeatedly calling the public HTTP endpoint with a narrow/non-matching prefix — no privileged access required.

### Recommendation
Bound or eliminate the linear scan in `SnapshotImpl.collect(Map, byte[] prefix)`: maintain per-layer sorted/indexed structures (e.g., a `TreeMap`/prefix-indexed structure) to support seek-based prefix lookups similar to the LevelDB/RocksDB `prefixQuery` implementations, or cap/rate-limit the maximum number of dirty layers/keys scanned per request and reject/charge for excessively large prefix queries on public HTTP/gRPC endpoints.

### Proof of Concept
Java integration test plan (using `ChainbaseTest`/`DelegatedResourceAccountIndexStoreTest` patterns already in the repo):
1. Create a `Chainbase`/`TronStoreWithRevoking`-backed store, call `.newInstance()` to create N uncommitted snapshot layers.
2. In each layer, `put` M keys with random unrelated prefixes (simulating disjoint-key transactions), none matching a target `narrowPrefix`.
3. Measure wall-clock time of `store.prefixQuery(narrowPrefix)` (or `DelegatedResourceAccountIndexStore.getWithPrefix`) as N*M grows (e.g., 10k, 100k, 1M total keys), while the number of query calls and prefix stays constant.
4. Assert latency grows roughly linearly with total dirty-key count (N*M) rather than remaining flat/near-constant — demonstrating cost is driven by total store size, not by the (empty) matched-result set, confirming the underpriced-work invariant violation.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexServlet.java (L17-67)
```java
@Component
@Slf4j(topic = "API")
public class GetDelegatedResourceAccountIndexServlet extends RateLimiterServlet {

  @Autowired
  private Wallet wallet;

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

  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      boolean visible = params.isVisible();
      String input = params.getParams();
      if (visible) {
        JSONObject jsonObject = JSONObject.parseObject(input);
        String value = jsonObject.getString("value");
        jsonObject.put("value", Util.getHexAddress(value));
        input = jsonObject.toJSONString();
      }

      BytesMessage.Builder build = BytesMessage.newBuilder();
      JsonFormat.merge(input, build, visible);

      fillResponse(build.getValue(), visible, response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  private void fillResponse(ByteString address, boolean visible, HttpServletResponse response)
      throws IOException {
    DelegatedResourceAccountIndex reply =
        wallet.getDelegatedResourceAccountIndex(address);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
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

**File:** chainbase/src/main/java/org/tron/core/db2/core/Chainbase.java (L352-379)
```java
  public Map<WrappedByteArray, byte[]> prefixQuery(byte[] key) {
    Map<WrappedByteArray, byte[]> result = prefixQueryRoot(key);
    Map<WrappedByteArray, byte[]>  snapshot = prefixQuerySnapshot(key);
    result.putAll(snapshot);
    result.entrySet().removeIf(e -> e.getValue() == null);
    return result;
  }

  private Map<WrappedByteArray, byte[]> prefixQueryRoot(byte[] key) {
    Map<WrappedByteArray, byte[]> result = new HashMap<>();
    if (((SnapshotRoot) head.getRoot()).db.getClass() == LevelDB.class) {
      result = ((LevelDB) ((SnapshotRoot) head.getRoot()).db).getDb().prefixQuery(key);
    } else if (((SnapshotRoot) head.getRoot()).db.getClass() == RocksDB.class) {
      result = ((RocksDB) ((SnapshotRoot) head.getRoot()).db).getDb().prefixQuery(key);
    }
    return result;
  }

  private Map<WrappedByteArray, byte[]> prefixQuerySnapshot(byte[] key) {
    Map<WrappedByteArray, byte[]> result = new HashMap<>();
    Snapshot snapshot = head();
    if (!snapshot.equals(head.getRoot())) {
      Map<WrappedByteArray, WrappedByteArray> all = new HashMap<>();
      ((SnapshotImpl) snapshot).collect(all, key);
      all.forEach((k, v) -> result.put(k, v.getBytes()));
    }
    return result;
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotImpl.java (L138-147)
```java
  synchronized void collect(Map<WrappedByteArray, WrappedByteArray> all, byte[] prefix) {
    Snapshot next = getRoot().getNext();
    while (next != null) {
      Streams.stream(((SnapshotImpl) next).db).filter(e -> Bytes.indexOf(
              Objects.requireNonNull(e.getKey().getBytes()), prefix) == 0)
          .forEach(e -> all.put(WrappedByteArray.of(e.getKey().getBytes()),
              WrappedByteArray.of(e.getValue().getBytes())));
      next = next.getNext();
    }
  }
```

**File:** chainbase/src/main/java/org/tron/common/storage/rocksdb/RocksDbDataSourceImpl.java (L380-397)
```java
  @Override
  public Map<WrappedByteArray, byte[]> prefixQuery(byte[] key) {
    resetDbLock.readLock().lock();
    try (final ReadOptions readOptions = getReadOptions();
         final RocksIterator iterator = getRocksIterator(readOptions)) {
      Map<WrappedByteArray, byte[]> result = new HashMap<>();
      for (iterator.seek(key); iterator.isValid(); iterator.next()) {
        if (Bytes.indexOf(iterator.key(), key) == 0) {
          result.put(WrappedByteArray.of(iterator.key()), iterator.value());
        } else {
          return result;
        }
      }
      return result;
    } finally {
      resetDbLock.readLock().unlock();
    }
  }
```
