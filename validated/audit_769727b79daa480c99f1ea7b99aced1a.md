Found it: `PendingManager` resets the shielded-tx counter to `0` in its constructor (each block production/push cycle) and `Manager.pushTransaction` gates on this shared counter. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Shared, cheaply-fillable `shieldedTransInPendingCounts` counter allows anonymous griefing/DoS of all shielded transactions - (File: framework/src/main/java/org/tron/core/db/Manager.java)

### Summary
`Manager.pushTransaction` gates every incoming `ShieldedTransferContract` transaction on a single global `AtomicInteger` counter, `shieldedTransInPendingCounts`, compared against a small, network-wide constant `shieldedTransInPendingMaxCounts` (default `10`) [3](#0-2) . Any anonymous account can broadcast cheap, low-value shielded transactions to fill this shared slot; once full, every other user's shielded-transaction broadcast is silently rejected (returns `false`, no revert reason) until the pool is drained by block production. This mirrors the reported bug class: a cheap, front-runnable shared "status/slot" gate that legitimate users must pass through, which an attacker can occupy first to block others.

### Finding Description
`pushTransaction` checks and increments a single node-wide `AtomicInteger shieldedTransInPendingCounts` before admitting a shielded transaction into the pending pool: [1](#0-0) 

The counter is only reset to `0` when a new `PendingManager` is instantiated (at the start of `pushBlock`/block-production cycles): [2](#0-1) 

Because only a single shielded transaction is even allowed per produced block (`SHIELDED_TRANS_IN_BLOCK_COUNTS = 1`), the pending pool naturally accumulates unconfirmed shielded transactions between blocks. Any address (no special permission required) can submit shielded transactions with minimal transfer amounts — this is exactly the "cheap deposit/withdraw" griefing primitive described in the report: an attacker submits up to `shieldedTransInPendingMaxCounts` (10, by default, and configurable/node-specific) trivial shielded transactions to occupy the slot for an entire block-interval window, causing `pushTransaction` for a legitimate user's shielded transaction to return `false` (soft failure, no clear error surfaced to the caller) for the remainder of that window. This is reachable directly from an anonymous broadcast-transaction RPC call, with no privileged role required, matching the "front-run cheap deposit/withdraw to flip shared state and revert others" bug class from the report, applied here to a shared admission counter instead of a `status` enum.

### Impact Explanation
This is a low-cost, repeatable DoS specifically targeting the shielded (privacy) transaction pathway: legitimate users attempting to broadcast `ShieldedTransferContract` transactions can be persistently blocked from entering the mempool by a griefer continuously refilling the counter every time it's reset (each new block cycle), at the cost of only 10 trivial shielded transactions' worth of fees per block. Because `pushTransaction` returns `false` instead of throwing a descriptive validate/execute exception, wallets/RPC clients may not clearly surface why the broadcast failed, worsening user experience and effectively denying shielded transfer service network-wide.

### Likelihood Explanation
High. No privileged access, keys, or protocol-level exploit is needed — merely broadcasting inexpensive `ShieldedTransferContract` transactions repeatedly is sufficient, and the small default limit (`10`) makes the attack cheap to sustain continuously across blocks.

### Recommendation
Replace the coarse global admission counter with a fairness-aware mechanism: e.g., per-account (not global) shielded-tx pending limits, priority/first-come processing that can't be trivially crowded out by an attacker's own repeated cheap transactions, or increasing/removing the artificial cap in favor of standard resource/energy-fee-based throttling so that griefing costs scale with the number of transactions blocked rather than being fixed and cheap.

### Proof of Concept
1. Attacker repeatedly broadcasts minimal-value `ShieldedTransferContract` transactions (e.g., `fromAmount=1`) as fast as `PendingManager` resets the counter after each `pushBlock`/`generateBlock` cycle.
2. Each accepted shielded transaction increments `shieldedTransInPendingCounts` via `Manager.pushTransaction` [4](#0-3)  until it reaches `shieldedTransInPendingMaxCounts` (default 10).
3. Once the threshold is reached, any legitimate user's `ShieldedTransferContract` broadcast hits the same check and `pushTransaction` returns `false` silently [5](#0-4) , denying them mempool admission until the next `PendingManager` reset, at which point the attacker can immediately refill the slots again.

### Citations

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

**File:** framework/src/main/java/org/tron/core/db/PendingManager.java (L14-21)
```java
  private Manager dbManager;
  private long timeout = Args.getInstance().getPendingTransactionTimeout();

  public PendingManager(Manager db) {
    this.dbManager = db;
    db.getSession().reset();
    db.getShieldedTransInPendingCounts().set(0);
  }
```

**File:** common/src/main/resources/reference.conf (L367-369)
```text
  # Shielded transaction (ZK)
  zenTokenId = "000000"
  shieldedTransInPendingMaxCounts = 10 # Max shielded transactions in pending pool.
```
