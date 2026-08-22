## Analysis Result

### Title
Unbounded historical event replay from a fixed `startSyncBlockNum` re-processes all chain history on every node restart, causing prolonged event-service unavailability - (File: `framework/src/main/java/org/tron/core/services/event/HistoryEventService.java`)

### Summary
When `event.subscribe.version = 1` and `startSyncBlockNum` is configured, `HistoryEventService` replays every block from `startSyncBlockNum` up to the current solidified head on every service start (including every node restart), without ever persisting how far the replay has progressed. Because the amount of work scales with the number of blocks/transactions/contract logs that anonymous users have broadcast to the chain since `startSyncBlockNum`, this is directly analogous to the reported bug class: services that "replay all events from the beginning of the history" on startup without persisting offset progress.

### Finding Description
`HistoryEventService.init()` checks `instance.getStartSyncBlockNum()`; if it is > 0, it spawns a dedicated thread that runs `syncEvent()`: [1](#0-0) 

`syncEvent()` loops from the configured `startSyncBlockNum` to `getLatestSolidifiedBlockNum()`, fetching and processing every single block via `blockEventGet.getBlockEvent(tmp)` before incrementing `tmp`: [2](#0-1) 

Critically, `startSyncBlockNum` is read purely from static configuration (`event.subscribe.startSyncBlockNum`) and is never advanced or persisted by the code itself — the configuration comment explicitly instructs operators to manually reset it after a full sync: [3](#0-2) 

This means that on every restart of a node with event subscription enabled (a common configuration for exchanges/explorers that need `contractevent`/`contractlog`/`solidityevent` feeds), the service resumes the replay from the same fixed starting point rather than from where it left off, forcing a full re-walk of all blocks since that point.

Each block replayed is not cheap: `BlockEventGet.getBlockEvent()` reads the full block, retrieves `TransactionInfo` for every transaction, parses all contract logs, resolves contract ABIs from the `ContractStore`/`AbiStore`, and matches ABI event signatures for every log entry: [4](#0-3) 

Because the range `[startSyncBlockNum, latestSolidifiedBlockNum)` grows purely as a function of ordinary chain activity (transactions and contract-log-producing calls broadcast by any anonymous actor), an attacker can cheaply and continuously inflate the amount of work this replay must perform on every future restart simply by broadcasting many transactions/contract calls with numerous logs, with no special privileges required.

### Impact Explanation
For any node operator that has enabled `event.subscribe` with `version = 1` and a static `startSyncBlockNum` (a supported, documented, non-default configuration used to deliver on-chain event feeds to downstream consumers such as exchanges/explorers), every restart — whether from a routine upgrade, OOM, or any other crash — forces a full, unbounded, single-threaded re-walk of chain history from that fixed point. During this replay the event-subscription pipeline (`RealtimeEventService`/`SolidEventService` flush paths) is effectively backlogged, delaying delivery of real-time events, and the workload (disk I/O for `TransactionInfo` per block, ABI-based log decoding per event) is directly proportional to attacker-inflatable on-chain activity. This is a denial-of-service risk against the event-subscription functionality of any exchange/monitoring node that uses this feature, analogous to the audited external report.

### Likelihood Explanation
Likelihood is moderate: it requires the operator to have opted into `event.subscribe.version = 1` with `startSyncBlockNum` set (not the default `version = 0`), but this is a documented, supported configuration path aimed exactly at production consumers of chain events. Given that configuration, the attacker-controllable amplification (broadcasting many transactions/contract-log-heavy calls) requires no special privileges — any anonymous account able to broadcast transactions and deploy/call contracts that emit logs can grow the replay cost for as long as the node operator leaves `startSyncBlockNum` unchanged (which the config comments imply is a manual, easy-to-forget step).

### Recommendation
**Short term:** Persist the replay progress (the last successfully processed block number) atomically with any built state, and resume `syncEvent()` from the persisted offset rather than the static `startSyncBlockNum` config value on every restart. Bound per-restart replay work (e.g., a `MAX_LOAD_NUM`-style cap similar to `BlockEventLoad`) so that catch-up happens incrementally rather than in one unbounded loop.

**Long term:** Add integration tests verifying that: (1) `HistoryEventService` resumes from the last processed block, not from the configured `startSyncBlockNum`, on restart; and (2) event-service startup/catch-up time remains bounded and does not scale unboundedly with total chain length or accumulated contract-log volume.

### Proof of Concept
1. Configure a node with `event.subscribe.enable = true`, `event.subscribe.version = 1`, `event.subscribe.startSyncBlockNum = 1`, and enable `contractevent`/`contractlog` triggers.
2. As an anonymous actor, broadcast a large volume of transactions/contract calls that emit many logs over time to grow chain height and total log volume (all via standard `broadcastTransaction`/contract-call RPCs, no special privileges needed).
3. Restart the node (e.g., trigger a crash, or wait for a routine restart).
4. Observe `HistoryEventService.syncEvent()` re-processing all blocks from block 1 through the current solidified head via `BlockEventGet.getBlockEvent()` — see `framework/src/main/java/org/tron/core/services/event/HistoryEventService.java#L62-L83` and `framework/src/main/java/org/tron/core/services/event/BlockEventGet.java#L56-L94` — with per-restart cost growing with attacker-inflated chain activity, since `startSyncBlockNum` was never advanced/persisted (`framework/src/main/resources/config.conf#L448-L451`).

### Citations

**File:** framework/src/main/java/org/tron/core/services/event/HistoryEventService.java (L36-46)
```java
  public void init() {
    if (instance.getStartSyncBlockNum() <= 0) {
      initEventService(manager.getChainBaseManager().getHeadBlockId());
      return;
    }

    thread = new Thread(() -> syncEvent(), "history-event");
    thread.start();

    logger.info("History event service start.");
  }
```

**File:** framework/src/main/java/org/tron/core/services/event/HistoryEventService.java (L62-83)
```java
  private void syncEvent() {
    try {
      long tmp = instance.getStartSyncBlockNum();
      long endNum = manager.getDynamicPropertiesStore().getLatestSolidifiedBlockNum();
      while (tmp < endNum) {
        if (thread.isInterrupted() || isClosed) {
          throw new InterruptedException();
        }
        if (instance.isUseNativeQueue()) {
          Thread.sleep(20);
        } else if (instance.isBusy()) {
          Thread.sleep(100);
          continue;
        }
        BlockEvent blockEvent = blockEventGet.getBlockEvent(tmp);
        realtimeEventService.flush(blockEvent, false);
        solidEventService.flush(blockEvent);
        tmp++;
        endNum = manager.getDynamicPropertiesStore().getLatestSolidifiedBlockNum();
      }
      long startNum = endNum == 0 ? 0 : endNum - 1;
      initEventService(manager.getChainBaseManager().getBlockIdByNum(startNum));
```

**File:** framework/src/main/resources/config.conf (L448-451)
```text
  version = 0
  # Specify the starting block number to sync historical events. Only applicable when version = 1.
  # After performing a full event sync, set this value to 0 or a negative number.
  # startSyncBlockNum = 1
```

**File:** framework/src/main/java/org/tron/core/services/event/BlockEventGet.java (L96-137)
```java
  public SmartContractTrigger getContractTrigger(BlockCapsule block, long solidNum) {

    GrpcAPI.TransactionInfoList list = manager.getTransactionInfoByBlockNum(block.getNum());

    SmartContractTrigger contractTrigger = new SmartContractTrigger();
    for (int i = 0; i < block.getTransactions().size(); i++) {
      Protocol.Transaction tx = block.getInstance().getTransactions(i);
      Protocol.TransactionInfo txInfo = list.getTransactionInfo(i);

      List<ContractTrigger> triggers = parseLogs(tx, txInfo);
      for (ContractTrigger trigger : triggers) {
        if (!EventPluginLoader.matchFilter(trigger)) {
          continue;
        }
        ContractTrigger eventOrLog = processTrigger(trigger);
        eventOrLog.setBlockHash(Hex.toHexString(block.getBlockId().getBytes()));
        eventOrLog.setLatestSolidifiedBlockNumber(solidNum);
        if (eventOrLog instanceof ContractEventTrigger) {
          ContractEventTrigger event = (ContractEventTrigger) eventOrLog;
          if (instance.isContractEventTriggerEnable() || instance.isSolidityEventTriggerEnable()) {
            contractTrigger.getContractEventTriggers().add(event);
          }
          if ((instance.isContractLogTriggerEnable()
              && instance.isContractLogTriggerRedundancy())
              || (instance.isSolidityLogTriggerEnable()
              && instance.isSolidityLogTriggerRedundancy())) {
            ContractLogTrigger logTrigger = new ContractLogTrigger(event);
            logTrigger.setTopicList(trigger.getLogInfo().getHexTopics());
            logTrigger.setData(trigger.getLogInfo().getHexData());
            contractTrigger.getRedundancies().add(logTrigger);
          }
        } else if (eventOrLog instanceof ContractLogTrigger) {
          ContractLogTrigger log = (ContractLogTrigger) eventOrLog;
          if (instance.isContractLogTriggerEnable() || instance.isSolidityLogTriggerEnable()) {
            contractTrigger.getContractLogTriggers().add(log);
          }
        }
      }
    }

    return contractTrigger;
  }
```
