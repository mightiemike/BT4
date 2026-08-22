### Title
Inefficient full-scan in `Manager.getTxByTid` (used by `getTxFromPending`) — `forEach` lambda `return` fails to short-circuit iteration - ([File: framework/src/main/java/org/tron/core/db/Manager.java])

### Summary
`Manager.getTxFromPending` (invoked from `GetTransactionFromPendingServlet` and gRPC `RpcApiService`) resolves the transaction ID via a helper that walks `pendingTransactions`/`popedTransactions`/`rePushTransactions` using `Collection.forEach(tx -> { ...; return; })`. Because `return` inside a lambda only exits that lambda invocation and not the enclosing `forEach` loop, the collection is always iterated to completion even after a match is found, so the call cost is always O(n) on the size of the pending pool rather than O(1)/early-exit as the code's structure suggests.

### Finding Description
The relevant pattern is:
```java
private TransactionCapsule getTxByTid(Collection<TransactionCapsule> pendingTransactions, String transactionId) {
  AtomicReference<TransactionCapsule> transactionCapsule = new AtomicReference<>();
  pendingTransactions.forEach(tx -> {
    if (tx.getTransactionId().toString().equals(transactionId)) {
      transactionCapsule.set(tx);
      return; // only exits this lambda invocation, NOT the forEach loop
    }
  });
  return transactionCapsule.get();
}
```
`Manager.getTxFromPending(String transactionId)` calls this helper against `pendingTransactions`, then `popedTransactions`, then `rePushTransactions`, and is reachable unauthenticated via `GetTransactionFromPendingServlet.doGet/doPost` [1](#0-0)  and via the equivalent gRPC entry point in `RpcApiService`. The only protection at the entry point is `RateLimiterServlet`'s QPS-based limiter [2](#0-1) , which throttles request *count*, not CPU time per request — so it does not compensate for a per-request cost that scales with mempool occupancy.

An unprivileged client can broadcast normal, fee-paying transactions to keep the local mempool near its configured maximum occupancy, then repeatedly call the pending-transaction lookup endpoint. Each call performs a full scan of the mempool collections regardless of where (or whether) the target transaction is found, because the `forEach`/`return` idiom never short-circuits.

### Impact Explanation
This is a real inefficiency (metering-faithfulness violation: cost is not O(1) as the "early exit" code intends, but always O(n) on pool size), which increases per-request CPU cost under attacker-influenced high mempool occupancy — a DoS-via-RPC-API amplification vector, TRON bounty class "DoS via RPC-API." However, the amplification is bounded: `n` is capped by the node's configured pending-transaction pool size limit, not attacker-controlled without bound, so this is a constant-factor CPU waste under worst-case load rather than an unbounded/exponential resource-exhaustion primitive. It does not by itself crash the node or cause state corruption; it degrades responsiveness of this specific query path when combined with a full mempool and repeated calls within the rate limiter's QPS budget.

### Likelihood Explanation
Low cost to attacker: broadcasting normal valid transactions to fill the mempool is standard network usage (they pay normal bandwidth/energy fees), and the query endpoint is public/unauthenticated. Feasibility is high (no privileged role or special config needed) but the severity is inherently capped by the mempool size ceiling, making this a minor/moderate performance issue rather than a critical DoS.

### Recommendation
Replace the `forEach`+mutable-reference pattern with a real loop that supports `break`, or use `Stream.filter(...).findFirst()`, so the search terminates as soon as a match is found:
```java
for (TransactionCapsule tx : pendingTransactions) {
  if (tx.getTransactionId().toString().equals(transactionId)) {
    return tx;
  }
}
return null;
```

### Proof of Concept
```java
@Test
public void testGetTxFromPendingNoEarlyExit() {
  // Populate manager.pendingTransactions with N transactions,
  // inserting the target transaction FIRST.
  int[] sizes = {100, 10000, 100000};
  long[] times = new long[sizes.length];
  for (int i = 0; i < sizes.length; i++) {
    manager.getPendingTransactions().clear();
    TransactionCapsule target = buildDummyTransaction();
    manager.getPendingTransactions().add(target); // inserted first
    for (int j = 1; j < sizes[i]; j++) {
      manager.getPendingTransactions().add(buildDummyTransaction());
    }
    long start = System.nanoTime();
    manager.getTxFromPending(target.getTransactionId().toString());
    times[i] = System.nanoTime() - start;
  }
  // Assert time scales ~linearly with N even though target is always first,
  // demonstrating no true early exit (forEach return does not break the loop).
  Assert.assertTrue(times[2] > times[0] * 500); // grows roughly with N, not O(1)
}
```
Expected result: lookup time for the first-inserted (best-case) transaction grows proportionally with the total pending pool size, confirming the `forEach`/`return` idiom does not short-circuit as intended by the surrounding `if`/`break`-style logic.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetTransactionFromPendingServlet.java (L14-20)
```java
@Component
@Slf4j(topic = "API")
public class GetTransactionFromPendingServlet extends RateLimiterServlet {

  @Autowired
  private Manager manager;

```

**File:** framework/src/main/java/org/tron/core/services/http/GetTransactionFromPendingServlet.java (L21-34)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      String input = request.getParameter("value");
      TransactionCapsule reply = manager.getTxFromPending(input);
      if (reply != null) {
        response.getWriter().println(Util.printTransaction(reply.getInstance(), visible));
      } else {
        response.getWriter().println("{}");
      }
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```
