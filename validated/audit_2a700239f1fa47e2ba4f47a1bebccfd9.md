### Title
DPoS voting can be front-run to seize a disproportionate share of the same-cycle standby-witness allowance - ([File: consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java])

### Summary
`MaintenanceManager.doMaintenance()` tallies all votes cast during a cycle, immediately updates each witness's cumulative `voteCount`, and — using that just-updated vote count — calls `IncentiveManager.reward()` in the same method invocation to split the fixed `WitnessStandbyAllowance` pool proportionally among the top-127 witnesses. Because vote counting and reward payout happen atomically in the same maintenance step, a vote transaction broadcast immediately before the block that triggers maintenance is folded into the very snapshot used to calculate that cycle's payout, exactly mirroring the `returnFunds()` pattern where a `stake()` submitted right before a share-price-increasing event captures value the staker never economically earned.

### Finding Description
`MaintenanceManager.doMaintenance()` first computes vote deltas from `votesStore` via `countVote()`, then applies them directly to each witness's persistent `voteCount`: [1](#0-0) 

Immediately afterward, still inside the same `doMaintenance()` call, `incentiveManager.reward(newWitnessAddressList)` is invoked using the *just-updated* vote counts: [2](#0-1) 

`IncentiveManager.reward()` then distributes the fixed `WitnessStandbyAllowance` pool proportionally to each witness's `voteCount` at that exact instant: [3](#0-2) 

Votes are recorded by `VoteWitnessActuator.countVoteAccount()`, which requires only that the voter hold enough frozen TRON Power (`FreezeBalanceV2Actuator` / `FreezeBalanceActuator` for `TRON_POWER`) — it does not require the vote to have existed for any minimum duration before it is counted: [4](#0-3) 

This is structurally the same bug class as `SafetyModule.returnFunds()`: a value-distributing operation (`incentiveManager.reward`) reads a state variable (`voteCount`) that can be mutated by a normal, unprivileged, broadcast transaction (`VoteWitnessContract`) immediately before the distribution executes, letting the actor capture a share of the payout without having held the underlying "stake" (frozen TRON Power / vote weight) for any meaningful period.

### Impact Explanation
A witness (or a coordinated voter who self-votes for a witness they control) can inflate `voteCount` right before the maintenance block executes to grab a larger slice of the `WitnessStandbyAllowance`, diluting the payout legitimately earned by other standby witnesses who held votes for the full cycle. This is an asset/accounting-fairness issue affecting the DPoS reward distribution rather than direct fund theft from third parties, so impact is moderate rather than critical.

### Likelihood Explanation
Exploitation requires: (1) enough frozen TRON Power to place a witness in the standby set, (2) precise timing of a `VoteWitnessContract` transaction to land in the last block(s) of a maintenance cycle, and (3) the total allowance pool being large enough, relative to existing vote weight, to make the maneuver profitable versus the TRX opportunity cost of freezing. This is a narrow, timing-dependent opportunity similar in likelihood to the original report (low but non-zero, since maintenance cycle boundaries are publicly predictable via `getNextMaintenanceTime()`).

### Recommendation
Snapshot each witness's `voteCount` (and vote weights used for reward apportionment) at the *start* of the maintenance cycle rather than after applying votes accumulated during that same cycle, or require votes to have existed for at least one full cycle before they contribute to `IncentiveManager.reward()`'s apportionment, matching the delayed-cooldown design already used for the Vi-based voter reward algorithm (`RewardViCalService`/`MortgageService`, which uses `beginCycle`/`endCycle` accounting rather than instantaneous vote count).

### Proof of Concept
1. Attacker controls (or colludes with) a witness address near the bottom of the standby set.
2. Attacker freezes TRX for `TRON_POWER` via `FreezeBalanceV2Actuator`.
3. Just before the block whose timestamp crosses `nextMaintenanceTime` (predictable via `MaintenanceManager.applyBlock` / `ConsensusDelegate.getNextMaintenanceTime()`), the attacker broadcasts a `VoteWitnessContract` transaction voting for the controlled witness.
4. `MaintenanceManager.doMaintenance()` processes this vote in `countVote()`, adds it to the witness's `voteCount`, then immediately calls `incentiveManager.reward()`, which pays the witness a share of `WitnessStandbyAllowance` proportional to the inflated `voteCount` — for a "stake" that existed for at most one block.

Note: I was unable to fully verify from the indexed code whether any cooldown/lock period prevents immediately reversing the frozen `TRON_POWER` used for this vote after the reward is captured (the `UnfreezeBalanceV2Actuator` unlock-timing logic was not fully retrievable in this session), so the "unstake immediately afterward" portion of the analog is asserted with lower confidence than the front-run/reward-capture mechanism itself.

### Citations

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L103-127)
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
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L129-131)
```java
      dposService.updateWitness(newWitnessAddressList);

      incentiveManager.reward(newWitnessAddressList);
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java (L20-43)
```java
  public void reward(List<ByteString> witnesses) {
    if (consensusDelegate.allowChangeDelegation()) {
      return;
    }
    if (witnesses.size() > WITNESS_STANDBY_LENGTH) {
      witnesses = witnesses.subList(0, WITNESS_STANDBY_LENGTH);
    }
    long voteSum = 0;
    for (ByteString witness : witnesses) {
      voteSum += consensusDelegate.getWitness(witness.toByteArray()).getVoteCount();
    }
    if (voteSum <= 0) {
      return;
    }
    long totalPay = consensusDelegate.getWitnessStandbyAllowance();
    for (ByteString witness : witnesses) {
      byte[] address = witness.toByteArray();
      long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay
          / voteSum));
      AccountCapsule accountCapsule = consensusDelegate.getAccount(address);
      accountCapsule.setAllowance(accountCapsule.getAllowance() + pay);
      consensusDelegate.saveAccount(accountCapsule);
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L152-191)
```java
  private void countVoteAccount(VoteWitnessContract voteContract) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    VotesStore votesStore = chainBaseManager.getVotesStore();
    MortgageService mortgageService = chainBaseManager.getMortgageService();
    byte[] ownerAddress = voteContract.getOwnerAddress().toByteArray();

    VotesCapsule votesCapsule;

    //
    mortgageService.withdrawReward(ownerAddress);

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);

    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    if (dynamicStore.supportAllowNewResourceModel()
        && accountCapsule.oldTronPowerIsNotInitialized()) {
      accountCapsule.initializeOldTronPower();
    }

    if (!votesStore.has(ownerAddress)) {
      votesCapsule = new VotesCapsule(voteContract.getOwnerAddress(),
          accountCapsule.getVotesList());
    } else {
      votesCapsule = votesStore.get(ownerAddress);
    }

    accountCapsule.clearVotes();
    votesCapsule.clearNewVotes();

    voteContract.getVotesList().forEach(vote -> {
      logger.debug("countVoteAccount, address[{}]",
          ByteArray.toHexString(vote.getVoteAddress().toByteArray()));

      votesCapsule.addNewVotes(vote.getVoteAddress(), vote.getVoteCount());
      accountCapsule.addVotes(vote.getVoteAddress(), vote.getVoteCount());
    });

    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
    votesStore.put(ownerAddress, votesCapsule);
  }
```
