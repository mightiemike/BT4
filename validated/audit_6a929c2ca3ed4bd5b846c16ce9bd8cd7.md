### Title
Unbounded per-block `LinkedBlockingQueue` growth in solidity contract trigger maps enables RPC-node memory-exhaustion DoS - ([File: framework/src/main/java/org/tron/common/logsfilter/capsule/ContractTriggerCapsule.java])

### Summary
`ContractTriggerCapsule.processTrigger()` inserts every emitted contract event/log into `Args.getSolidityContractEventTriggerMap()` / `Args.getSolidityContractLogTriggerMap()` via `computeIfAbsent(blockNumber, () -> new LinkedBlockingQueue())`, and `new LinkedBlockingQueue()` with no capacity argument is unbounded (effectively `Integer.MAX_VALUE`). An attacker who can get many LOG-emitting transactions into a single block can grow the per-block queue essentially without limit until the block is processed and `clearSolidityContractTriggerCache` runs.

### Finding Description
In [1](#0-0)  and the analogous log branches at [2](#0-1)  and [3](#0-2) , every qualifying contract event/log triggers `computeIfAbsent(event.getBlockNumber(), listBlk -> new LinkedBlockingQueue())`. Because `LinkedBlockingQueue()`'s no-arg constructor defaults capacity to `Integer.MAX_VALUE`, `offer()` will essentially always succeed and the queue can grow without a practical bound for the duration of one block's processing, only being reclaimed later via `clearSolidityContractTriggerCache` in `Manager.java`.

The attack path is reachable from an unprivileged actor: deploy/invoke a contract that emits the maximum number of `LOG` opcodes affordable within one transaction's energy budget, and broadcast many such transactions to fill a block. Each transaction executes normally, pays its energy/bandwidth fee, and passes all standard checks (`TransactionCapsule.validateSignature`, actuator `validate()`, energy accounting) — none of those checks are designed to limit trigger-queue memory, since they only govern transaction execution cost, not event-plugin memory bookkeeping.

### Impact Explanation
This maps to a DoS via the TRON protocol/RPC-serving implementation: heap growth in `Args`'s solidity trigger maps on any node with the event/log plugin trigger enabled can cause memory pressure, GC thrashing, or process crash on the affected full/RPC node, degrading its ability to serve RPC/event-indexing requests (e.g., TronGrid-style event indexers). This is a node-level availability impact rather than a consensus or asset-accounting issue.

### Likelihood Explanation
The precondition is that the node operator has explicitly enabled the solidity event/log trigger plugin (`isSolidityEventTriggerEnable()` / `isSolidityLogTriggerEnable()`), which is **not the default configuration** for a generic java-tron node — it requires an event-plugin config block to be added to `config.conf`. It is, however, a realistic and common configuration for public RPC-serving/event-indexing nodes. Given that precondition is met, the attack is cheap and fully repeatable: the attacker only pays normal transaction fees/energy for LOG-emitting contract calls, requires no privileged role, and can repeat the attack every block indefinitely.

### Recommendation
Bound the per-block queues (e.g., `new LinkedBlockingQueue<>(MAX_TRIGGER_QUEUE_SIZE)`) so `offer()` naturally rejects excess triggers instead of relying on unbounded growth, and/or impose a global cap on total pending trigger entries across all block-number buckets in `Args`'s maps, evicting/logging when the cap is reached. Additionally, consider capping the number of triggers processed per transaction/block rather than only bounding memory reactively.

### Proof of Concept
```java
// Conceptual PoC (framework/src/test/java/org/tron/common/logsfilter/capsule/ContractTriggerCapsuleTest.java style)
// 1. Enable EventPluginLoader.getInstance().isSolidityEventTriggerEnable() (or LogTrigger) as done in tests.
// 2. Deploy a contract whose fallback emits the maximum number of `LOG3`/`LOG4` events
//    affordable within one transaction's energy limit.
// 3. Broadcast N such transactions (N large) all landing in the same block number.
// 4. For each transaction, invoke ContractTriggerCapsule.processTrigger() as production code does
//    per LogInfo entry produced by the VM.
// 5. Before Manager.clearSolidityContractTriggerCache(blockNum) runs (i.e., before next block),
//    assert Args.getSolidityContractEventTriggerMap().get(blockNum).size() grows proportionally
//    with N * eventsPerTx, with no queue-capacity based rejection, demonstrating unbounded heap growth.
``` [4](#0-3)

### Citations

**File:** framework/src/main/java/org/tron/common/logsfilter/capsule/ContractTriggerCapsule.java (L138-147)
```java
        if (EventPluginLoader.getInstance().isSolidityEventTriggerEnable()
            && !contractTrigger.isRemoved()) {
          boolean result = Args.getSolidityContractEventTriggerMap().computeIfAbsent(event
              .getBlockNumber(), listBlk -> new LinkedBlockingQueue())
                  .offer((ContractEventTrigger) event);
          if (!result) {
            logger.info("too many triggers, solidity event trigger lost: {}",
                event.getUniqueId());
          }
        }
```

**File:** framework/src/main/java/org/tron/common/logsfilter/capsule/ContractTriggerCapsule.java (L162-171)
```java
          if (EventPluginLoader.getInstance().isSolidityLogTriggerRedundancy()
              && !contractTrigger.isRemoved()) {
            boolean result = Args.getSolidityContractLogTriggerMap().computeIfAbsent(event
                .getBlockNumber(), listBlk -> new LinkedBlockingQueue())
                .offer(logTrigger);
            if (!result) {
              logger.info("too many triggers, solidity log trigger lost: {}",
                  logTrigger.getUniqueId());
            }
          }
```

**File:** framework/src/main/java/org/tron/common/logsfilter/capsule/ContractTriggerCapsule.java (L178-187)
```java
        if (EventPluginLoader.getInstance().isSolidityLogTriggerEnable()
            && !contractTrigger.isRemoved()) {
          boolean result = Args.getSolidityContractLogTriggerMap().computeIfAbsent(event
              .getBlockNumber(), listBlk -> new LinkedBlockingQueue())
                  .offer((ContractLogTrigger) event);
          if (!result) {
            logger.info("too many triggers, solidity log trigger lost: {}",
                event.getUniqueId());
          }
        }
```
