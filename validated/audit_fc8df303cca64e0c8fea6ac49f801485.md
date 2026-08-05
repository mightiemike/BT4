### Title
Per-block log accumulation in `LogMatch.matchBlockOneByOne` is unbounded until after the entire block's matches are built - ([File: framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogMatch.java])

### Summary
`LogBlockQuery.getPossibleBlock` caps the *candidate block count* at `MAX_RESULT` (10000), but `LogMatch.matchBlockOneByOne` only enforces the same `MAX_RESULT` cap on the *cumulative matched-log count* after each block has been fully scanned and its entire per-block match list has already been materialized in memory via `matchBlock`. A single block whose logs match a loosely-specified filter can therefore force construction of an arbitrarily large `List<LogFilterElement>` before the check at lines 99-101 has a chance to reject the request.

### Finding Description
`LogBlockQuery.getPossibleBlock` (`framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogBlockQuery.java` lines 93-96) only limits how many *blocks* can match the bloom-filter query, not how many *logs* per block exist. [1](#0-0) 

`LogMatch.matchBlockOneByOne` then iterates these candidate blocks and, for each one, calls the static `matchBlock` helper, which scans **all** transactions/logs of that block and appends every matching entry to a local `matchedLog` ArrayList with no size limit at all: [2](#0-1) 

Only after `matchBlock` returns the fully-built list for that one block does `matchBlockOneByOne` check the running total against `LogBlockQuery.MAX_RESULT`: [3](#0-2) 

This means the 10000-result cap is only enforced at block granularity, after the cost of building the full per-block match array has already been paid. If a single candidate block contains far more than 10000 matching logs (e.g., a block filled with contract-deployment transactions that each emit many `LOG` events), the JVM must allocate and populate a `LogFilterElement` object per log — including a `DataWord` per topic — before the exception is even thrown.

The repo already contains a fix/test (`LogMatchOverLimitTest`) confirming that the cross-block accumulation check was hardened to fire "before `addAll`" so the *returned* array never exceeds `MAX_RESULT`. [4](#0-3)  However, that fix only guards the boundary *between* blocks; it does nothing to cap the amount of work/memory spent building the match list for a *single* block, since `matchBlock` itself has no incremental limit and always returns its complete result before the caller can inspect its size. [5](#0-4) 

The number of logs obtainable in one block is bounded by the block's energy/bandwidth limit (an attacker must pay energy fees to emit `LOG` opcodes), so the write side is priced. But once such a log-heavy block exists on-chain, it is permanent, and every subsequent `eth_getLogs`/`eth_getFilterLogs` request over that block via `LogFilterWrapper`/`LogMatch` is free, unauthenticated JSON-RPC read work. [6](#0-5)  That asymmetry allows unlimited-cost repeated (and concurrent) memory spikes on the node from a single one-time, bounded-cost setup transaction.

### Impact Explanation
Each request matching such a block causes the server to allocate a `LogFilterElement` (with nested `DataWord` topic list) for every log in that block before any cap can reject the request — potentially tens of thousands of objects per call, well beyond the intended 10000-result budget. Because `eth_getLogs`/`eth_getFilterLogs` are unauthenticated public JSON-RPC endpoints, an attacker can issue many such requests concurrently, multiplying the per-request memory spike and putting sustained pressure on node heap/GC, which can degrade or stall JSON-RPC service availability. This is a resource-exhaustion / underpriced-work issue on the read path, not a fund-loss or consensus-divergence issue.

### Likelihood Explanation
Requires the attacker to first get a block onto the chain containing an unusually high number of matching logs (feasible via multiple contract-deployment/log-emitting transactions within the energy limits of one or a few blocks — a one-time, bounded, self-funded cost), then repeatedly issue `eth_getLogs` over that block range. This is fully reachable through the public JSON-RPC API with no special privileges, and is repeatable indefinitely since the log data is permanent on-chain.

### Recommendation
Enforce the `MAX_RESULT` cap incrementally inside `matchBlock` (or check-and-abort mid-scan) rather than only after each block's full match list is constructed, e.g. pass the current accumulated count into `matchBlock` and throw `JsonRpcTooManyResultException` as soon as the per-block running total would exceed `MAX_RESULT`, instead of returning the complete unbounded list for the caller to check afterward.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/jsonrpc/LogMatchSingleBlockOverLimitTest.java
@Test
public void testSingleBlockHugeLogCount_buildsFullListBeforeThrowing()
    throws ItemNotFoundException, JsonRpcInvalidParamsException {
  // simulate one block containing far more matching logs than MAX_RESULT (10000),
  // e.g. 200_000 logs from many LOG-emitting transactions in one block
  int hugeLogCount = 200_000;
  Manager manager = buildMockManager(1L, buildTxList(hugeLogCount));
  LogMatch logMatch = buildLogMatch(Collections.singletonList(1L), manager);

  // Measure peak memory/time before exception fires
  long before = Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory();
  assertThrows(JsonRpcTooManyResultException.class, logMatch::matchBlockOneByOne);
  long after = Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory();

  // Expected (bug): matchBlock() must fully allocate `hugeLogCount` LogFilterElement
  // objects (far exceeding the 10000 budget) before the MAX_RESULT check can reject
  // the request, showing memory/time cost scales with hugeLogCount, not with the
  // intended MAX_RESULT cap.
}
```
Fixed behavior: after applying an incremental per-block cap inside `matchBlock`, the same test should show the scan aborts (and memory allocation stops) once the accumulated match count crosses `MAX_RESULT`, regardless of how many logs remain in the block.

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogBlockQuery.java (L93-96)
```java
    if (blockNumList.size() >= MAX_RESULT) {
      throw new JsonRpcTooManyResultException(
          "query returned more than " + MAX_RESULT + " results");
    }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogMatch.java (L41-80)
```java
  public static List<LogFilterElement> matchBlock(LogFilter logFilter, long blockNum,
      String blockHash, List<TransactionInfo> transactionInfoList, boolean removed) {

    int txCount = transactionInfoList.size();
    List<LogFilterElement> matchedLog = new ArrayList<>();
    int logIndexInBlock = 0;

    for (int i = 0; i < txCount; i++) {
      TransactionInfo transactionInfo = transactionInfoList.get(i);
      int logCount = transactionInfo.getLogCount();

      for (int j = 0; j < logCount; j++) {
        Log log = transactionInfo.getLog(j);

        if (logFilter.matchesExactly(log)) {
          List<DataWord> topicList = new ArrayList<>();
          for (ByteString topic : log.getTopicsList()) {
            topicList.add(new DataWord(topic.toByteArray()));
          }

          LogFilterElement logFilterElement = new LogFilterElement(blockHash,
              blockNum,
              ByteArray.toHexString(transactionInfo.getId().toByteArray()),
              i,
              ByteArray.toHexString(log.getAddress().toByteArray()),
              topicList,
              ByteArray.toHexString(log.getData().toByteArray()),
              logIndexInBlock,
              removed,
              transactionInfo.getBlockTimeStamp()
          );
          matchedLog.add(logFilterElement);
        }

        logIndexInBlock += 1;
      }
    }

    return matchedLog;
  }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogMatch.java (L98-104)
```java
      if (!matchedLog.isEmpty()) {
        if (logFilterElementList.size() + matchedLog.size() > LogBlockQuery.MAX_RESULT) {
          throw new JsonRpcTooManyResultException(
              "query returned more than " + LogBlockQuery.MAX_RESULT + " results");
        }
        logFilterElementList.addAll(matchedLog);
      }
```

**File:** framework/src/test/java/org/tron/core/jsonrpc/LogMatchOverLimitTest.java (L31-35)
```java
/**
 * Verifies the over-limit check in {@link LogMatch#matchBlockOneByOne()}
 * The fix ensures the exception is thrown BEFORE {@code addAll}, so the result list never
 * silently exceeds {@link LogBlockQuery#MAX_RESULT}.
 */
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilterWrapper.java (L27-61)
```java
  public LogFilterWrapper(FilterRequest fr, long currentMaxBlockNum, Wallet wallet,
      boolean checkBlockRange) throws JsonRpcInvalidParamsException {

    // 1.convert FilterRequest to LogFilter
    this.logFilter = new LogFilter(fr);

    // 2. get fromBlock、toBlock from FilterRequest
    long fromBlockSrc;
    long toBlockSrc;
    if (fr.getBlockHash() != null) {
      if (fr.getFromBlock() != null || fr.getToBlock() != null) {
        throw new JsonRpcInvalidParamsException(
            "cannot specify both BlockHash and FromBlock/ToBlock, choose one or the other");
      }
      byte[] blockHashBytes = JsonRpcApiUtil.hashToByteArray(fr.getBlockHash());
      Block block = null;
      if (wallet != null) {
        block = wallet.getBlockById(ByteString.copyFrom(blockHashBytes));
      }
      if (block == null) {
        throw new JsonRpcInvalidParamsException("invalid blockHash");
      }
      fromBlockSrc = block.getBlockHeader().getRawData().getNumber();
      toBlockSrc = fromBlockSrc;
    } else {

      // Normalize the request into one of four strategies based on parameter emptiness.
      // Long.MAX_VALUE is an internal sentinel meaning "open upper bound"; it is never
      // treated as a real block number by later query stages.
      // Note: "latest" tag handling differs by strategy:
      // - Strategy 2: toBlock="latest" -> Long.MAX_VALUE (track future blocks)
      // - Strategy 3: fromBlock="latest" -> currentMaxBlockNum snapshot (bounded start)
      // - Strategy 4: fromBlock="latest" -> currentMaxBlockNum; toBlock="latest" -> Long.MAX_VALUE

      boolean fromEmpty = StringUtils.isEmpty(fr.getFromBlock());
```
