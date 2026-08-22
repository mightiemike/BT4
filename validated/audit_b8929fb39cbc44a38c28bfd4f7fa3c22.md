### Title
Uncaught exception in `ProposalController.processProposal` during maintenance can halt block processing for all nodes - (File: `framework/src/main/java/org/tron/core/consensus/ProposalController.java`)

### Summary
The reported Voter.distribute bug is a class of vulnerability where a single item in a loop that runs unconditionally for all items can throw, aborting the entire batch operation and stalling the protocol. The closest reachable analog in java-tron is `ProposalController.processProposals()`, which is invoked deterministically on every node during the maintenance cycle (`MaintenanceManager.doMaintenance()`) and processes every pending/expired governance proposal in a single unguarded loop.

### Finding Description
`processProposals()` iterates from `latestProposalNum` down to `0` [1](#0-0) . Only the store read (`manager.getProposalStore().get(...)`) is wrapped in a try/catch that skips to the next proposal on failure [2](#0-1) . However, once a proposal is determined to have expired, `processProposal(proposalCapsule)` is called with no exception handling [3](#0-2) , which in turn calls `setDynamicParameters` → `ProposalService.process(manager, proposalCapsule)` [4](#0-3) .

`ProposalService.process` applies each parameter in the approved proposal via a switch statement covering dozens of `ProposalType`s, several of which perform arithmetic directly on the submitted `entry.getValue()`, e.g. `ADAPTIVE_RESOURCE_LIMIT_TARGET_RATIO` computes `ratio = 24 * 60 * entry.getValue()` and then divides `getTotalEnergyLimit() / ratio` [5](#0-4) . If any approved proposal parameter value results in an arithmetic error (e.g. a zero ratio) or another runtime exception during processing, that exception propagates out of `processProposal` uncaught, unlike the guarded `get()` call in the same loop.

Because `processProposals()` is invoked from every full node's `doMaintenance()` at the same deterministic maintenance boundary [6](#0-5) , an uncaught exception here does not just fail one node's call (as in the Voter.distribute report) — it fails identically on every node applying that block, which is a stronger and more damaging version of the reported bug class (chain-wide stall vs. a single failed transaction).

### Impact Explanation
If a maintenance-time proposal parameter update throws, block application halts across the network at that maintenance boundary, since all nodes execute the same deterministic logic. This maps to the report's "protocol may be stopped" impact, but is more severe because it affects consensus/block production rather than a single contract call that can simply be retried with a smaller batch.

### Likelihood Explanation
This code path requires a successfully approved proposal (needs witness quorum via `hasMostApprovals`) whose parameter value drives one of the unguarded arithmetic branches in `ProposalService.process`. I could not fully confirm within the available index whether the actuator-side validator (`actuator/src/main/java/org/tron/core/utils/ProposalUtil.java`) rejects degenerate values (e.g. `0`) for parameters like `ADAPTIVE_RESOURCE_LIMIT_TARGET_RATIO` before a proposal can be approved — the file exists and references this type, but I was unable to retrieve its validation logic in this session. This bounds the likelihood as governance-gated (requires witness approval), lower than an anonymous RPC path, but the missing per-item exception isolation in `processProposals()` is a confirmed structural weakness independent of that open question.

### Recommendation
Wrap the `processProposal(proposalCapsule)` call (not just the store read) in `processProposals()` in a try/catch that logs and continues to the next proposal on failure, mirroring the existing pattern used for the store-read exception. Additionally, ensure `ProposalUtil`'s validators reject any value that would cause division-by-zero or overflow in `ProposalService.process` for every `ProposalType` before a proposal can reach `APPROVED` state.

### Proof of Concept
Not independently reproduced — this analysis is based on static review of `ProposalController.java` and `ProposalService.java`; I was unable to confirm in this session whether `ProposalUtil`'s validator already blocks the degenerate value needed to trigger the arithmetic exception in `ADAPTIVE_RESOURCE_LIMIT_TARGET_RATIO`. A Devin session with full repo access would be needed to inspect `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` in full and construct a concrete end-to-end PoC (submit proposal → approve via witnesses → trigger maintenance → observe exception).

### Citations

**File:** framework/src/main/java/org/tron/core/consensus/ProposalController.java (L26-45)
```java
  public void processProposals() {
    long latestProposalNum = manager.getDynamicPropertiesStore().getLatestProposalNum();
    if (latestProposalNum == 0) {
      logger.info("latestProposalNum is 0, return");
      return;
    }

    long proposalNum = latestProposalNum;

    ProposalCapsule proposalCapsule = null;

    while (proposalNum > 0) {
      try {
        proposalCapsule = manager.getProposalStore()
            .get(ProposalCapsule.calculateDbKey(proposalNum));
      } catch (Exception ex) {
        logger.error("", ex);
        proposalNum--;
        continue;
      }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalController.java (L61-66)
```java
      long currentTime = manager.getDynamicPropertiesStore().getNextMaintenanceTime();
      if (proposalCapsule.hasExpired(currentTime)) {
        processProposal(proposalCapsule);
        proposalNum--;
        continue;
      }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalController.java (L74-98)
```java
  public void processProposal(ProposalCapsule proposalCapsule) {

    List<ByteString> activeWitnesses = this.manager.getWitnessScheduleStore().getActiveWitnesses();
    if (proposalCapsule.hasMostApprovals(activeWitnesses)) {
      logger.info(
          "Processing proposal,id:{},it has received most approvals, "
              + "begin to set dynamic parameter:{}, "
              + "and set proposal state as APPROVED",
          proposalCapsule.getID(), proposalCapsule.getParameters());
      setDynamicParameters(proposalCapsule);
      proposalCapsule.setState(State.APPROVED);
      manager.getProposalStore().put(proposalCapsule.createDbKey(), proposalCapsule);
    } else {
      logger.info(
          "Processing proposal,id:{}, "
              + "it has not received enough approvals, set proposal state as DISAPPROVED",
          proposalCapsule.getID());
      proposalCapsule.setState(State.DISAPPROVED);
      manager.getProposalStore().put(proposalCapsule.createDbKey(), proposalCapsule);
    }

  }

  public void setDynamicParameters(ProposalCapsule proposalCapsule) {
    ProposalService.process(manager, proposalCapsule);
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L168-174)
```java
        case ADAPTIVE_RESOURCE_LIMIT_TARGET_RATIO: {
          long ratio = 24 * 60 * entry.getValue();
          manager.getDynamicPropertiesStore().saveAdaptiveResourceLimitTargetRatio(ratio);
          manager.getDynamicPropertiesStore().saveTotalEnergyTargetLimit(
              manager.getDynamicPropertiesStore().getTotalEnergyLimit() / ratio);
          break;
        }
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L57-82)
```java
  public void applyBlock(BlockCapsule blockCapsule) {
    long blockNum = blockCapsule.getNum();
    long blockTime = blockCapsule.getTimeStamp();
    long nextMaintenanceTime = consensusDelegate.getNextMaintenanceTime();
    boolean flag = consensusDelegate.getNextMaintenanceTime() <= blockTime;
    if (flag) {
      if (blockNum != 1) {
        updateWitnessValue(beforeWitness);
        beforeMaintenanceTime = nextMaintenanceTime;
        doMaintenance();
        updateWitnessValue(currentWitness);
      }
      consensusDelegate.updateNextMaintenanceTime(blockTime);
      if (blockNum != 1) {
        //pbft sr msg
        pbftManager.srPrePrepare(blockCapsule, currentWitness,
            consensusDelegate.getNextMaintenanceTime());
      }
    }
    consensusDelegate.saveStateFlag(flag ? 1 : 0);
    //pbft block msg
    if (blockNum == 1) {
      nextMaintenanceTime = consensusDelegate.getNextMaintenanceTime();
    }
    pbftManager.blockPrePrepare(blockCapsule, nextMaintenanceTime);
  }
```
