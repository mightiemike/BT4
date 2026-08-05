### Title
Underpriced disk/CPU state iteration in JSON-RPC log queries - (framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogMatch.java)

### Summary
`LogMatch.matchBlockOneByOne` iterates every transaction and every log in every block of a `maxBlockRange=5000` window before the `LogBlockQuery.MAX_RESULT=10000` cap can stop it. An unprivileged caller can request a dense historical range with no address/topic filter, causing the node to load up to 5000 `TransactionInfoList` protobufs from disk and scan all their logs for a single `eth_getLogs`/`eth_getFilterLogs` call. By default the JSON-RPC servlet is rate-limited only by the global per-endpoint QPS=1000 fallback, not per-IP, so the request pattern can be repeated cheaply.

### Finding Description
Entrypoint: public HTTP POST to `/jsonrpc` with method `eth_getLogs` or `eth_getFilterLogs`, handled by `JsonRpcServlet` (extends `RateLimiterServlet`) and dispatched to `TronJsonRpcImpl.getLogs` / `getFilterLogs`.

Code path:
1. `LogFilterWrapper` validates `toBlock - fromBlock <= maxBlockRange` (default 5000) [1](#0-0) .
2. `LogBlockQuery` builds the list of candidate block numbers.
3. `LogMatch.matchBlockOneByOne` loops over `blockNumList` and for each block calls `manager.getTransactionInfoByBlockNum(blockNum)` [2](#0-1) .
4. `matchBlock` then iterates every `TransactionInfo` and every `Log` in that block, applying `logFilter.matchesExactly(log)` [3](#0-2) .
5. The `MAX_RESULT=10000` check only runs after a full block has been processed and only if `!matchedLog.isEmpty()` [4](#0-3) .

Root cause: the result cap is result-count-based, not work-based. If the attacker chooses an address/topic filter that matches nothing (or matches only at the very end), the function still loads and deserializes all `TransactionInfoList` objects for 5000 blocks and scans every log before returning an empty or small result. Even when logs do match, each full block is loaded and scanned before the cap can abort. There is no cost guard proportional to blocks touched, bytes read, or logs inspected.

Rate limiting: `JsonRpcServlet` extends `RateLimiterServlet`. When no `rate.limiter.http` entry exists for `JsonRpcServlet`, the default adapter `DefaultBaseQqsAdapter` is used with `QpsStrategy.DEFAULT_QPS_PARAM = "qps=1000"` [5](#0-4) [6](#0-5) . This is a node-wide/global QPS limit, not per-IP. The default `config.conf` and `reference.conf` ship with empty `http`/`rpc` rate-limiter lists [7](#0-6) [8](#0-7) , so the per-IP fallback is not active for JSON-RPC methods unless an operator explicitly configures `IPQPSRateLimiterAdapter` for `JsonRpcServlet`.

### Impact Explanation
A single request can force the node to perform large amounts of disk I/O and CPU work (deserializing 5000 `TransactionInfoList` protobufs and scanning all logs) while consuming only one QPS token and returning at most a small or empty payload. Repeated requests can sustain high disk/CPU load, degrading responsiveness of other RPCs and block processing on the full node. This is materially underpriced public work: the cost to the node is unbounded in blocks/logs scanned, while the cost to the caller is bounded by the small JSON-RPC request and the default global QPS limit.

### Likelihood Explanation
Preconditions:
- `node.jsonrpc.httpFullNodeEnable = true` (or solidity/PBFT JSON-RPC enabled).
- Default `rate.limiter` with no `JsonRpcServlet` entry, so the global per-endpoint QPS=1000 fallback applies.
- Attacker knows or discovers a 5000-block window with high transaction/log density (public chain history is sufficient).

Feasibility: high. The attacker only needs to issue `eth_getLogs` requests with `fromBlock/toBlock` spanning 5000 blocks and no restrictive address/topic filter. The block-range cap is enforced, but 5000 blocks is the allowed maximum. Repeatability: high, because the limiter is global QPS, not per-IP, so a single client (or a few clients) can consume the full 1000 req/s quota with these expensive queries.

### Recommendation
Add a work budget guard in `LogMatch.matchBlockOneByOne` that aborts early based on blocks scanned, transaction-info records loaded, or logs inspected, not only on final result count. For example, track `blocksScanned` and `logsInspected` and throw `JsonRpcTooManyResultException` (or a new `JsonRpcQueryTooLargeException`) when either exceeds a configurable threshold before `MAX_RESULT` is reached. Alternatively, require at least one address or topic filter for wide ranges, or charge/weight the per-endpoint rate limiter by estimated work. Also consider applying `IPQPSRateLimiterAdapter` to `JsonRpcServlet` by default.

### Proof of Concept
Add a unit/fuzz test in `framework/src/test/java/org/tron/core/jsonrpc/LogMatchWorkBudgetTest.java`:

```java
@Test
public void testWideRangeDenseBlockAbortsOnWorkBudget()
    throws ItemNotFoundException, JsonRpcInvalidParamsException {
  int maxBlockRange = 5000;
  int logsPerBlock = 100; // dense contract activity
  int maxWorkBudget = 100_000; // proposed new guard

  Manager manager = mock(Manager.class);
  ChainBaseManager chainBaseManager = mock(ChainBaseManager.class);
  when(manager.getChainBaseManager()).thenReturn(chainBaseManager);
  when(chainBaseManager.getBlockIdByNum(anyLong()))
      .thenReturn(new BlockCapsule.BlockId(Sha256Hash.ZERO_HASH, 0));

  // All logs use a non-matching address so result count stays zero but work is huge.
  ByteString unmatchedAddr = ByteString.copyFromUtf8("unmatched");
  for (long b = 0; b < maxBlockRange; b++) {
    TransactionInfo.Builder tx = TransactionInfo.newBuilder();
    for (int i = 0; i < logsPerBlock; i++) {
      tx.addLog(Log.newBuilder().setAddress(unmatchedAddr).build());
    }
    when(manager.getTransactionInfoByBlockNum(b))
        .thenReturn(TransactionInfoList.newBuilder()
            .addTransactionInfo(tx.build()).build());
  }

  FilterRequest fr = new FilterRequest("0x0", "0x" + Integer.toHexString(maxBlockRange - 1),
      null, null, null);
  LogFilterWrapper wrapper = new LogFilterWrapper(fr, maxBlockRange - 1, null, false);
  LogMatch logMatch = new LogMatch(wrapper,
      LongStream.range(0, maxBlockRange).boxed().collect(Collectors.toList()), manager);

  // Expected after fix: throws before all 5000 blocks are scanned.
  assertThrows(JsonRpcTooManyResultException.class, logMatch::matchBlockOneByOne);

  // Current behavior (before fix): returns empty array after scanning 5000*100 logs.
  // Verify by counting getTransactionInfoByBlockNum invocations:
  //   verify(manager, atMost(1000)).getTransactionInfoByBlockNum(anyLong());
}
```

A load-test PoC would start `FullNodeJsonRpcHttpService`, issue concurrent `eth_getLogs` requests spanning 5000 dense blocks, and assert that p99 latency of a simple `eth_blockNumber` call degrades relative to baseline.

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogFilterWrapper.java (L114-119)
```java
  public void validateBlockRange(long currentMaxBlockNum) throws JsonRpcInvalidParamsException {
    int maxBlockRange = Args.getInstance().getJsonRpcMaxBlockRange();
    if (maxBlockRange > 0 && min(toBlock, currentMaxBlockNum) - fromBlock > maxBlockRange) {
      throw new JsonRpcInvalidParamsException("exceed max block range: " + maxBlockRange);
    }
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

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/filters/LogMatch.java (L82-108)
```java
  public LogFilterElement[] matchBlockOneByOne()
      throws BadItemException, ItemNotFoundException, JsonRpcTooManyResultException {
    List<LogFilterElement> logFilterElementList = new ArrayList<>();

    for (long blockNum : blockNumList) {
      List<TransactionInfo> transactionInfoList =
              manager.getTransactionInfoByBlockNum(blockNum).getTransactionInfoList();
      //if query condition (address and topics) is empty, we will traversal every block,
      //include empty block
      if (transactionInfoList.isEmpty()) {
        continue;
      }
      String blockHash = manager.getChainBaseManager().getBlockIdByNum(blockNum).toString();
      List<LogFilterElement> matchedLog = matchBlock(logFilterWrapper.getLogFilter(), blockNum,
          blockHash, transactionInfoList, false);

      if (!matchedLog.isEmpty()) {
        if (logFilterElementList.size() + matchedLog.size() > LogBlockQuery.MAX_RESULT) {
          throw new JsonRpcTooManyResultException(
              "query returned more than " + LogBlockQuery.MAX_RESULT + " results");
        }
        logFilterElementList.addAll(matchedLog);
      }
    }

    return logFilterElementList.toArray(new LogFilterElement[0]);
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L59-80)
```java
  @PostConstruct
  private void addRateContainer() {
    final String name = getClass().getSimpleName();
    RateLimiterInitialization.HttpRateLimiterItem item = Args.getInstance()
        .getRateLimiterInitialization().getHttpMap().get(name);

    String cName;
    String params;
    if (item == null) {
      cName = DEFAULT_ADAPTER_NAME;
      params = QpsStrategy.DEFAULT_QPS_PARAM;
    } else {
      cName = item.getStrategy();
      params = item.getParams();
    }

    try {
      container.add(KEY_PREFIX_HTTP, name, buildAdapter(cName, params, name));
    } catch (Exception e) {
      throw rateLimiterInitError(cName, params, name, e);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/strategy/QpsStrategy.java (L10-14)
```java
public class QpsStrategy extends Strategy {
  public static final String STRATEGY_PARAM_QPS = "qps";
  public static final int DEFAULT_QPS = Args.getInstance().getRateLimiterGlobalApiQps();
  public static final String DEFAULT_QPS_PARAM = "qps=" + DEFAULT_QPS;
  private RateLimiter rateLimiter;
```

**File:** common/src/main/resources/reference.conf (L453-521)
```text
## Rate limiter config
rate.limiter = {
  # Each HTTP servlet and gRPC method can have its own rate-limit strategy.
  # Three API rate-limit strategies are available:
  #   GlobalPreemptibleAdapter – limits maximum concurrent requests globally.
  #                              paramString = "permit=N" (N = max concurrent calls)
  #   QpsRateLimiterAdapter    – limits average QPS across all callers.
  #                              paramString = "qps=N" (N may be a decimal)
  #   IPQPSRateLimiterAdapter  – limits average QPS per source IP.
  #                              paramString = "qps=N" (N may be a decimal)
  # If no strategy is configured for an endpoint, QpsRateLimiterAdapter with
  # qps=1000 is applied automatically.

  # Per-servlet HTTP rate limits. component is the servlet class simple name.
  http = [
    # {
    #   component = "GetNowBlockServlet",
    #   strategy = "GlobalPreemptibleAdapter",
    #   paramString = "permit=1"
    # },
    # {
    #   component = "GetAccountServlet",
    #   strategy = "IPQPSRateLimiterAdapter",
    #   paramString = "qps=1"
    # },
    # {
    #   component = "ListWitnessesServlet",
    #   strategy = "QpsRateLimiterAdapter",
    #   paramString = "qps=1"
    # }
  ]

  # Per-method gRPC rate limits. component is "package.ServiceName/MethodName".
  rpc = [
    # {
    #   component = "protocol.Wallet/GetBlockByLatestNum2",
    #   strategy = "GlobalPreemptibleAdapter",
    #   paramString = "permit=1"
    # },
    # {
    #   component = "protocol.Wallet/GetAccount",
    #   strategy = "IPQPSRateLimiterAdapter",
    #   paramString = "qps=1"
    # },
    # {
    #   component = "protocol.Wallet/ListWitnesses",
    #   strategy = "QpsRateLimiterAdapter",
    #   paramString = "qps=1"
    # }
  ]

  # P2P message rate limits.
  p2p = {
    # QPS ceiling for individual P2P message types received from peers.
    # Values are doubles; fractional QPS is allowed (e.g. 0.5 = one per 2 s).
    syncBlockChain = 3.0  # SyncBlockChain handshake messages
    fetchInvData = 3.0    # FetchInvData (block/tx fetch) messages
    disconnect = 1.0      # Disconnect messages
  }

  # Node-wide QPS ceiling across all HTTP + gRPC requests combined.
  global.qps = 50000
  # Per-source-IP QPS ceiling across all HTTP + gRPC requests from that IP.
  global.ip.qps = 10000
  # Default per-endpoint QPS limit applied to any endpoint with no explicit strategy.
  global.api.qps = 1000
  # true = reject over-limit requests immediately; false = queue and block the caller.
  apiNonBlocking = false
}
```

**File:** framework/src/main/resources/config.conf (L174-195)
```text
## rate limiter config
rate.limiter = {
  # See reference.conf for available strategies (GlobalPreemptibleAdapter, QpsRateLimiterAdapter, IPQPSRateLimiterAdapter).
  http = [
  ],

  rpc = [
  ]

  p2p = {
    # syncBlockChain = 3.0
    # fetchInvData = 3.0
    # disconnect = 1.0
  }

  # global qps, default 50000
  global.qps = 50000
  # IP-based global qps, default 10000
  global.ip.qps = 10000
  # If true, API rate limiters reject immediately on overload (non-blocking). Default: false
  apiNonBlocking = false
}
```
