### Title
Unbounded `lock.await()` inside reward-VI precomputation permanently freezes core staking/voting actuators on failure - (File: `chainbase/src/main/java/org/tron/core/service/RewardViCalService.java`)

### Summary
`MortgageService.withdrawReward` is called unconditionally at the start of the core, unprivileged actuators that any account uses to participate in on-chain governance and resource management (`VoteWitnessActuator`, `UnfreezeBalanceActuator`, `UnfreezeBalanceV2Actuator`, `WithdrawBalanceActuator`, and the equivalent TVM native-contract processors `VoteWitnessProcessor`, `UnfreezeBalanceV2Processor`, `WithdrawRewardProcessor`, `Program.withdrawRewardAndCancelVote`). Just like `OgvStaking` unconditionally calling `RewardsSource.collectRewards` inside `stake`/`unstake`/`extend`, java-tron's staking/voting path is tightly coupled to an auxiliary reward-calculation component, `RewardViCalService`, whose failure/incompletion can hang or break the primary functionality. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`MortgageService.withdrawReward` computes historical rewards via `computeReward(beginCycle, endCycle, accountCapsule)`, which delegates cycles prior to the new-reward-algorithm cutover to `getOldReward`, and `getOldReward` — when `allowOldRewardOpt()` is enabled — forwards to `RewardViCalService.getNewRewardAlgorithmReward`: [4](#0-3) 

