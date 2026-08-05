### Title
Unmetered `eth_newFilter` filter complexity enables recurring per-block CPU cost amplification via `LogFilter.matchBloom` - ([File: framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java])

### Finding Description
`LogFilter`'s constructor independently bounds address count (`jsonRpcMaxAddressSize`, default 1000) and each topic-slot's sub-topic count (`jsonRpcMaxSubTopics`, default 1000, up to `maxTopics`=4 slots), but never bounds the *combined* complexity of a single filter. [1](#0-0) [2](#0-1) 

`initBlooms()` builds a `Bloom[][] filterBlooms` with one row per non-empty topic slot plus one row for addresses, each row sized to that slot's cardinality (up to 1000 entries per row, up to 5 rows total: 4 topic slots + addresses). [3](#0-2) 

`matchBloom()` is invoked for every registered filter on every new block via `TronJsonRpcImpl.processLogFilterEntry` -> `handleLogsFilter`, and in the worst case (no match) iterates the full OR-list of every row before failing, i.e. up to `sum(row sizes)` calls to `Bloom.matches()`: [4](#0-3) [5](#0-4) 

`eth_newFilter` (`TronJsonRpcImpl.newFilter`) only checks a simple count cap against `maxLogFilterNum` (default 20,000); it performs no accounting for the size/complexity of the address or topic arrays being registered, and creation is a single, free JSON-RPC call: [6](#0-5) 

Filters persist and continue to be bloom-matched on every block until their 5-minute (`EXPIRE_SECONDS`) TTL is discovered lazily during the *next* `handleLogsFilter` pass, so a maximal filter imposes its full per-block cost for its entire lifetime regardless of whether the caller ever polls it: [7](#0-6) 

As a result, an attacker can repeatedly call `eth_newFilter` with 1000 addresses and 4×1000 sub-topics, up to `maxLogFilterNum` times, each call costing nothing beyond the request itself, while collectively forcing up to ~5000 `Bloom.matches()` operations per filter per block (≈100M operations/block at the documented maxima), for 5 minutes per filter, indefinitely repeatable. The code comment in `TronJsonRpcImpl` itself acknowledges the assumed capacity ("a 3-thread pool can keep up... for each block") was sized against typical filters, not maximal-size ones, and there is no complexity-based admission control or cost accounting to prevent this. [8](#0-7) 

### Impact Explanation
Per-filter bloom-matching cost scales linearly with attacker-chosen address/topic array size (up to ~5000 Bloom comparisons per filter per block), and total node cost scales with the number of concurrently registered filters (up to 20,000 by default). This lets an unprivileged, unauthenticated JSON-RPC caller impose a recurring, multiplicative CPU cost on every subsequent block for the 5-minute filter lifetime, for the fixed price of a single free `eth_newFilter` call, and this can be repeated continuously to sustain the load — a form of underpriced public compute work that can degrade block-processing throughput/latency on nodes with JSON-RPC enabled.

### Likelihood Explanation
Preconditions are the documented defaults (`jsonRpcMaxAddressSize=1000`, `jsonRpcMaxSubTopics=1000`, `jsonRpcMaxLogFilterNum=20000`) which are already shipped in `config.conf`/`reference.conf`. No authentication, fee, or complexity-based rate limiting stands between an attacker and repeated maximal `eth_newFilter` calls — only a flat per-IP/global QPS limiter (default 10,000/50,000 qps) applies, which does not account for request cost and is far above what's needed to fill the filter cap. This is fully reachable by any client with JSON-RPC HTTP access, which is a common node configuration for public RPC endpoints.

### Recommendation
Add a complexity-based limit at filter-creation time in `LogFilter`/`LogFilterWrapper`, e.g., cap the product/sum of address-count and per-slot topic counts (total OR-branch count across all rows) independent of the individual per-dimension caps, or scale the effective per-filter cost against the `maxLogFilterNum` cap (e.g., enforce a global budget of total OR-branches across all active filters, or lower default per-dimension caps). Alternatively, precompute filterBlooms once at creation and add an aggregate size gate before insertion into `eventFilter2Result`, rejecting requests whose combined complexity exceeds a configurable global bound.

### Proof of Concept
1. Unit-test `LogFilter.matchBloom()` cost: construct a `FilterRequest` with 1000 addresses and 4 topic slots each with 1000 sub-topics (respecting `jsonRpcMaxAddressSize`/`jsonRpcMaxSubTopics` defaults), build the `LogFilter`, call `matchBloom(new Bloom())` (guaranteed non-match) in a loop, and assert/benchmark that a single call takes meaningfully longer (proportional to ~5000 `Bloom.matches()` calls) than a minimal filter — extending `framework/src/test/java/org/tron/core/jsonrpc/BloomTest.java`'s `benchmarkMatches` pattern.
2. Integration test extending `framework/src/test/java/org/tron/core/jsonrpc/HandleLogsFilterTest.java`: register N (e.g. hundreds to thousands) maximal-size `LogFilterAndResult` entries in `eventFilter2ResultFull` via `TronJsonRpcImpl.newFilter`-equivalent construction, then invoke `handleLogsFilter` with a `LogsFilterCapsule` carrying a non-matching `Bloom`, and measure/assert wall-clock time scales with filter count × per-filter complexity, demonstrating the cost is unmetered relative to the single `eth_newFilter` call price.

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java (L52-56)
```java
    } else if (fr.getAddress() instanceof ArrayList) {
      int maxAddressSize = Args.getInstance().getJsonRpcMaxAddressSize();
      if (maxAddressSize > 0 && ((ArrayList<?>) fr.getAddress()).size() > maxAddressSize) {
        throw new JsonRpcInvalidParamsException("exceed max addresses: " + maxAddressSize);
      }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java (L92-96)
```java
        } else if (topic instanceof ArrayList) {
          int maxSubTopics = Args.getInstance().getJsonRpcMaxSubTopics();
          if (maxSubTopics > 0 && ((ArrayList<?>) topic).size() > maxSubTopics) {
            throw new JsonRpcInvalidParamsException("exceed max topics: " + maxSubTopics);
          }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java (L135-156)
```java
  private void initBlooms() {
    if (filterBlooms != null) {
      return;
    }

    //topics ahead，address last
    List<byte[][]> addrAndTopics = new ArrayList<>(topics);
    addrAndTopics.add(contractAddresses);

    filterBlooms = new Bloom[addrAndTopics.size()][];
    for (int i = 0; i < addrAndTopics.size(); i++) {
      byte[][] orTopics = addrAndTopics.get(i);
      if (orTopics == null || orTopics.length == 0) {
        filterBlooms[i] = new Bloom[] {new Bloom()}; // always matches
      } else {
        filterBlooms[i] = new Bloom[orTopics.length];
        for (int j = 0; j < orTopics.length; j++) {
          filterBlooms[i][j] = Bloom.create(Hash.sha3(orTopics[j]));
        }
      }
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java (L161-176)
```java
  public boolean matchBloom(Bloom blockBloom) {
    initBlooms();
    for (Bloom[] andBloom : filterBlooms) {
      boolean orMatches = false;
      for (Bloom orBloom : andBloom) {
        if (blockBloom.matches(orBloom)) {
          orMatches = true;
          break;
        }
      }
      if (!orMatches) {
        return false;
      }
    }
    return true;
  }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L125-131)
```java
  public static final int EXPIRE_SECONDS = 5 * 60;
  private final int maxBlockFilterNum = Args.getInstance().getJsonRpcMaxBlockFilterNum();
  private final int maxLogFilterNum = Args.getInstance().getJsonRpcMaxLogFilterNum();
  private static final Cache<LogFilterElement, LogFilterElement> logElementCache =
      CacheBuilder.newBuilder()
          .maximumSize(300_000L) // 300s * tps(1000) * 1 log/tx ≈ 300_000
          .expireAfterWrite(EXPIRE_SECONDS, TimeUnit.SECONDS)
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L183-189)
```java
  /**
   * Using the default maxLogFilterNum of 20,000, a 3-thread pool can keep up with log event
   * processing for each block within the 3-second BLOCK_PRODUCED_INTERVAL. Increasing the thread
   * pool size too much may affect the performance of the main block processing thread.
   */
  private final ForkJoinPool logsFilterPool =
      ExecutorServiceManager.newForkJoinPool("logs-filter-pool", 3);
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L291-294)
```java
    if (logsFilterCapsule.getBloom() != null && !logFilterAndResult.getLogFilterWrapper()
        .getLogFilter().matchBloom(logsFilterCapsule.getBloom())) {
      return;
    }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L1439-1466)
```java
  @Override
  public String newFilter(FilterRequest fr) throws JsonRpcInvalidParamsException,
      JsonRpcMethodNotFoundException, JsonRpcExceedLimitException {
    disableInPBFT("eth_newFilter");

    // not supports finalized as block parameter
    if (FINALIZED_STR.equalsIgnoreCase(fr.getFromBlock())
        || FINALIZED_STR.equalsIgnoreCase(fr.getToBlock())) {
      throw new JsonRpcInvalidParamsException(INVALID_BLOCK_RANGE);
    }

    Map<String, LogFilterAndResult> eventFilter2Result;
    if (getSource() == RequestSource.FULLNODE) {
      eventFilter2Result = eventFilter2ResultFull;
    } else {
      eventFilter2Result = eventFilter2ResultSolidity;
    }
    // Due to concurrent access, the threshold may occasionally be exceeded.
    if (maxLogFilterNum > 0 && eventFilter2Result.size() >= maxLogFilterNum) {
      throw new JsonRpcExceedLimitException(
          "exceed max log filters: " + maxLogFilterNum + ", try again later");
    }
    long currentMaxFullNum = wallet.getNowBlock().getBlockHeader().getRawData().getNumber();
    LogFilterAndResult logFilterAndResult = new LogFilterAndResult(fr, currentMaxFullNum, wallet);
    String filterID = generateFilterId();
    eventFilter2Result.put(filterID, logFilterAndResult);
    return ByteArray.toJsonHex(filterID);
  }
```
