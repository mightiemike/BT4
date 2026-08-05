## Analog Vulnerability Found

### Title
Proposal auto-approval when active witness set is empty due to missing minimum-threshold check - (File: `chainbase/src/main/java/org/tron/core/capsule/ProposalCapsule.java`)

### Summary
`ProposalCapsule.hasMostApprovals()` computes the approval threshold as a fraction of the current active-witness set size (`activeWitnesses.size() * 7 / 10`) with no floor/minimum check, mirroring the CoreDAO `GovHub` flaw where a governance decision's pass/fail threshold is derived purely from the size of a mutable member/voter set with no minimum-size enforcement.

### Finding Description
`ProposalCapsule.hasMostApprovals(List<ByteString> activeWitnesses)` returns `true` when the number of matching approvals is `>= activeWitnesses.size() * 7 / 10`: [1](#0-0) 

If `activeWitnesses` is empty, the threshold evaluates to `0`, so `count >= 0` is trivially `true` for **any** proposal — including one with zero approvals. `ProposalController.processProposal()` calls this method directly with the current active-witness list fetched from the `WitnessScheduleStore` and, if it returns `true`, immediately applies the proposal's dynamic parameters and marks it `APPROVED`: [2](#0-1) 

The active-witness list itself is entirely rebuilt from `ConsensusDelegate.getAllWitnesses()` during each maintenance cycle in `MaintenanceManager.doMaintenance()`, with no check that the resulting witness set is non-empty before it is persisted via `dposService.updateWitness(...)`: [3](#0-2) [4](#0-3) 

This is the same root-cause pattern as the CoreDAO `GovHub` bug: a vote/approval-threshold calculation is driven by a mutable set's size, and the code never enforces a minimum size for that set, so an empty (or near-empty) set flips the pass/fail outcome for governance actions in an unintended way. In java-tron's case the effect is inverted relative to GovHub — instead of proposals being wrongly `DEFEATED`, they are wrongly `APPROVED` with zero real votes — which is arguably a stronger impact since it lets committee-parameter changes ("dynamic parameters" such as fees, account upgrade costs, energy prices, etc., processed by `ProposalService.process`) be applied to the live chain state without genuine witness consensus.

### Impact Explanation
If the active-witness set is ever empty (e.g., during early bootstrap of a private/consortium network before any witness has been registered/activated, or any other state transiently producing an empty active set), every pending proposal — even with zero approvals — is immediately treated as fully approved and its parameters are applied via `setDynamicParameters`/`ProposalService.process`. This is a critical governance/invalid-state divergence: chain-critical economic and consensus parameters could be mutated without any witness authorization.

### Likelihood Explanation
Exploitation requires the network to reach a state where `WitnessScheduleStore`'s active-witness list is empty at the moment `processProposals()` runs. On a fully bootstrapped mainnet with `MAX_ACTIVE_WITNESS_NUM` witnesses this is unlikely under normal operation, but it is a realistic risk for freshly deployed private/consortium chains (where `genesis.block.witnesses` can legitimately be configured empty, as `GenesisBlock.getDefault()` demonstrates) or any other operational condition where `getAllWitnesses()` transiently returns an empty/near-empty collection before the first maintenance cycle populates it.

### Recommendation
Add an explicit minimum-size guard in `ProposalCapsule.hasMostApprovals()` (and/or before invoking it in `ProposalController.processProposal()`) so that an empty or under-sized `activeWitnesses` list causes the proposal to be treated as not approved (e.g., default to `DISAPPROVED`) rather than trivially satisfying the `count >= size * 7/10` threshold.

### Proof of Concept
1. Deploy/initialize a chain instance where the active witness set becomes empty (e.g., before genesis witnesses are registered, or by clearing `WitnessScheduleStore`'s active witness list).
2. Create a `ProposalCapsule` with `State.PENDING` and zero entries in `approvals`.
3. Call `ProposalController.processProposal(proposalCapsule)`.
4. Observe `proposalCapsule.hasMostApprovals(activeWitnesses)` returns `true` (since `activeWitnesses.size() == 0` → threshold `0`, and `approvals.size() == 0 >= 0`), causing the controller to call `setDynamicParameters(proposalCapsule)` and set the proposal state to `APPROVED` — this mirrors `testHasMostApprovals`/`testProcessProposal` in `framework/src/test/java/org/tron/core/witness/ProposalControllerTest.java`, but with an empty `activeWitnesses` list instead of a populated one.

### Citations

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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L103-130)
```java
    Map<ByteString, Long> countWitness = countVote(votesStore);
    if (!countWitness.isEmpty()) {
      List<ByteString> currentWits = consensusDelegate.getActiveWitnesses();

      List<ByteString> newWitnessAddressList = new ArrayList<>();
      consensusDelegate.getAllWitnesses()
          .forEach(witnessCapsule -> newWitnessAddressList.add(witnessCapsule.getAddress()));

      countWitness.forEach((address, voteCount) -> {
        byte[] witnessAddress = address.toByteArray();
        WitnessCapsule witnessCapsule = consensusDelegate.getWitness(witnessAddress);
        if (witnessCapsule == null) {
          logger.warn("Witness capsule is null. address is {}", Hex.toHexString(witnessAddress));
          return;
        }
        AccountCapsule account = consensusDelegate.getAccount(witnessAddress);
        if (account == null) {
          logger.warn("Witness account is null. address is {}", Hex.toHexString(witnessAddress));
          return;
        }
        witnessCapsule.setVoteCount(witnessCapsule.getVoteCount() + voteCount);
        consensusDelegate.saveWitness(witnessCapsule);
        logger.info("address is {} , countVote is {}", witnessCapsule.createReadableString(),
            witnessCapsule.getVoteCount());
      });

      dposService.updateWitness(newWitnessAddressList);

```

**File:** consensus/src/main/java/org/tron/consensus/dpos/DposService.java (L178-186)
```java
  public void updateWitness(List<ByteString> list) {
    consensusDelegate.sortWitness(list);
    if (list.size() > MAX_ACTIVE_WITNESS_NUM) {
      consensusDelegate
          .saveActiveWitnesses(list.subList(0, MAX_ACTIVE_WITNESS_NUM));
    } else {
      consensusDelegate.saveActiveWitnesses(list);
    }
  }
```
