This confirms a valid, strong analog: a plugin-triggered exception inside `Manager.blockTrigger` is wrapped into a `TronError`, which per its own javadoc and `ExitManager.initExceptionHandler` causes the **entire node process to call `System.exit()`**, i.e., a single failure in an optional, pluggable event-subscription component takes down the whole node — a strict analog (indeed worse) than SponsorVault's "revert the execute() transaction" DoS.

### Title
Uncaught Exception From Optional Event-Plugin Trigger Handling Crashes The Entire Node - (File: `framework/src/main/java/org/tron/core/db/Manager.java`)

### Summary
`Manager.blockTrigger()` posts block/transaction/log events to registered `EventPluginLoader` listeners and JSON-RPC filter queues after every block is applied. Any exception thrown while posting these triggers — including from a third-party event plugin (loaded dynamically via `pluginManager`/`eventListeners`) — is caught, wrapped in a `TronError`, and rethrown. `TronError` is documented as fatal: it is designed to propagate to the JVM's default uncaught-exception handler, which calls `System.exit()`, killing the node.

### Finding Description
`blockTrigger` runs unconditionally as part of block application (called from `applyBlock`/`pushBlock` flow) whenever JSON-RPC filters or the event-subscribe plugin system are enabled: [1](#0-0) 

```java
void blockTrigger(final BlockCapsule block, long oldSolid, long newSolid) {
    try {
      if (CommonParameter.getInstance().isJsonRpcHttpFullNodeEnable()) {
        postBlockFilter(block, false);
        postLogsFilter(block, false, false);
      }
      if (CommonParameter.getInstance().isJsonRpcHttpSolidityNodeEnable()) {
        postSolidityFilter(oldSolid, newSolid);
      }
      if (EventPluginLoader.getInstance().getVersion() != 0) {
        lastUsedSolidityNum = newSolid;
        return;
      }
      postBlockTrigger(block, false);
      postSolidityTrigger(newSolid);
    } catch (Exception e) {
      logger.error("Block trigger failed. head: {}, oldSolid: {}, newSolid: {}",
          block.getNum(), oldSolid, newSolid, e);
      throw new TronError(e, TronError.ErrCode.EVENT_SUBSCRIBE_ERROR);
    }
  }
```

`postBlockTrigger` (in real-time mode, `EventPluginLoader.getInstance().getVersion() == 0`) synchronously invokes `EventPluginLoader.postBlockTrigger`/`postTransactionTrigger`, which iterate over externally-loaded `eventListeners` (third-party plugins, analogous to the third-party `SponsorVault` in the Connext report): [2](#0-1) 

The `TronError` thrown here is explicitly documented as unrecoverable and designed to trigger process termination: [3](#0-2) 

And `ExitManager` wires this up as the default uncaught exception handler, calling `System.exit(code)` for any `TronError` (or any exception whose cause chain contains one): [4](#0-3) 

Unlike `postContractTrigger`, which explicitly wraps each per-trigger dispatch in `try { ... } catch (Throwable throwable) { logger.warn(...); }` to tolerate plugin failures (see `Manager.java:2504-2512`), `blockTrigger`'s block/transaction/solidity trigger path has no such isolation — any single exception (a bug, misconfiguration, or malicious/faulty behavior in a loaded event plugin, or even a downstream I/O error in `NativeMessageQueue`) escalates to a fatal `TronError` and kills the whole node process, not just skip the failed notification. This is confirmed by the existing test `ManagerTest.blockTrigger()`, which asserts a mocked `postBlockTrigger` throw results in a `TronError` with `EVENT_SUBSCRIBE_ERROR`: [5](#0-4) 

### Impact Explanation
This is a stronger DoS than the referenced SponsorVault bug: SponsorVault only reverted a single `execute()` transaction, leaving the chain/relayer network itself alive. Here, a fault in an optional, node-operator-configured, dynamically-loaded plugin (event-subscribe plugin or JSON-RPC filter path) during block application causes the entire full node to exit via `System.exit()`. If the plugin is buggy, misconfigured, briefly unavailable (e.g., a downstream queue/socket the plugin talks to), or otherwise throws under some inputs, every node running with event-subscribe/JSON-RPC filters enabled would repeatedly crash on the same block upon restart, effectively halting that node's participation in consensus/serving — with no built-in circuit breaker or fail-safe like the fix recommended for SponsorVault (try/catch that swallows and logs, but continues to make progress).

### Likelihood Explanation
Event-subscribe plugins and JSON-RPC full-node/solidity filters are common production configurations (documented, commonly enabled features), so the code path is reachable in normal operation, not just by an attacker. The failure trigger doesn't require a malicious peer or privileged actor — it only requires the configured event plugin (a component explicitly designed to be pluggable/third-party, matching the "third-party is a single point of failure" theme of the original finding) to throw once during block processing. Given `postContractTrigger` already needed a defensive `catch (Throwable)` for exactly this reason (see `Manager.java:2508-2512` and the existing test `ManagerMockTest.testPostContractTriggerSwallowsThrowable`), it indicates the team is aware plugin trigger code can throw unexpectedly, but the mitigation was not applied consistently to `blockTrigger`'s block/transaction/solidity trigger paths.

### Recommendation
Apply the same defensive pattern already used in `postContractTrigger` to `blockTrigger`: catch and log exceptions from `postBlockFilter`, `postLogsFilter`, `postSolidityFilter`, `postBlockTrigger`, and `postSolidityTrigger` individually (or in aggregate) without escalating to a fatal `TronError`/`System.exit()`. Reserve `TronError` for cases where safe continued operation is truly impossible (e.g. corrupted state), not for optional, best-effort notification/subscription features whose failure should not stop block processing or crash the node.

### Proof of Concept
1. Enable the event-subscribe plugin system (`eventPluginLoaded = true`) or JSON-RPC full-node filters (`isJsonRpcHttpFullNodeEnable`) on a node.
2. Register/load a plugin listener whose `handleBlockEvent`/`handleTransactionTrigger` implementation throws an exception for some block content (this mirrors an external/third-party component misbehaving, as with `SponsorVault`).
3. As demonstrated by the existing unit test `ManagerTest.blockTrigger()` (`framework/src/test/java/org/tron/core/db/ManagerTest.java:1387-1395`), any exception from `postBlockTrigger` propagates out of `Manager.blockTrigger` as a `TronError` with `ErrCode.EVENT_SUBSCRIBE_ERROR`.
4. `TronError.java`'s own documentation and `ExitManager.initExceptionHandler`/`logAndExit` confirm this leads to `System.exit(1)` on the node — the node process terminates the moment the block is processed, causing denial of service until the operator disables/fixes the faulty plugin.

### Citations

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1429-1456)
```java
  void blockTrigger(final BlockCapsule block, long oldSolid, long newSolid) {
    // post block and logs for jsonrpc
    try {
      if (CommonParameter.getInstance().isJsonRpcHttpFullNodeEnable()) {
        postBlockFilter(block, false);
        postLogsFilter(block, false, false);
      }

      if (CommonParameter.getInstance().isJsonRpcHttpSolidityNodeEnable()) {
        postSolidityFilter(oldSolid, newSolid);
      }

      if (EventPluginLoader.getInstance().getVersion() != 0) {
        lastUsedSolidityNum = newSolid;
        return;
      }

      // if event subscribe is enabled, post block trigger to queue (real-time, not removed)
      postBlockTrigger(block, false);
      // if event subscribe is enabled, post solidity trigger to queue
      // (also emits solidified-mode block/transaction triggers)
      postSolidityTrigger(newSolid);
    } catch (Exception e) {
      logger.error("Block trigger failed. head: {}, oldSolid: {}, newSolid: {}",
          block.getNum(), oldSolid, newSolid, e);
      throw new TronError(e, TronError.ErrCode.EVENT_SUBSCRIBE_ERROR);
    }
  }
```

**File:** framework/src/main/java/org/tron/common/logsfilter/EventPluginLoader.java (L516-524)
```java
  public void postBlockTrigger(BlockLogTrigger trigger) {
    if (useNativeQueue) {
      NativeMessageQueue.getInstance()
          .publishTrigger(toJsonString(trigger), trigger.getTriggerName());
    } else {
      eventListeners.forEach(listener ->
          listener.handleBlockEvent(toJsonString(trigger)));
    }
  }
```

**File:** common/src/main/java/org/tron/core/exception/TronError.java (L5-9)
```java
/**
 * If a {@link TronError} is thrown, the service will trigger {@link System#exit(int)} by
 * {@link Thread#setDefaultUncaughtExceptionHandler(Thread.UncaughtExceptionHandler)}.
 * NOTE: Do not attempt to catch {@link TronError}.
 */
```

**File:** common/src/main/java/org/tron/common/exit/ExitManager.java (L23-52)
```java
  public static void initExceptionHandler() {
    Thread.setDefaultUncaughtExceptionHandler((t, e) -> {
      findTronError(e).ifPresent(ExitManager::logAndExit);
      logger.error("Uncaught exception", e);
    });
  }

  public static Optional<TronError> findTronError(Throwable e) {
    if (e == null) {
      return Optional.empty();
    }

    Set<Throwable> seen = new HashSet<>();

    while (e != null && !seen.contains(e)) {
      if (e instanceof TronError) {
        return Optional.of((TronError) e);
      }
      seen.add(e);
      e = e.getCause();
    }
    return Optional.empty();
  }

  public static void logAndExit(TronError exit) {
    final int code = exit.getErrCode().getCode();
    logger.error("Shutting down with code: {}, reason: {}", exit.getErrCode(), exit.getMessage());
    Thread exitThread = exitThreadFactory.newThread(() -> System.exit(code));
    exitThread.start();
  }
```

**File:** framework/src/test/java/org/tron/core/db/ManagerTest.java (L1387-1395)
```java
  @Test
  public void blockTrigger() {
    Manager manager = spy(new Manager());
    doThrow(new RuntimeException("postBlockTrigger mock")).when(manager)
        .postBlockTrigger(any(), anyBoolean());
    TronError thrown = Assert.assertThrows(TronError.class, () ->
        manager.blockTrigger(new BlockCapsule(Block.newBuilder().build()), 1, 1));
    Assert.assertEquals(TronError.ErrCode.EVENT_SUBSCRIBE_ERROR, thrown.getErrCode());
  }
```
