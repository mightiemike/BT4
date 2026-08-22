### Title
Proposal pass-threshold denominator (`activeWitnesses`) is not snapshotted per proposal, allowing dynamic witness churn to flip approval outcomes - (`framework/src/main/java/org/tron/core/consensus/ProposalController.java`)

### Summary
The Party `passThresholdBps` bug class is "a mutable, chain-wide governance parameter is read live at decision time instead of being cached/snapshotted when the proposal was created, so changing the parameter via a separate transaction retroactively changes whether a pending vote passes." The closest reachable analog in java-tron is `ProposalController.processProposal()` / `ProposalCapsule.hasMostApprovals()`, where the "total voting power" equivalent (`activeWitnesses`) is fetched live from `WitnessScheduleStore` at proposal-processing time rather than being fixed at proposal-creation time.

### Finding Description
A witness proposal (`ProposalCreateContract`) is approved over time via `ProposalApproveContract` broadcasts, which just add/remove the sender's address to `proposal.approvals` [1](#0-0) . No snapshot of the active witness set (the analog of `totalVotingPower`) is taken when the proposal is created — `ProposalCapsule` only stores `parameters`, `approvals`, `createTime`, and `expirationTime` [2](#0-1) .

When the proposal is finally processed (at expiration, during maintenance), `ProposalController.processProposal` fetches the *current* active witness list live and passes it into `hasMostApprovals`: [3](#0-2) 

`hasMostApprovals` then filters approvals to only those addresses still present in the just-fetched `activeWitnesses` list, and computes the required threshold as `activeWitnesses.size() * 7 / 10` — using the *current* witness set size as the denominator, not the one in effect when the votes were cast: [4](#0-3) 

The active witness set is not static — it is recomputed every maintenance cycle by `MaintenanceManager.doMaintenance()`/`updateWitnessValue`, which reassigns `srList` based on the latest vote tally, and can add/remove SRs each cycle: [5](#0-4) . `processProposals` itself is only invoked against `nextMaintenanceTime`-based expiration, so a proposal's expiration and the active-SR-set recomputation are tightly coupled to the same maintenance boundary [6](#0-5) .

Consequently, two identical sets of approvals recorded for the same proposal can be judged pass/fail differently purely because the active-SR set (denominator) shifted between the time witnesses cast their approvals and the time the proposal is actually evaluated — exactly the same class of bug as caching `totalVotingPower`/`passThresholdBps` at proposal-creation time but instead reading the live, mutable value at decision time.

### Impact Explanation
Because `hasMostApprovals` recomputes both the approving-witness filter and the 70% threshold denominator from the live `activeWitnesses` list rather than a value fixed at proposal creation, the passing of consensus-parameter proposals (which control chain-wide values such as `TRANSACTION_FEE`, `ENERGY_FEE`, `WITNESS_PAY_PER_BLOCK`, etc., applied via `ProposalService.process`) can be manipulated by witness set churn between vote casting and processing time — an SR that approved a proposal may see its vote silently discounted (filtered out) if it is voted out of the active set before the maintenance cycle in which the proposal is processed, and the required approval count itself shifts with the size of the new active set. This can cause a proposal that should have passed to fail, or vice versa, corrupting on-chain governance parameter changes that affect fee/reward accounting across the whole network.

### Likelihood Explanation
Witness set changes occur automatically every maintenance cycle based on ordinary TRX-holder voting (a normal, unprivileged, continuously-occurring network activity), and proposal processing is deterministically tied to maintenance-cycle boundaries, so the misalignment window is a routine occurrence rather than a rare edge case. No privileged access or malicious node behavior is required — any SR whose seat changes near a proposal's expiration boundary, or any coordinated voting shift among TRX holders, can trigger the divergence.

### Recommendation
Snapshot the active witness set (or at least its size, as the denominator) into the `Proposal` protobuf when the proposal is created (analogous to caching `totalVotingPower`), and use that cached value in `hasMostApprovals` for both the eligible-approver filter and the 70% threshold computation, rather than re-fetching `WitnessScheduleStore.getActiveWitnesses()` at processing time.

### Proof of Concept
1. Witness set `W0` (27 SRs) is active. A `ProposalCreateContract` is submitted, creating proposal `P`.
2. 18 of the 27 SRs in `W0` approve `P` via `ProposalApproveContract` (69%+ of `activeWitnesses.size()`), satisfying the 70% threshold for `W0`.
3. Before `P` expires/is processed, a maintenance cycle runs and `updateWitnessValue` replaces several of the approving SRs with new SRs (`W1`), per `MaintenanceManager.doMaintenance()`.
4. When `ProposalController.processProposal(P)` finally runs, `hasMostApprovals(W1)` filters out the approvals from SRs no longer in `W1` and recomputes the threshold as `W1.size() * 7/10`, potentially causing a previously-passing proposal to be marked `DISAPPROVED` (or, in the reverse scenario, allowing a previously-insufficient proposal to newly pass) purely due to witness churn unrelated to the actual proposal's merit — mirroring Scenario A/B from the Party report where a governance threshold denominator changes mid-flight and flips an unrelated proposal's outcome.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ProposalApproveActuator.java (L44-56)
```java
    ProposalStore proposalStore = chainBaseManager.getProposalStore();
    try {
      final ProposalApproveContract proposalApproveContract =
          this.any.unpack(ProposalApproveContract.class);
      ProposalCapsule proposalCapsule = proposalStore
          .get(ByteArray.fromLong(proposalApproveContract.getProposalId()));
      ByteString committeeAddress = proposalApproveContract.getOwnerAddress();
      if (proposalApproveContract.getIsAddApproval()) {
        proposalCapsule.addApproval(committeeAddress);
      } else {
        proposalCapsule.removeApproval(committeeAddress);
      }
      proposalStore.put(proposalCapsule.createDbKey(), proposalCapsule);
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ProposalCapsule.java (L65-93)
```java
  public Map<Long, Long> getParameters() {
    return this.proposal.getParametersMap();
  }

  public void setParameters(Map<Long, Long> parameters) {
    this.proposal = this.proposal.toBuilder()
        .putAllParameters(parameters)
        .build();
  }

  public long getExpirationTime() {
    return this.proposal.getExpirationTime();
  }

  public void setExpirationTime(long time) {
    this.proposal = this.proposal.toBuilder()
        .setExpirationTime(time)
        .build();
  }

  public long getCreateTime() {
    return this.proposal.getCreateTime();
  }

  public void setCreateTime(long time) {
    this.proposal = this.proposal.toBuilder()
        .setCreateTime(time)
        .build();
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ProposalCapsule.java (L156-168)
```java
  public boolean hasMostApprovals(List<ByteString> activeWitnesses) {
    long count = this.proposal.getApprovalsList().stream()
        .filter(witness -> activeWitnesses.contains(witness)).count();
    if (count != this.proposal.getApprovalsCount()) {
      List<ByteString> InvalidApprovalList = this.proposal.getApprovalsList().stream()
          .filter(witness -> !activeWitnesses.contains(witness)).collect(Collectors.toList());
      logger.info("InvalidApprovalList:" + getAddressStringList(InvalidApprovalList));
    }
    if (activeWitnesses.size() != MAX_ACTIVE_WITNESS_NUM) {
      logger.info("activeWitnesses size = {}", activeWitnesses.size());
    }
    return count >= activeWitnesses.size() * 7 / 10;
  }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalController.java (L26-72)
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

      if (proposalCapsule.hasProcessed()) {
        logger
            .info("Proposal has processed, id:[{}], skip it and before it",
                proposalCapsule.getID());
        //proposals with number less than this one, have been processed before
        break;
      }

      if (proposalCapsule.hasCanceled()) {
        logger.info("Proposal has canceled, id:[{}], skip it", proposalCapsule.getID());
        proposalNum--;
        continue;
      }

      long currentTime = manager.getDynamicPropertiesStore().getNextMaintenanceTime();
      if (proposalCapsule.hasExpired(currentTime)) {
        processProposal(proposalCapsule);
        proposalNum--;
        continue;
      }

      proposalNum--;
      logger.info("Proposal has not expired, id:[{}], skip it", proposalCapsule.getID());
    }
    logger.info("Processing proposals done, oldest proposal[{}]", proposalNum);
  }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalController.java (L74-93)
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
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L84-101)
```java
  private void updateWitnessValue(List<ByteString> srList) {
    srList.clear();
    srList.addAll(consensusDelegate.getActiveWitnesses());
  }

  public void doMaintenance() {
    VotesStore votesStore = consensusDelegate.getVotesStore();

    tryRemoveThePowerOfTheGr();

    DynamicPropertiesStore dynamicPropertiesStore = consensusDelegate.getDynamicPropertiesStore();
    DelegationStore delegationStore = consensusDelegate.getDelegationStore();
    if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
      long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
```
