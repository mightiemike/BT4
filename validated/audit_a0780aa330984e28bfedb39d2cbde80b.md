### Title
Unweighted per-block-count rate limiting on `/wallet/getblockbylimitnext` allows CPU/memory amplification via full-block deserialization and JSON re-serialization - ([File: framework/src/main/java/org/tron/core/services/http/GetBlockByLimitNextServlet.java])

### Finding Description
`GetBlockByLimitNextServlet.fillResponse` only checks that `endNum - startNum <= BLOCK_LIMIT_NUM` (100) before calling `wallet.getBlocksByLimitNext(startNum, endNum - startNum)`, with no additional cost accounting based on block size or transaction count. [1](#0-0) 

That call flows to `BlockStore.getLimitNumber`, which fetches up to 100 raw block byte arrays from `revokingDB` and deserializes each into a full `BlockCapsule` (including all transactions) via `pack`. [2](#0-1) 

The resulting `BlockList` is then re-serialized to JSON via `Util.printBlockList`, which walks every transaction of every block in the response. Because rate limiting (`QpsStrategy`/`IPQpsStrategy`/`DefaultBaseQqsAdapter`) is applied per-endpoint as a flat token count and no per-endpoint override for this servlet appears in `reference.conf`, the endpoint is throttled purely by request count, not by the amount of block/transaction data actually processed. An attacker can therefore always request the maximum allowed window (100 blocks) per call, each of which forces full deserialization of every transaction in those blocks and a full JSON re-serialization, while consuming the same single rate-limit token as a cheap 1-block or 0-transaction-block request.

### Impact Explanation
Repeated calls with `startNum=0`, `endNum=100` (or any 100-block window over historic blocks with many/large transactions) force the node to repeatedly deserialize and JSON-encode potentially megabytes of transaction data per request, at the standard per-endpoint QPS budget. This allows disproportionate CPU and memory consumption relative to the flat per-request rate-limit cost, degrading node responsiveness for legitimate API consumers under sustained request volume.

### Likelihood Explanation
No authentication or special privilege is required — this is a public HTTP JSON-RPC-style endpoint reachable by any unprivileged client. The attack requires only an unauthenticated node with populated block history containing many/large transactions, which is the normal state of any production TRON full node. The 100-block cap bounds the worst case somewhat, but does not scale the rate-limit cost to actual block/transaction size, so the attack is fully repeatable at the endpoint's configured QPS.

### Recommendation
Introduce cost-weighted rate limiting for `/wallet/getblockbylimitnext` (and its solidity/PBFT variants) based on the requested block count and/or actual transaction count/byte size returned, rather than a flat per-request token; alternatively, lower `BLOCK_LIMIT_NUM` and add a maximum aggregate transaction-count/byte-size cap enforced in `fillResponse` before calling `wallet.getBlocksByLimitNext`.

### Proof of Concept
Integration test plan:
1. Populate a test chain (e.g. via `WalletMockTest`/`WalletTest` fixtures) with 100 consecutive blocks, each containing the maximum allowed number of large transactions.
2. Issue repeated POST requests to `/wallet/getblockbylimitnext` with `startNum=0`, `endNum=100` at the configured default QPS (1000) using `RateLimiterServletTest`-style harness.
3. Measure wall-clock time and heap allocation per request via JMH or `-Xss`/GC instrumentation.
4. Assert that per-request cost (CPU time, allocated bytes) scales linearly with total transaction count/bytes in the 100-block window, while the rate limiter (`QpsStrategy`) always accounts it as a single token — demonstrating the mismatch between real cost and rate-limit weight.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetBlockByLimitNextServlet.java (L42-53)
```java
  private void fillResponse(boolean visible, long startNum, long endNum,
      HttpServletResponse response)
      throws IOException {
    if (endNum > 0 && endNum > startNum && endNum - startNum <= BLOCK_LIMIT_NUM) {
      BlockList reply = wallet.getBlocksByLimitNext(startNum, endNum - startNum);
      if (reply != null) {
        response.getWriter().println(Util.printBlockList(reply, visible));
        return;
      }
    }
    response.getWriter().println("{}");
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/BlockStore.java (L41-62)
```java
  public List<BlockCapsule> getLimitNumber(long startNumber, long limit) {
    BlockId startBlockId = new BlockId(Sha256Hash.ZERO_HASH, startNumber);
    return pack(revokingDB.getValuesNext(startBlockId.getBytes(), limit));
  }

  public List<BlockCapsule> getBlockByLatestNum(long getNum) {
    return pack(revokingDB.getlatestValues(getNum));
  }

  private List<BlockCapsule> pack(Set<byte[]> values) {
    List<BlockCapsule> blocks = new ArrayList<>();
    for (byte[] bytes : values) {
      try {
        blocks.add(new BlockCapsule(bytes));
      } catch (BadItemException e) {
        logger.error("Find bad item: {}", e.getMessage());
        // throw new TronDBException(e);
      }
    }
    blocks.sort(Comparator.comparing(BlockCapsule::getNum));
    return blocks;
  }
```
