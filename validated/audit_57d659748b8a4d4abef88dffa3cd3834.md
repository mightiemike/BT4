## Confirmed finding

Based on the code examined, this describes a real, reachable design gap rather than a false positive.

### Title
Unbounded per-filter log queue growth via cost-free `eth_newFilter` enables underpriced memory exhaustion - (File: `framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilterAndResult.java`)

### Summary
`eth_newFilter` creates a `LogFilterAndResult` backed by an unbounded `LinkedBlockingQueue` [1](#0-0) , and `TronJsonRpcImpl.handleLogsFilter` → `processLogFilterEntry` pushes every log matching the filter into that queue on every new block for the full `EXPIRE_SECONDS` (300s) lifetime [2](#0-1) , with no cap on queue size and no cost to the caller beyond the filter-count limit.

### Finding Description
`eth_newFilter` (`TronJsonRpcImpl.newFilter`) only enforces a count limit (`maxLogFilterNum`, default 20000) on the number of concurrently open filters [3](#0-2) ; it does not bound or price the number of log elements a filter may accumulate. An attacker can submit a `FilterRequest` with no address and no topics; `LogFilter.initBlooms()` treats an empty address/topic array as "always matches" (`new Bloom()`) [4](#0-3)  and `matchesContractAddress` returns true when `contractAddresses.length == 0` [5](#0-4) , so every log in every subsequent block matches.

On each new block, `handleLogsFilter` iterates all open filters and calls `processLogFilterEntry`, which (after the block-range and bloom checks) calls `LogMatch.matchBlock` and then unconditionally does `logFilterAndResult.getResult().addAll(localResults)` [2](#0-1) . `LogFilterAndResult.add`/the underlying `result` field is a plain `LinkedBlockingQueue<>()` with no capacity bound [6](#0-5) . The only mechanism that drains the queue is `popAll()`, invoked from `eth_getFilterChanges`/`eth_getFilterLogs`, which the attacker is never required to call. The filter itself only expires via `isExpire()`/`EXPIRE_SECONDS` bookkeeping in `FilterResult` after 300 seconds [7](#0-6) , and even then removal from the map happens lazily, only the next time `processLogFilterEntry` runs against that entry [8](#0-7) .

No existing guard limits per-filter queue length, matched-log count, or total memory; the only pricing mechanism is the flat filter-count cap (`maxLogFilterNum`), which is orthogonal to the amount of data a single filter can accumulate.

### Impact Explanation
With `maxLogFilterNum` at its default (20000) and no address/topic restriction required, an attacker can open many filters that each match all logs in the chain for 5 minutes without ever draining them. Because `LogFilterElement` instances are deduplicated via `logElementCache` (so the payload itself isn't duplicated per filter) [9](#0-8) , the amplification is in queue node objects/references rather than full log payload duplication — but with tens of thousands of filters times hundreds of matched logs per 5-minute window, the aggregate queue-node overhead and retained-object graph size can still become significant, and this scales linearly with both filter count and network log volume, none of which cost the attacker anything beyond issuing HTTP requests.

### Likelihood Explanation
Feasible under default configuration: `eth_newFilter` requires no authentication, has no per-filter drain requirement, and the empty `FilterRequest` matching pattern is explicitly supported. The only friction is the flat `maxLogFilterNum` cap, which limits filter count but not per-filter accumulation, so the attack is straightforward to execute repeatedly (create near cap, wait, let filters expire, repeat).

### Recommendation
Bound the per-filter result queue (e.g., cap `LogFilterAndResult`'s `result` size and drop/reject-oldest or reject-new elements once a threshold is hit, similar to the existing `logElementCache` sizing rationale), and/or price filter creation relative to matched-log volume (e.g., require address/topic narrowing, or track and limit total outstanding unconsumed elements across a client's filters). Alternatively, proactively expire and clear filters as soon as `isExpire()` becomes true rather than lazily on next block processing.

### Proof of Concept
Extend `HandleLogsFilterTest`-style setup:
```java
@Test
public void testUnboundedQueueGrowth_noDrain() throws Exception {
  FilterRequest fr = new FilterRequest(); // no address/topics => matches all
  LogFilterAndResult filterAndResult = new LogFilterAndResult(fr, 0L, null);
  jsonRpc.getEventFilter2ResultFull().put("attacker-filter", filterAndResult);

  int blocks = 100; // within 300s TTL window
  for (long b = 1; b <= blocks; b++) {
    List<TransactionInfo> txInfoList = Collections.singletonList(buildTxInfoWithLog(new byte[20]));
    LogsFilterCapsule capsule = new LogsFilterCapsule(b, "0xhash" + b, null, txInfoList, false, false);
    jsonRpc.handleLogsFilter(capsule); // never call popAll()
  }

  // Assert queue grows unboundedly with block count, never capped
  Assert.assertEquals(blocks, filterAndResult.getResult().size());
}
```
Expected assertion: queue size scales linearly with `blocks` with no upper bound enforced by `LogFilterAndResult`/`FilterResult`, demonstrating that memory consumption is unbounded for the full 300s TTL absent any drain call.

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilterAndResult.java (L17-28)
```java
  public LogFilterAndResult(FilterRequest fr, long currentMaxBlockNum, Wallet wallet)
      throws JsonRpcInvalidParamsException {
    // eth_newFilter, no need to check block range
    this.logFilterWrapper = new LogFilterWrapper(fr, currentMaxBlockNum, wallet, false);
    result = new LinkedBlockingQueue<>();
    this.updateExpireTime();
  }

  @Override
  public void add(LogFilterElement logFilterElement) {
    result.add(logFilterElement);
  }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L278-282)
```java
    LogFilterAndResult logFilterAndResult = entry.getValue();
    if (logFilterAndResult.isExpire()) {
      eventFilterMap.remove(entry.getKey());
      return;
    }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L296-313)
```java
    LogFilter logFilter = logFilterAndResult.getLogFilterWrapper().getLogFilter();
    List<LogFilterElement> elements =
        LogMatch.matchBlock(logFilter, blockNumber, logsFilterCapsule.getBlockHash(),
            logsFilterCapsule.getTxInfoList(), logsFilterCapsule.isRemoved());

    List<LogFilterElement> localResults = new ArrayList<>(elements.size());
    for (LogFilterElement element : elements) {
      LogFilterElement cachedElement;
      try {
        // compare with hashcode() first, then with equals(). If not exist, put it.
        cachedElement = logElementCache.get(element, () -> element);
      } catch (ExecutionException e) {
        logger.error("Getting/loading LogFilterElement from cache fails", e); // never happen
        cachedElement = element;
      }
      localResults.add(cachedElement);
    }
    logFilterAndResult.getResult().addAll(localResults);
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L1456-1465)
```java
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
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java (L144-148)
```java
    filterBlooms = new Bloom[addrAndTopics.size()][];
    for (int i = 0; i < addrAndTopics.size(); i++) {
      byte[][] orTopics = addrAndTopics.get(i);
      if (orTopics == null || orTopics.length == 0) {
        filterBlooms[i] = new Bloom[] {new Bloom()}; // always matches
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilter.java (L178-186)
```java
  private boolean matchesContractAddress(byte[] toAddr) {
    //not have 41 ahead both
    for (byte[] address : contractAddresses) {
      if (Arrays.equals(address, toAddr)) {
        return true;
      }
    }
    return contractAddresses.length == 0;
  }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/FilterResult.java (L10-21)
```java
  private long expireTimeStamp;

  @Getter
  protected BlockingQueue<T> result;

  public void updateExpireTime() {
    expireTimeStamp = System.currentTimeMillis() + TronJsonRpcImpl.EXPIRE_SECONDS * 1000;
  }

  public boolean isExpire() {
    return expireTimeStamp < System.currentTimeMillis();
  }
```