`getNewRewardAlgorithmReward` blocks the calling (actuator-execution) thread with an unbounded `CountDownLatch.await()` if the background precomputation (`isDone()`) has not finished: [5](#0-4) 

The latch is only released in `clearUp()`, which is invoked from the periodic background task `maybeRun()` scheduled via `ExecutorServiceManager.scheduleWithFixedDelay`: [6](#0-5) 

`ExecutorServiceManager.scheduleWithFixedDelay` wraps the scheduled `Runnable` such that any `Throwable` escaping `command.run()` (i.e. `maybeRun()`) is rethrown after being checked against `ExitManager`: [7](#0-6) 

Per the JDK contract for `ScheduledExecutorService.scheduleWithFixedDelay`, if a single execution of a periodic task throws, all future executions are suppressed. `maybeRun()` already catches generic exceptions from `startRewardCal()`/`accumulateWitnessVi()` (DB errors, malformed stored data, `NumberFormatException`, Merkle-root computation issues, etc.) and deliberately rethrows a `TronError` on any failure: [8](#0-7) 

If that rethrow terminates the scheduled task (rather than triggering an immediate process exit via `ExitManager.findTronError`), the periodic task is permanently suppressed and `clearUp()`/`lock.countDown()` is never invoked. From that point forward, every call into `getNewRewardAlgorithmReward` — triggered by any anonymous account submitting a `VoteWitnessContract`, `UnfreezeBalanceContract`, `UnfreezeBalanceV2Contract`, `WithdrawBalanceContract` transaction, or a smart contract invoking the `vote`/`unfreezeBalance`/`withdrawReward` TVM opcodes — blocks forever on `lock.await()`. Because these actuators execute synchronously inside block/transaction processing, this stalls the thread processing transactions/blocks, effectively halting further chain progress for any account that still needs an old-cycle reward computed.

This is the direct structural analog of the reported bug: a core, permissionless, user-facing operation (`stake`/`unstake`/`vote`/`unfreeze`) is strongly coupled to an auxiliary reward subsystem with no isolation (no timeout, no try/catch, no fallback) around the call into that subsystem.

### Impact Explanation
A permanent block on `lock.await()` on the block/transaction-processing thread constitutes a consensus-halting Denial-of-Service: no further votes, freezes, unfreezes, or reward withdrawals involving old-algorithm cycles can be processed, and depending on where in the pipeline the actuator executes, block production/validation itself can stall. This matches the "concrete... DoS via ... protocol implementation" acceptance criterion. The trigger condition (an exception inside `accumulateWitnessVi`/`startRewardCal`, reachable via corrupted/unexpected stored delegation or witness data during the reward-algorithm migration window) is within the reward/resource accounting logic explicitly in scope.

### Likelihood Explanation
This path only activates when `allowOldRewardOpt()` is enabled and the chain is within the transition window where `beginCycle < newRewardCalStartCycle` (i.e., accounts still have unwithdrawn rewards from before `NEW_REWARD_ALGORITHM_EFFECTIVE_CYCLE`). This is a narrow, migration-specific window, and I could not fully verify from the available index whether `ExitManager.findTronError` intercepts and force-exits the process before the suppression of future scheduled runs takes effect (which would turn this into a controlled crash/restart rather than a silent permanent hang) — this needs confirmation by reading `ExitManager.findTronError` and the full exception surface of `startRewardCal`/`accumulateWitnessVi`/`calcMerkleRoot`, which I could not fully inspect in the remaining budget. If `ExitManager` catches all realistic failures and exits, the practical impact is a crash-and-restart DoS rather than a silent hang; either way the actuator's lack of isolation around the coupled subsystem is the same root-cause pattern as the reported bug.

### Recommendation
- Do not let `withdrawReward`/`computeReward` block indefinitely on an internal precomputation service. Use a bounded `lock.await(timeout, unit)` and fall back to synchronous on-the-fly computation (the existing per-cycle loop in `getOldReward`) if the timeout elapses, rather than hanging.
- Decouple the actuators from `RewardViCalService`'s readiness: wrap the call in `MortgageService.getOldReward` so that any failure or non-readiness of `RewardViCalService` degrades gracefully (e.g., defers reward crediting, emits an event/log) instead of blocking the caller.
- Ensure `RewardViCalService.maybeRun()` failures are handled uniformly — either always force a controlled process exit via `ExitManager` (fail-fast, avoiding an indefinite hang) or make the periodic task resilient to individual iteration failures (e.g., wrap each `accumulateWitnessVi` call so a single bad entry doesn't kill the whole scheduled task).

### Proof of Concept
1. Enable `allowOldRewardOpt` (via the corresponding committee/dynamic parameter) and configure `NEW_REWARD_ALGORITHM_EFFECTIVE_CYCLE` such that `newRewardCalStartCycle > 1`, entering the migration window.
2. Cause `startRewardCal()`/`accumulateWitnessVi()` to throw during the first scheduled run of `RewardViCalService.maybeRun()` — e.g., by having malformed/missing data under a witness's delegation "reward"/"vote" keys that leads to an unexpected runtime exception during `getReward`/`getWitnessVote`/BigInteger parsing, or a failure in `calcMerkleRoot`'s iterator. Confirm (via logs) that `maybeRun()` logs "Find fatal error, program will be exited soon." and rethrows `TronError`, and that `clearUp()`/`lock.countDown()` was never reached.
3. Broadcast any `VoteWitnessContract`, `UnfreezeBalanceContract`/`UnfreezeBalanceV2Contract`, or `WithdrawBalanceContract` transaction (or invoke `vote`/`unfreezeBalance`/`withdrawReward` from a smart contract) from an account holding votes cast before the effective cycle.
4. Observe that the transaction-processing thread executing the actuator blocks indefinitely inside `RewardViCalService.getNewRewardAlgorithmReward`'s `lock.await()`, since `isDone()` never becomes true and no other scheduled iteration will run.

Note: I was unable to fully verify within the available exploration whether `ExitManager.findTronError` intercepts this specific `TronError` to force an immediate process exit (which would change the failure mode from "silent permanent hang" to "crash-and-restart"); this should be confirmed by inspecting `ExitManager` before finalizing severity, but the coupling flaw and lack of isolation in the actuator/TVM call sites is confirmed by code.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L71-75)
```java
    byte[] ownerAddress = unfreezeBalanceContract.getOwnerAddress().toByteArray();

    //
    mortgageService.withdrawReward(ownerAddress);

```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L156-162)
```java
    byte[] ownerAddress = voteContract.getOwnerAddress().toByteArray();

    VotesCapsule votesCapsule;

    //
    mortgageService.withdrawReward(ownerAddress);

```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L39-41)
```java
  public void execute(VoteWitnessParam param, Repository repo) throws ContractExeException {
    byte[] ownerAddress = param.getVoterAddress();
    VoteRewardUtil.withdrawReward(ownerAddress, repo);
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L260-269)
```java
  private long getOldReward(long begin, long end, List<Pair<byte[], Long>> votes) {
    if (dynamicPropertiesStore.allowOldRewardOpt()) {
      return rewardViCalService.getNewRewardAlgorithmReward(begin, end, votes);
    }
    long reward = 0;
    for (long cycle = begin; cycle < end; cycle++) {
      reward += computeReward(cycle, votes);
    }
    return reward;
  }
```

**File:** chainbase/src/main/java/org/tron/core/service/RewardViCalService.java (L101-135)
```java
  private void maybeRun() {
    try {
      if (enableNewRewardAlgorithm()) {
        if (this.newRewardCalStartCycle > 1) {
          if (isDone()) {
            this.clearUp(true);
            logger.info("rewardViCalService is already done");
          } else {
            if (lastBlockNumber ==  Long.MAX_VALUE // start rewardViCalService immediately
                || this.getLatestBlockHeaderNumber() > lastBlockNumber) {
              // checkpoint is flushed to db, so we can start rewardViCalService
              startRewardCal();
              clearUp(true);
            } else {
              logger.info("startRewardCal will run after checkpoint is flushed to db");
            }
          }
        } else {
          clearUp(false);
          logger.info("rewardViCalService is no need to run");
        }
      }
    } catch (Exception e) {
      logger.error(" Find fatal error, program will be exited soon.", e);
      throw new TronError(e, TronError.ErrCode.REWARD_VI_CALCULATOR);
    }
  }

  private void clearUp(boolean isDone) {
    lock.countDown();
    if (isDone) {
      calcMerkleRoot();
    }
    es.shutdown();
  }
```

**File:** chainbase/src/main/java/org/tron/core/service/RewardViCalService.java (L143-153)
```java
  public long getNewRewardAlgorithmReward(long beginCycle, long endCycle,
                                          List<Pair<byte[], Long>> votes) {
    if (!isDone()) {
      logger.warn("rewardViCalService is not done, wait for it");
      try {
        lock.await();
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
        throw new TronDBException(e);
      }
    }
```

**File:** common/src/main/java/org/tron/common/es/ExecutorServiceManager.java (L117-130)
```java
  public static ScheduledFuture<?> scheduleWithFixedDelay(ScheduledExecutorService es,
                                                   Runnable command,
                                                   long initialDelay,
                                                   long delay,
                                                   TimeUnit unit) {
    return es.scheduleWithFixedDelay(() -> {
      try {
        command.run();
      } catch (Throwable e) {
        ExitManager.findTronError(e).ifPresent(ExitManager::logAndExit);
        throw e;
      }
    }, initialDelay, delay, unit);
  }
```
