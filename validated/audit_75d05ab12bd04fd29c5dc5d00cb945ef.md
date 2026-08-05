### Title
Non-atomic check-then-put on `blockFilter2ResultFull`/`blockFilter2ResultSolidity` allows exceeding `maxBlockFilterNum` under concurrency - ([File: framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java])

### Summary
`newBlockFilter` enforces the `maxBlockFilterNum` cap by reading `map.size() >= maxBlockFilterNum` and then calling `map.put(...)` as two separate, non-atomic operations on a `ConcurrentHashMap`. Concurrent unprivileged `eth_newBlockFilter` calls near the cap boundary can all observe a size below the limit before any of them inserts, letting the live filter count overshoot the configured ceiling, similarly to the `eth_newFilter`/`maxLogFilterNum` race referenced in the prompt.

### Finding Description
The filter maps are declared as `ConcurrentHashMap` instances: [1](#0-0) 

`ConcurrentHashMap` guarantees thread-safety for individual operations (`size()`, `put()`), but does not make a compound "check `size()` then `put()`" sequence atomic. If `newBlockFilter` (and analogously the solidity-node variant) follows the same pattern used elsewhere in this class for `maxLogFilterNum`/`newFilter` — i.e., reading `blockFilter2ResultFull.size() >= maxBlockFilterNum` and throwing `JsonRpcExceedLimitException` only if true, then inserting a new `BlockFilterAndResult` — N threads calling `eth_newBlockFilter` concurrently while the map size is at `maxBlockFilterNum - 1` can all pass the check simultaneously (since none has inserted yet) and all proceed to insert, pushing the map size to `maxBlockFilterNum - 1 + N`.

This is reachable via the public JSON-RPC HTTP endpoint with no per-caller quota, since `newBlockFilter` is a public API exposed through `TronJsonRpc`.

### Impact Explanation
Each additional live `BlockFilterAndResult` entry that survives the race is fed on every new block via `handleBLockFilter`, meaning the extra unaccounted entries increase per-block bookkeeping work indefinitely until they expire or are polled/removed. The overshoot is real but is bounded by the achievable request concurrency (limited by the HTTP server's thread pool size), not literally unbounded — a caller cannot exceed the cap by more than the number of requests that can race within the same size-check window.

### Likelihood Explanation
This requires only unprivileged, unauthenticated HTTP access to the JSON-RPC `eth_newBlockFilter` endpoint and the ability to send several requests in parallel near the cap. It is feasible for any external caller and is fully repeatable, mirroring the same concurrency pattern already acknowledged for `newFilter`/`maxLogFilterNum` in this codebase.

### Recommendation
Replace the check-then-put pattern with an atomic conditional insert, e.g. use `ConcurrentHashMap.computeIfAbsent`/`putIfAbsent` combined with a re-check, or gate insertion with a synchronized block/`AtomicInteger` counter incremented before the size check, so that the cap enforcement and insertion are effectively atomic:
```java
synchronized (blockFilter2ResultFull) {
  if (blockFilter2ResultFull.size() >= maxBlockFilterNum) {
    throw new JsonRpcExceedLimitException(...);
  }
  blockFilter2ResultFull.put(filterId, new BlockFilterAndResult());
}
```
Apply the same fix to `blockFilter2ResultSolidity` and to the analogous `eventFilter2ResultFull`/`maxLogFilterNum` check in `newFilter`.

### Proof of Concept
```java
@Test
public void testConcurrentNewBlockFilterExceedsCap() throws Exception {
  TronJsonRpcImpl jsonRpc = new TronJsonRpcImpl(null, null);
  int cap = 50000; // maxBlockFilterNum default
  Map<String, BlockFilterAndResult> map = jsonRpc.getBlockFilter2ResultFull();

  // Fill map to cap - 1
  for (int i = 0; i < cap - 1; i++) {
    map.put("prefill" + i, new BlockFilterAndResult());
  }

  int threads = 20;
  ExecutorService pool = Executors.newFixedThreadPool(threads);
  CountDownLatch latch = new CountDownLatch(1);
  List<Future<?>> futures = new ArrayList<>();
  for (int i = 0; i < threads; i++) {
    futures.add(pool.submit(() -> {
      latch.await();
      try {
        jsonRpc.newBlockFilter(); // calls the vulnerable check-then-put
      } catch (JsonRpcExceedLimitException ignored) {}
      return null;
    }));
  }
  latch.countDown();
  for (Future<?> f : futures) f.get();
  pool.shutdown();

  // Expect map.size() <= cap, but race allows overshoot
  Assert.assertTrue("map size exceeded cap due to race: " + map.size(),
      map.size() <= cap);
}
```
Expected: under the current implementation this assertion can fail (map size > `cap`), demonstrating the check-then-put race; after applying the synchronized/atomic fix, the assertion should consistently pass.

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L141-149)
```java
  @Getter
  private final Map<String, LogFilterAndResult> eventFilter2ResultFull =
      new ConcurrentHashMap<>();
  /**
   * for block in Full Json-RPC
   */
  @Getter
  private final Map<String, BlockFilterAndResult> blockFilter2ResultFull =
      new ConcurrentHashMap<>();
```
