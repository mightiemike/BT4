### Title
Incomplete lite-fullnode gRPC filter allows unfiltered historical query via `GetTransactionInfoByBlockNum` - ([File: framework/src/main/java/org/tron/core/services/filter/LiteFnQueryGrpcInterceptor.java])

### Summary
`LiteFnQueryGrpcInterceptor` blocks a hardcoded, manually maintained set of gRPC method names (`filterMethods`) when the node runs as a lite fullnode with `openHistoryQueryWhenLiteFN=false`. `Wallet.GetTransactionInfoByBlockNum` / `WalletSolidity.GetTransactionInfoByBlockNum`, which iterates over all transactions in a historical block to fetch their `TransactionInfo` (data typically pruned/unavailable on a lite node), is not present in this set even though the semantically equivalent `GetTransactionInfoById` is filtered.

### Finding Description
`LiteFnQueryGrpcInterceptor.interceptCall` performs access control purely via string matching against a static allowlist populated in a `static {}` block: [1](#0-0) 

The list explicitly filters `protocol.Wallet/GetTransactionInfoById` and `protocol.WalletSolidity/GetTransactionInfoById`: [2](#0-1) 

but does not include `GetTransactionInfoByBlockNum`, which is a distinct RPC defined in `api.proto` and implemented in `Wallet.java` / `Manager.java` / `RpcApiService.java`, backed by the same `TransactionRetStore`/history lookup path that `GetTransactionInfoById` uses. Because the interceptor's block condition is `filterMethods.contains(fullMethodName)` with no semantic/whitelist-based grouping (e.g. by request type "historical block query"), any RPC that was added or renamed without a corresponding manual entry in this set silently bypasses the lite-node protection and falls through to `next.startCall(call, headers)`, reaching the full handler and its underlying store lookups regardless of `openHistoryQueryWhenLiteFN`.

An anonymous, unauthenticated gRPC client can call this endpoint directly — no signed transaction, fee, or on-chain state change is required, since it is a read-only query RPC not gated by transaction validation, permission checks, or energy/bandwidth accounting.

### Impact Explanation
On a lite fullnode (an increasingly common deployment mode to save disk I/O by pruning historical trie/data), this omission lets any anonymous caller force the node to perform heavy/uncontrolled I/O against pruned or degraded historical stores for a query the operator intentionally disabled via `openHistoryQueryWhenLiteFN=false`. Repeated calls against arbitrary/large block numbers can be used to induce disk thrashing, exceptions, or resource exhaustion, corresponding to a DoS via RPC-API class of impact. There is no accounting cost to the caller (gRPC queries are not metered like transactions), so the attack is essentially free.

### Likelihood Explanation
Preconditions are limited to the node being configured as lite fullnode with default `openHistoryQueryWhenLiteFN=false` (a legitimate, commonly recommended lite-node configuration, not a misconfiguration). The attacker needs only network access to the node's gRPC endpoint — no privileged role, keys, or fees. The exploit is trivially repeatable (loop over block numbers) and requires no special crafting beyond a normal `GetTransactionInfoByBlockNum` request.

### Recommendation
Replace the manually maintained, method-name-based blocklist with a systematic mechanism, e.g.:
- Derive the filtered-method set from a shared annotation/marker on RPC handler implementations that access historical/pruned stores, so newly added or renamed heavy history RPCs are filtered by construction rather than by manual enumeration.
- Alternatively, add a build-time or unit test that enumerates all methods of the `Wallet`/`WalletSolidity` gRPC service descriptors and asserts each history-dependent method (based on the underlying store used) is present in `LiteFnQueryGrpcInterceptor.filterMethods`.
- Immediately add `protocol.Wallet/GetTransactionInfoByBlockNum` and `protocol.WalletSolidity/GetTransactionInfoByBlockNum` to `filterMethods`, since it shares the same historical/pruning-dependent path as `GetTransactionInfoById`.

### Proof of Concept
```java
// JUnit test demonstrating the gap in LiteFnQueryGrpcInterceptor.filterMethods
import org.junit.Test;
import org.tron.core.services.filter.LiteFnQueryGrpcInterceptor;
import java.util.Set;
import static org.junit.Assert.assertTrue;

public class LiteFnFilterGapTest {
  @Test
  public void testGetTransactionInfoByBlockNumIsFiltered() {
    Set<String> filtered = LiteFnQueryGrpcInterceptor.getFilterMethods();
    // Same category as GetTransactionInfoById (already filtered), yet missing:
    assertTrue("Wallet/GetTransactionInfoByBlockNum should be filtered on lite nodes",
        filtered.contains("protocol.Wallet/GetTransactionInfoByBlockNum"));
    assertTrue("WalletSolidity/GetTransactionInfoByBlockNum should be filtered on lite nodes",
        filtered.contains("protocol.WalletSolidity/GetTransactionInfoByBlockNum"));
  }
}
```
Expected result on the current codebase: both assertions fail, confirming that an unauthenticated gRPC client can call `GetTransactionInfoByBlockNum` against a lite fullnode configured with `openHistoryQueryWhenLiteFN=false` and reach the historical-lookup handler despite the intended filter.

### Citations

**File:** framework/src/main/java/org/tron/core/services/filter/LiteFnQueryGrpcInterceptor.java (L38-61)
```java
    filterMethods.add("protocol.Wallet/GetTransactionById");
    filterMethods.add("protocol.Wallet/GetTransactionCountByBlockNum");
    filterMethods.add("protocol.Wallet/GetTransactionInfoById");
    filterMethods.add("protocol.Wallet/IsSpend");
    filterMethods.add("protocol.Wallet/ScanAndMarkNoteByIvk");
    filterMethods.add("protocol.Wallet/ScanNoteByIvk");
    filterMethods.add("protocol.Wallet/ScanNoteByOvk");
    filterMethods.add("protocol.Wallet/TotalTransaction");
    filterMethods.add("protocol.Wallet/GetMarketOrderByAccount");
    filterMethods.add("protocol.Wallet/GetMarketOrderById");
    filterMethods.add("protocol.Wallet/GetMarketPriceByPair");
    filterMethods.add("protocol.Wallet/GetMarketOrderListByPair");
    filterMethods.add("protocol.Wallet/GetMarketPairList");
    filterMethods.add("protocol.Wallet/ScanShieldedTRC20NotesByIvk");
    filterMethods.add("protocol.Wallet/ScanShieldedTRC20NotesByOvk");
    filterMethods.add("protocol.Wallet/IsShieldedTRC20ContractNoteSpent");

    // walletSolidity
    filterMethods.add("protocol.WalletSolidity/GetBlockByNum");
    filterMethods.add("protocol.WalletSolidity/GetBlockByNum2");
    filterMethods.add("protocol.WalletSolidity/GetMerkleTreeVoucherInfo");
    filterMethods.add("protocol.WalletSolidity/GetTransactionById");
    filterMethods.add("protocol.WalletSolidity/GetTransactionCountByBlockNum");
    filterMethods.add("protocol.WalletSolidity/GetTransactionInfoById");
```

**File:** framework/src/main/java/org/tron/core/services/filter/LiteFnQueryGrpcInterceptor.java (L79-91)
```java
  @Override
  public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(ServerCall<ReqT, RespT> call,
      Metadata headers, ServerCallHandler<ReqT, RespT> next) {
    if (chainBaseManager.isLiteNode()
            && !CommonParameter.getInstance().openHistoryQueryWhenLiteFN
            && filterMethods.contains(call.getMethodDescriptor().getFullMethodName())) {
      call.close(Status.UNAVAILABLE
              .withDescription("this API is closed because this node is a lite fullnode"), headers);
      return new ServerCall.Listener<ReqT>() {};
    } else {
      return next.startCall(call, headers);
    }
  }
```
