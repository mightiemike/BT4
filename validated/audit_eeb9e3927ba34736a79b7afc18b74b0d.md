[1](#0-0) [2](#0-1)

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L114-120)
```java
@Slf4j(topic = "API")
@Component
public class TronJsonRpcImpl implements TronJsonRpc, Closeable {

  public enum RequestSource {
    FULLNODE,
    SOLIDITY,
```

**File:** framework/src/test/java/org/tron/core/jsonrpc/HandleLogsFilterTest.java (L194-223)
```java
  private void setParallelThreshold(int value) {
    jsonRpc.setFilterParallelThreshold(value);
  }

  /**
   * Parallel path: every matching filter receives exactly one event — no events dropped or
   * double-counted under concurrent dispatch.
   */
  @Test(timeout = 10000)
  public void testParallelPath_allMatchingFilters_receiveEvents() throws Exception {
    setParallelThreshold(2);
    int count = 5;
    FilterRequest fr = new FilterRequest();
    List<TransactionInfo> txInfoList =
        Collections.singletonList(buildTxInfoWithLog(new byte[20]));
    Map<String, LogFilterAndResult> map = jsonRpc.getEventFilter2ResultFull();
    String prefix = "parallel-match-";
    for (int i = 0; i < count; i++) {
      map.put(prefix + i, new LogFilterAndResult(fr, 0L, null));
    }

    LogsFilterCapsule capsule =
        new LogsFilterCapsule(150L, "0xabcdef", null, txInfoList, false, false);
    jsonRpc.handleLogsFilter(capsule);

    for (int i = 0; i < count; i++) {
      Assert.assertEquals("filter " + i + " must receive exactly one event",
          1, map.get(prefix + i).getResult().size());
    }
  }
```
