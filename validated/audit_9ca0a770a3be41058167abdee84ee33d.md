## Finding

### Title
Silent swallowing of storage-persistence failures during block production allows a witness to broadcast a signed block whose local head is never advanced, risking equivocation/double-signing on restart - (File: `framework/src/main/java/org/tron/core/consensus/BlockHandleImpl.java`)

### Summary
This maps to the reported bug class: a signed/propagated state-transition (there: a vote; here: a self-produced, signed block) is broadcast to the network *before* the local persistence step, and if persistence fails (e.g., due to storage exhaustion), the failure is caught and swallowed rather than halting the node. On restart or in the very next iteration, the node's on-disk chain head is stale, creating conditions for a witness to re-derive and sign a conflicting/duplicate block for a slot it has already used - the DPoS analogue of "double-vote."

### Finding Description
`BlockHandleImpl.produce()` generates a block, immediately hands it to consensus and broadcasts it over the network, and only afterwards attempts to persist it locally via `manager.pushBlock(blockCapsule)`: [1](#0-0) 

Note the ordering: `tronNetService.broadcast(blockMessage)` happens *before* `manager.pushBlock(blockCapsule)`. If `pushBlock` throws — which it will if the underlying storage layer fails to write (e.g., disk full) — the `catch (Exception e)` block only logs the error and returns `null`; it does not rethrow, halt the executor, or trigger the node's fatal-error/shutdown path.

The underlying storage layer does raise fatal errors for disk/write failures: `LevelDbDataSourceImpl` wraps I/O failures as `RuntimeException`/`TronError` (`LEVELDB_INIT`), and `SnapshotManager.flush()` explicitly converts checkpoint/flush failures into a `TronError.ErrCode.DB_FLUSH`, which is the mechanism intended to escalate storage failures to a process-halting exit: [2](#0-1) 

The intended escalation path is `DposTask`'s outer loop, which inspects any `Throwable` for a wrapped `TronError` via `ExitManager.findTronError` and rethrows it (leading to process exit): [3](#0-2) [4](#0-3) 

However, `TronError` is a normal exception type reachable by `catch (Exception e)`, and `BlockHandleImpl.produce()` catches and fully swallows it *before* it can ever reach `DposTask`'s `Throwable` handler. This means a storage failure that occurs specifically during self-produced-block persistence (`pushBlock` inside `produce()`) never triggers the node's designed halt-on-fatal-storage-error behavior — unlike storage failures on other paths (e.g., `SnapshotManager.flush()` failing during normal operation), which do correctly propagate and shut the node down.

Because the block was already signed with the witness's key and broadcast to peers (`tronNetService.broadcast`) *before* the failed persistence attempt, the vote/attestation-equivalent (the signed block) is already irrevocably out in the network, exactly as in the original report where "the vote was created... it will still be propagated, but the updated state will not be saved to disk."

### Impact Explanation
If a witness's local storage fails during the persistence step (`manager.pushBlock`) inside `produce()`, the failure is silently absorbed and the DPoS production loop (`DposTask`) continues running rather than halting. The witness's on-disk chain head (`DynamicPropertiesStore` latest block header) is not advanced to reflect the just-broadcast block. On a subsequent slot assignment (or after a restart that reloads the stale on-disk head), the witness node can compute the next block atop the stale parent and produce/sign another block for the same or an overlapping slot, resulting in equivocation: two differently-numbered/contented blocks signed by the same witness key for what the network sees as conflicting chain states. This undermines DPoS safety guarantees, can create forks, and damages the witness's reputation/eligibility, mirroring the "double-vote" risk described in the source report.

### Likelihood Explanation
This requires the witness node's underlying storage to fail specifically at the moment of `manager.pushBlock` within the self-produce path (e.g., disk full, I/O error, RocksDB/LevelDB write failure) — a real-world condition for any long-running validator node that is not proactively monitoring disk usage. It does not require an external attacker; it is a reliability/robustness bug that a witness operator could unknowingly trigger. Given that block production happens continuously (every ~3 seconds when scheduled), and that TRON's design elsewhere (`SnapshotManager.flush`) explicitly treats such failures as fatal and halts the node, the omission of this same treatment along the `produce()` path is a genuine gap rather than an intentional design choice.

### Recommendation
In `BlockHandleImpl.produce()`, do not blanket-swallow all exceptions from `manager.pushBlock(blockCapsule)` (and ideally from `tronNetService.broadcast`/`consensus.receiveBlock` as well). At minimum, re-check/rethrow any exception whose cause chain contains a `TronError` (mirroring `ExitManager.findTronError`) so that storage failures on the self-produced-block path correctly escalate to the node's existing halt-on-fatal-storage mechanism, consistent with how `SnapshotManager.flush()` failures are already handled. More broadly, consider persisting the block locally before broadcasting it, so that a persistence failure prevents the signed block from ever reaching the network in the first place.

### Proof of Concept
1. Configure/run a witness node and induce a storage failure (e.g., fill the disk, or inject a fault in the underlying RocksDB/LevelDB write path) such that `manager.pushBlock` inside `BlockHandleImpl.produce()` throws.
2. Observe that `consensus.receiveBlock` and `tronNetService.broadcast` have already executed, so peers receive the fully signed block.
3. Observe that the `catch (Exception e)` block in `produce()` only logs `"Handle block {} failed."` and returns `null` — the `DposTask` loop is not stopped, and the process does not exit, even though the local `DynamicPropertiesStore`/chain head was not advanced by the failed `pushBlock`.
4. On restart (or the very next scheduled slot for that witness), confirm that `chainBaseManager.getHeadBlockNum()`/`getHeadBlockId()` reflect the stale (pre-broadcast) head, allowing `generateBlock` to build and sign a new/duplicate block from that stale parent — producing two signed blocks from the same witness key that conflict with what was already propagated to the network. [5](#0-4)

### Citations

**File:** framework/src/main/java/org/tron/core/consensus/BlockHandleImpl.java (L45-60)
```java
  public BlockCapsule produce(Miner miner, long blockTime, long timeout) {
    BlockCapsule blockCapsule = manager.generateBlock(miner, blockTime, timeout);
    if (blockCapsule == null) {
      return null;
    }
    try {
      consensus.receiveBlock(blockCapsule);
      BlockMessage blockMessage = new BlockMessage(blockCapsule);
      tronNetService.broadcast(blockMessage);
      manager.pushBlock(blockCapsule);
    } catch (Exception e) {
      logger.error("Handle block {} failed.", blockCapsule.getBlockId().getString(), e);
      return null;
    }
    return blockCapsule;
  }
```

**File:** chainbase/src/main/java/org/tron/core/db2/core/SnapshotManager.java (L328-354)
```java
  public void flush() {
    if (unChecked) {
      return;
    }

    if (shouldBeRefreshed()) {
      try {
        long start = System.currentTimeMillis();
        if (!isV2Open()) {
          deleteCheckpoint();
        }
        createCheckpoint();

        long checkPointEnd = System.currentTimeMillis();
        refresh();
        flushCount = 0;
        logger.info("Flush cost: {} ms, create checkpoint cost: {} ms, refresh cost: {} ms.",
            System.currentTimeMillis() - start,
            checkPointEnd - start,
            System.currentTimeMillis() - checkPointEnd
        );
      } catch (TronDBException e) {
        logger.error(" Find fatal error, program will be exited soon.", e);
        hitDown = true;
        throw new TronError(e, TronError.ErrCode.DB_FLUSH);
      }
    }
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/DposTask.java (L52-76)
```java
    Runnable runnable = () -> {
      while (isRunning) {
        try {
          if (dposService.isNeedSyncCheck()) {
            Thread.sleep(1000);
            dposService.setNeedSyncCheck(dposSlot.getTime(1) < System.currentTimeMillis());
          } else {
            long time =
                BLOCK_PRODUCED_INTERVAL - System.currentTimeMillis() % BLOCK_PRODUCED_INTERVAL;
            Thread.sleep(time);
            State state = produceBlock();
            if (!State.OK.equals(state)) {
              logger.info("Produce block failed: {}", state);
            }
          }
        } catch (InterruptedException e) {
          logger.warn("Produce block task interrupted.");
          Thread.currentThread().interrupt();
        } catch (Throwable throwable) {
          logger.error("Produce block error.", throwable);
          ExitManager.findTronError(throwable).ifPresent(e -> {
            throw e;
          });
        }
      }
```

**File:** common/src/main/java/org/tron/common/exit/ExitManager.java (L30-52)
```java
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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1631-1663)
```java
  public BlockCapsule generateBlock(Miner miner, long blockTime, long timeout) {
    String address =  StringUtil.encode58Check(miner.getWitnessAddress().toByteArray());
    final Histogram.Timer timer = Metrics.histogramStartTimer(
        MetricKeys.Histogram.BLOCK_GENERATE_LATENCY, address);
    Metrics.histogramObserve(MetricKeys.Histogram.MINER_DELAY,
        (System.currentTimeMillis() - blockTime) / Metrics.MILLISECONDS_PER_SECOND, address);
    long postponedTrxCount = 0;
    logger.info("Generate block {} begin.", chainBaseManager.getHeadBlockNum() + 1);

    BlockCapsule blockCapsule = new BlockCapsule(chainBaseManager.getHeadBlockNum() + 1,
        chainBaseManager.getHeadBlockId(),
        blockTime, miner.getWitnessAddress());
    blockCapsule.generatedByMyself = true;
    session.reset();
    session.setValue(revokingStore.buildSession());

    accountStateCallBack.preExecute(blockCapsule);

    if (getDynamicPropertiesStore().getAllowMultiSign() == 1) {
      byte[] privateKeyAddress = miner.getPrivateKeyAddress().toByteArray();
      AccountCapsule witnessAccount = getAccountStore()
          .get(miner.getWitnessAddress().toByteArray());
      if (!Arrays.equals(privateKeyAddress, witnessAccount.getWitnessPermissionAddress())) {
        logger.warn("Witness permission is wrong.");
        return null;
      }
    }

    HistoryBlockHashUtil.write(this, blockCapsule);

    Set<String> accountSet = new HashSet<>();
    AtomicInteger shieldedTransCounts = new AtomicInteger(0);
    List<TransactionCapsule> toBePacked = new ArrayList<>();
```
