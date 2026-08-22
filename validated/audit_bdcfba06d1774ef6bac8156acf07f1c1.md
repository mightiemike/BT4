### Title
Proposal state transitions are not fully enforced, allowing approve/disapprove after a proposal is already resolved - (File: actuator/src/main/java/org/tron/core/actuator/ProposalApproveActuator.java)

### Summary
`Proposal` instances have an implied lifecycle of `PENDING -> APPROVED/DISAPPROVED` (set by `ProposalController.processProposal`) or `PENDING -> CANCELED` (set by `ProposalDeleteActuator`), as shown by the `Proposal.State` enum in `protocol/src/main/protos/core/Tron.proto`. However, `ProposalApproveActuator.validate()` only rejects a `CANCELED` state and an expired proposal — it never checks whether the proposal has already reached the terminal `APPROVED` or `DISAPPROVED` state. This mirrors the reported bug class: state transitions are implicit and not fully enforced, allowing more transitions than the intended sequence suggests.

### Finding Description
`ProposalController.processProposal` sets a proposal's state to `APPROVED` or `DISAPPROVED` once it "has expired" according to `hasExpired(nextMaintenanceTime)`: [1](#0-0) 

That check uses `manager.getDynamicPropertiesStore().getNextMaintenanceTime()`, i.e. the maintenance cycle time, not the actual chain time (`getLatestBlockHeaderTimestamp`) used elsewhere: [2](#0-1) 

Meanwhile, `ProposalApproveActuator.validate()` only guards against `now >= proposalCapsule.getExpirationTime()` and `State.CANCELED`, using the block header timestamp (`now`), not the proposal's actual processed state: [3](#0-2) 

There is no check for `State.APPROVED` or `State.DISAPPROVED` (the "processed" states, as defined by `ProposalCapsule.hasProcessed()`): [4](#0-3) 

`ProposalApproveActuator.execute()` then unconditionally adds/removes an approval and persists it, regardless of the proposal's already-processed state: [5](#0-4) 

Similarly, `ProposalDeleteActuator.validate()` only checks `expired` and `CANCELED`, but not `hasProcessed()`, so an already `APPROVED`/`DISAPPROVED` proposal can still be transitioned to `CANCELED`: [6](#0-5) 

Because `expirationTime` (set at proposal creation, independent of `nextMaintenanceTime`) can remain in the future even after the proposal has already been resolved at a maintenance cycle, there is a window where witnesses (via broadcast `ProposalApproveContract`/`ProposalDeleteContract` transactions) can mutate the `approvals` list or flip the state of a proposal whose committee decision (and side effects via `ProposalService.process`, which mutates `DynamicPropertiesStore` parameters) has already been executed.

### Impact Explanation
This is a state-machine enforcement gap in an on-chain governance actuator reachable via ordinary broadcast transactions (`ProposalApproveContract`, `ProposalDeleteContract`) from any witness account. While the direct side effect (`ProposalService.process`) is not re-triggered by further approvals (it only runs once, when `processProposal` transitions the state), the actuators allow inconsistent/undefined states to be recorded (e.g. approvals added to an `APPROVED`/`DISAPPROVED` proposal, or a resolved proposal later marked `CANCELED`). This corrupts the on-chain record of governance decisions and violates the invariant implied by `getState()`/`hasProcessed()`, complicating downstream tooling, auditing, and any future logic that assumes `APPROVED`/`DISAPPROVED` is terminal. It does not directly cause double-application of dynamic parameter changes (since `ProposalService.process` is only invoked from the single `processProposal` transition), so it is a data-integrity/consensus-record-corruption issue rather than a direct fund-loss bug.

### Likelihood Explanation
Any witness account can trigger this by broadcasting a `ProposalApproveContract` or `ProposalDeleteContract` for a proposal whose `expirationTime` has not yet passed but whose state has already been flipped to `APPROVED`/`DISAPPROVED` at an earlier maintenance cycle (since `hasExpired` in `ProposalController` is evaluated against `nextMaintenanceTime`, a different clock than the `expirationTime` check in the actuators). This requires no privileged access beyond being a registered witness, which any account can become, and requires no key compromise — it only requires crafting an approve/delete transaction against a resolved proposal ID.

### Recommendation
Enforce proposal state transitions explicitly:
- In `ProposalApproveActuator.validate()`, reject the operation if `proposalCapsule.hasProcessed()` (i.e., state is `APPROVED` or `DISAPPROVED`) in addition to the existing `CANCELED` and expiration checks.
- In `ProposalDeleteActuator.validate()`, similarly reject deletion if `proposalCapsule.hasProcessed()`.
- Consider unifying the "is this proposal still actionable" check into a single method on `ProposalCapsule` (e.g., `isPending()`) used consistently by `ProposalApproveActuator`, `ProposalDeleteActuator`, and `ProposalController`, so that `PENDING` is the only state from which `APPROVED`, `DISAPPROVED`, or `CANCELED` can be reached, and no further mutation is possible once a terminal state is reached.

### Proof of Concept
1. Create a proposal via `ProposalCreateContract`; note its `proposalId` and `expirationTime`.
2. Advance to a maintenance cycle where `nextMaintenanceTime >= proposalCapsule.getExpirationTime()`'s internal cutoff used by `ProposalController.processProposals` (i.e. `hasExpired(nextMaintenanceTime)` returns true) while the real chain time (`latestBlockHeaderTimestamp`) is still `< expirationTime`. `ProposalController.processProposal` sets the proposal state to `APPROVED` or `DISAPPROVED`.
3. As a witness, broadcast a `ProposalApproveContract` (or `ProposalDeleteContract`) for that same `proposalId`. `ProposalApproveActuator.validate()` (or `ProposalDeleteActuator.validate()`) only checks `now >= expirationTime` and `State.CANCELED` — both pass — so the transaction succeeds and mutates the `approvals` list or state on an already-resolved proposal, an unenforced/unexpected state transition.

### Citations

**File:** framework/src/main/java/org/tron/core/consensus/ProposalController.java (L61-93)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ProposalApproveActuator.java (L45-56)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ProposalApproveActuator.java (L115-132)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    ProposalCapsule proposalCapsule;
    try {
      proposalCapsule = proposalStore.
          get(ByteArray.fromLong(contract.getProposalId()));
    } catch (ItemNotFoundException ex) {
      throw new ContractValidateException(PROPOSAL_EXCEPTION_STR + contract.getProposalId()
          + NOT_EXIST_STR);
    }

    if (now >= proposalCapsule.getExpirationTime()) {
      throw new ContractValidateException(PROPOSAL_EXCEPTION_STR + contract.getProposalId()
          + "] expired");
    }
    if (proposalCapsule.getState() == State.CANCELED) {
      throw new ContractValidateException(PROPOSAL_EXCEPTION_STR + contract.getProposalId()
          + "] canceled");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ProposalCapsule.java (L129-132)
```java
  public boolean hasProcessed() {
    return this.proposal.getState().equals(State.DISAPPROVED) || this.proposal.getState()
        .equals(State.APPROVED);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ProposalDeleteActuator.java (L111-123)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    if (!proposalCapsule.getProposalAddress().equals(contract.getOwnerAddress())) {
      throw new ContractValidateException(PROPOSAL_EXCEPTION_STR + contract.getProposalId() + "] "
          + "is not proposed by " + readableOwnerAddress);
    }
    if (now >= proposalCapsule.getExpirationTime()) {
      throw new ContractValidateException(PROPOSAL_EXCEPTION_STR + contract.getProposalId()
          + "] expired");
    }
    if (proposalCapsule.getState() == State.CANCELED) {
      throw new ContractValidateException(PROPOSAL_EXCEPTION_STR + contract.getProposalId()
          + "] canceled");
    }
```
