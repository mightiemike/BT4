### Title
Unbounded per-call linear rehash scan in `eth_getTransactionByHash` - ([File: TronJsonRpcImpl.java])

### Finding Description
`TronJsonRpcImpl.getTransactionByHash` resolves the transaction's block via `wallet.getTransactionInfoById`/`wallet.getBlockByNum` and then calls `formatTransactionResult(transactionInfo, block)` [1](#0-0) . `formatTransactionResult` does not use the transaction index stored anywhere; instead it linearly iterates the block's full transaction list and, for every entry, recomputes the transaction ID by SHA256-hashing `transaction.getRawData().toByteArray()` via `getTxID`, until it finds a matching hash [2](#0-1) . `getTxID` performs a fresh `Sha256Hash.hash(...)` computation on the raw transaction bytes on every invocation, with no caching/memoization [3](#0-2) . The identical linear-scan pattern also exists in `getTransactionByHash`'s fallback branch via `getTransactionIndex` [4](#0-3) .

This is fully reachable by an unprivileged caller: `eth_getTransactionByHash` is a public JSON-RPC method, requiring only a valid, previously-known transaction hash (which is public information for any confirmed transaction). By choosing a historical transaction placed at the highest index of a maximally-filled block (blocks are capped at 2,000,000 bytes per `Parameter.ChainConstant.BLOCK_SIZE`, allowing potentially thousands of small transactions per block) [5](#0-4) , the caller forces the server to scan and rehash every prior transaction in the block on every single call. There is no per-call cost accounting, caching, or index-based lookup — the JSON-RPC servlet path only applies a generic rate limiter, not one scaled to computed cost.

### Impact Explanation
Every repeated call for the same worst-case transaction re-executes the full O(N) SHA256 rehash of the block, with N bounded only by how many transactions fit in a 2MB block. This allows an attacker to cheaply and repeatedly trigger disproportionate CPU work on the full node compared to trivial cost/effort on their side, which can be amplified at high QPS to degrade JSON-RPC service responsiveness for legitimate users (CPU exhaustion / degraded throughput on the public read API).

### Likelihood Explanation
The precondition (a historical block with many transactions, target transaction placed last) is trivially satisfiable — any node operator/attacker only needs to observe a real, already-produced block with near-max transaction count and note the hash of its last transaction; no privileged access or protocol manipulation is required. The call itself, `eth_getTransactionByHash(txId)`, is a standard unauthenticated JSON-RPC call repeatable at arbitrary QPS subject only to generic connection/servlet-level rate limits, not cost-based ones.

### Recommendation
Avoid the O(N) rehash scan on the hot read path:
- Persist/return the transaction's index directly from `TransactionInfo` (or a dedicated index store) instead of recomputing it by linear scan and rehashing every transaction.
- If index must be derived from the block, cache the computed `txId -> index` map per block (e.g., LRU cache keyed by block number) so repeated queries against the same block do not redo the hashing work.
- Apply cost-aware rate limiting or per-request work budgets to `eth_getTransactionByHash`-style handlers so no single JSON-RPC call can trigger unbounded server-side computation.

### Proof of Concept
Java benchmark/integration test plan:
1. Build a `BlockCapsule`/`Block` with `N` transactions (e.g., N = maximum transactions that fit under `ChainConstant.BLOCK_SIZE`), and matching `TransactionInfo` entries persisted via test store setup as in `JsonrpcServiceTest` [6](#0-5) .
2. Call `tronJsonRpc.getTransactionByHash(txIdAtIndex0)` and measure wall-clock latency; repeat for `txIdAtIndex(N-1)`.
3. Assert that latency for the last-indexed transaction scales linearly with N (e.g., `latency(N-1) >> latency(0)` and grows with N), demonstrating unbounded, caller-selectable server cost.
4. As a regression guard after the fix, assert that latency for index 0 and index N-1 stay within a small bounded ratio (e.g., < 2x) regardless of index, or that a second identical call for the same tx/block is served from cache with near O(1) latency.

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L782-789)
```java
    } else {
      Block block = wallet.getBlockByNum(transactionInfo.getBlockNumber());
      if (block == null) {
        return null;
      }

      return formatTransactionResult(transactionInfo, block);
    }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L792-815)
```java
  private TransactionResult formatTransactionResult(TransactionInfo transactioninfo, Block block) {
    String txId = ByteArray.toHexString(transactioninfo.getId().toByteArray());

    Transaction transaction = null;
    int transactionIndex = -1;

    List<Transaction> txList = block.getTransactionsList();
    for (int index = 0; index < txList.size(); index++) {
      transaction = txList.get(index);
      if (getTxID(transaction).equals(txId)) {
        transactionIndex = index;
        break;
      }
    }

    if (transactionIndex == -1) {
      return null;
    }

    long energyUsageTotal = transactioninfo.getReceipt().getEnergyUsageTotal();
    BlockCapsule blockCapsule = new BlockCapsule(block);
    return new TransactionResult(blockCapsule, transactionIndex, transaction,
        energyUsageTotal, wallet.getEnergyFee(blockCapsule.getTimeStamp()), wallet);
  }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java (L217-219)
```java
  public static String getTxID(Transaction transaction) {
    return ByteArray.toHexString(Sha256Hash.hash(true, transaction.getRawData().toByteArray()));
  }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java (L588-601)
```java
  public static int getTransactionIndex(String txId, List<Transaction> txList) {
    int transactionIndex = -1;
    Transaction transaction;

    for (int index = 0; index < txList.size(); index++) {
      transaction = txList.get(index);
      if (getTxID(transaction).equals(txId)) {
        transactionIndex = index;
        break;
      }
    }

    return transactionIndex;
  }
```

**File:** common/src/main/java/org/tron/core/config/Parameter.java (L73-73)
```java
    public static final int BLOCK_SIZE = 2_000_000;
```

**File:** framework/src/test/java/org/tron/core/jsonrpc/JsonrpcServiceTest.java (L393-412)
```java
  @Test
  public void testGetTransactionByHash() {
    TransactionResult transactionResult = null;
    try {
      transactionResult = tronJsonRpc.getTransactionByHash(
          "0x1111111111111111111111111111111111111111111111111111111111111111");
    } catch (Exception e) {
      Assert.fail();
    }
    Assert.assertNull(transactionResult);

    try {
      transactionResult = tronJsonRpc.getTransactionByHash(
          ByteArray.toJsonHex(transactionCapsule1.getTransactionId().getBytes()));
    } catch (Exception e) {
      Assert.fail();
    }
    Assert.assertEquals(ByteArray.toJsonHex(transactionCapsule1.getBlockNum()),
        transactionResult.getBlockNumber());
  }
```
