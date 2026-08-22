### Title
Griefing DoS on Shielded-Transaction Broadcast via Shared Global Pending Counter - (File: framework/src/main/java/org/tron/core/db/Manager.java)

### Summary
`Manager.pushTransaction()` gates every broadcast of a `ShieldedTransferContract` transaction behind a single global counter, `shieldedTransInPendingCounts`, compared against the config value `shieldedTransInPendingMaxCounts` (default 10). Any anonymous account able to submit valid shielded transactions can push enough of them to saturate this shared counter, causing `pushTransaction()` to reject **every other user's** shielded transaction broadcasts until the counter is reset at the next block cycle — mirroring the reported PearVault pattern where one user inflates a shared pending-state variable to block direct operations for everyone else.

### Finding Description
In `Manager.pushTransaction()`, shielded transactions are checked against a global, not per-account, threshold: [1](#0-0) 

The check `shieldedTransInPendingCounts.get() >= shieldedTransInPendingMaxCounts` returns `false` (broadcast rejected) for *any* caller once the global count is reached — there is no per-account fairness or balance/liquidity check equivalent to what the PearVault fix added. The counter field and its config-driven cap are declared here: [2](#0-1) [3](#0-2) 

The counter is only reset to zero when a new `PendingManager` is constructed (i.e., at the start of each block-generation/pending cycle): [4](#0-3) 

However, block packing only allows a single shielded transaction per block (`SHIELDED_TRANS_IN_BLOCK_COUNTS = 1`): [5](#0-4) [6](#0-5) 

This creates the same structural flaw as the PearVault bug: a single actor can front-run/spam a cheap, unprivileged action (broadcasting `shieldedTransInPendingMaxCounts` shielded transactions) that inflates a global pending-state counter, and that counter alone (not actual capacity, since only 1 per block is packed) gates whether *any other* user's transaction of that type is accepted at all.

### Impact Explanation
Once the attacker fills the counter (default cap 10), all legitimate shielded-transaction broadcasts from any other account are silently rejected (`pushTransaction` returns `false`) until the node's pending set is flushed at the next block-generation cycle. Since only one shielded transaction is actually included per block, the attacker can keep resubmitting cheap shielded transactions each cycle to perpetually deny shielded-transaction service to the rest of the network — a persistent availability/DoS impact on a specific RPC/broadcast-transaction feature, reachable by any anonymous node/user without special privilege.

### Likelihood Explanation
Any account capable of constructing a valid (fee-paying) shielded transaction — a normal user capability, not a privileged role — can trigger this repeatedly and cheaply. No malicious peer, leaked key, or admin privilege is required; only ordinary broadcast of transactions via the wallet/RPC API is needed, matching the "unprivileged, reachable from a broadcast transaction" criteria.

### Recommendation
Replace the coarse global admission check with logic that reflects actual per-block capacity and/or per-account fairness (e.g., track and cap pending shielded transactions per-account, or size the pending admission window to the number that can actually be packed per block rather than an independently configured `shieldedTransInPendingMaxCounts`), analogous to how the PearVault fix added a real capacity/liquidity check to `requestWithdrawal()` before allowing state that blocks others.

### Proof of Concept
1. Attacker crafts and broadcasts `shieldedTransInPendingMaxCounts` (default 10) valid `ShieldedTransferContract` transactions in rapid succession within one pending cycle.
2. Each successful push increments `shieldedTransInPendingCounts` in `Manager.pushTransaction()` [7](#0-6) .
3. Once the counter reaches the cap, any other user's shielded transaction broadcast hits the guard and is rejected (`return false`) even though the node has ample capacity to validate/store it [8](#0-7) .
4. Because block packing only includes 1 shielded transaction per block [6](#0-5) , the attacker can resubmit before/at each new pending cycle, keeping the counter saturated and denying shielded transaction service to all other users indefinitely.

### Citations

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L185-192)
```java
  private static final int SHIELDED_TRANS_IN_BLOCK_COUNTS = 1;
  private static final String SAVE_BLOCK = "Save block: {}";
  private static final int SLEEP_TIME_OUT = 50;
  private static final int TX_ID_CACHE_SIZE = 100_000;
  private static final int SLEEP_FOR_WAIT_LOCK = 10;
  private static final int NO_BLOCK_WAITING_LOCK = 0;
  private final int shieldedTransInPendingMaxCounts =
      Args.getInstance().getShieldedTransInPendingMaxCounts();
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L245-247)
```java
  private BlockingQueue<TransactionCapsule> pendingTransactions;
  @Getter
  private AtomicInteger shieldedTransInPendingCounts = new AtomicInteger(0);
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L924-943)
```java
        synchronized (this) {
          if (isShieldedTransaction(trx.getInstance())
                  && shieldedTransInPendingCounts.get() >= shieldedTransInPendingMaxCounts) {
            return false;
          }
          if (!session.valid()) {
            session.setValue(revokingStore.buildSession());
          }

          try (ISession tmpSession = revokingStore.buildSession()) {
            processTransaction(trx, null);
            trx.setTrxTrace(null);
            pendingTransactions.add(trx);
            Metrics.gaugeInc(MetricKeys.Gauge.MANAGER_QUEUE, 1,
                    MetricLabels.Gauge.QUEUE_PENDING);
            tmpSession.merge();
          }
          if (isShieldedTransaction(trx.getInstance())) {
            shieldedTransInPendingCounts.incrementAndGet();
          }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1715-1720)
```java
      //shielded transaction
      Transaction transaction = trx.getInstance();
      if (isShieldedTransaction(transaction)
          && shieldedTransCounts.incrementAndGet() > SHIELDED_TRANS_IN_BLOCK_COUNTS) {
        continue;
      }
```

**File:** framework/src/main/java/org/tron/core/db/PendingManager.java (L17-21)
```java
  public PendingManager(Manager db) {
    this.dbManager = db;
    db.getSession().reset();
    db.getShieldedTransInPendingCounts().set(0);
  }
```
