### Title
Vote-timing exploit around maintenance cycle boundary allows minimal-duration voting to capture full-cycle SR rewards - (File: `actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java`, `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java`, `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
java-tron's SR voting reward system accrues rewards on a per-cycle basis (one cycle = one `maintenanceTimeInterval`, default 6 hours). A user's vote weight for an entire cycle is determined only by the vote state present at the moment `MaintenanceManager.doMaintenance()` runs, with no time-weighting for how long the vote was actually held during the cycle. This is the same bug class as the reported `Pledge.sol` issue: a user can time a stake/vote transaction to land immediately before the periodic snapshot, then reverse it immediately after, capturing a full cycle's reward while bearing almost none of the opportunity cost of holding the position.

### Finding Description
Reward computation is cycle-based. `VoteRewardUtil.computeReward`/`MortgageService.computeReward` compute reward as the delta of a witness's accumulated per-vote reward index (`Vi`) between `beginCycle-1` and `endCycle-1`, multiplied by the account's `voteCount`: [1](#0-0) 

The `Vi` index itself is only updated once per cycle, inside `MaintenanceManager.doMaintenance()`, using the witness's `voteCount` value *at the instant the maintenance job executes* — not any time-weighted average of votes held throughout the cycle: [2](#0-1) 

A user can submit a `VoteWitnessContract` transaction (via `VoteWitnessActuator.countVoteAccount`) at any point in the cycle — including a fraction of a second before the maintenance boundary — and it is unconditionally recorded into `AccountCapsule`'s vote list with no regard for holding duration: [3](#0-2) 

`countVoteAccount` also settles/starts reward accounting via `mortgageService.withdrawReward(ownerAddress)` right before votes are replaced, which sets `beginCycle`/`endCycle` bookkeeping for the account: [4](#0-3) [5](#0-4) 

Since `doMaintenance()` runs on a fixed periodic schedule (`maintenanceTimeInterval`, configured in `reference.conf`/`config.conf`), the exact timing of the next maintenance execution is publicly known/predictable ahead of time from chain state (`getNextMaintenanceTime` in `DynamicPropertiesStore`). This is the on-chain analog to the off-chain "recurring timestamp, once daily" snapshot described in the report: an attacker can vote right before the snapshot boundary and clear the vote (or unfreeze) right after the cycle rolls over, receiving one full cycle's worth of `Vi`-delta reward for holding TRON Power for only seconds, rather than the full ~6-hour cycle.

### Impact Explanation
An account can receive a full cycle's SR voting reward while having economically committed frozen TRX/voting power for only a negligible fraction of that cycle. If reward-per-cycle exceeds the transaction fee cost of two transactions (vote + clear-vote/withdraw), this is a repeatable, risk-free profit extraction from the SR reward pool, effectively diluting rewards owed to genuine long-term voters and allowing gaming of the voting-reward economics — an accounting/incentive-integrity issue analogous to the reported finding, though the per-cycle reward magnitude (rather than exceeding gas) determines actual profitability.

### Likelihood Explanation
The next maintenance time is deterministic and readable from chain state ahead of time, so timing a vote transaction to land just before the boundary is straightforward and requires no privileged access — any account holding frozen TRX (TRON Power) can attempt it. Likelihood of *some* users exploiting this is moderate; it is bounded by whether cycle reward magnitude exceeds the two transactions' fees, which the maintainers themselves flagged as the key variable in the original report.

### Recommendation
Introduce time-weighting into the reward accrual so a vote must be held for a minimum duration within the cycle (or accrue reward proportionally to holding duration within the cycle) before it counts toward that cycle's `Vi` delta, rather than counting full-cycle reward based solely on the vote state sampled at the `doMaintenance()` instant. Alternatively, require votes to be recorded some minimum number of blocks/time before the maintenance boundary to be eligible for that cycle's reward (a "vote lock-in" analogous to increasing snapshot variability recommended in the original report).

### Proof of Concept
1. Attacker observes `DynamicPropertiesStore.getNextMaintenanceTime()` to know the upcoming cycle boundary.
2. Just before that boundary, attacker submits a `VoteWitnessContract` transaction, causing `VoteWitnessActuator.countVoteAccount` to register full vote weight against a witness.
3. `MaintenanceManager.doMaintenance()` executes at the boundary, calling `delegationStore.accumulateWitnessVi(curCycle, witness, witness.getVoteCount())`, which includes the attacker's just-added vote weight for the entire just-closed cycle.
4. Immediately after the cycle rolls over, attacker submits another `VoteWitnessContract` with an empty vote list (or unfreezes), clearing their vote.
5. Attacker calls `withdrawReward`/`queryReward`, which computes `computeReward(beginCycle, endCycle, ...)` using the full cycle's `Vi` delta, granting a full cycle's proportional reward for holding the vote only briefly.

Note: I could not directly execute this scenario in a live environment; the analysis is based on static code review of the actuator, `MaintenanceManager`, and `MortgageService`/`DelegationStore` reward-accrual logic, and the default `maintenanceTimeInterval` value could not be fully confirmed from the indexed `reference.conf` snippet.

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L89-134)
```java
  public void withdrawReward(byte[] address) {
    if (!dynamicPropertiesStore.allowChangeDelegation()) {
      return;
    }
    AccountCapsule accountCapsule = accountStore.get(address);
    long beginCycle = delegationStore.getBeginCycle(address);
    long endCycle = delegationStore.getEndCycle(address);
    long currentCycle = dynamicPropertiesStore.getCurrentCycleNumber();
    long reward = 0;
    if (beginCycle > currentCycle || accountCapsule == null) {
      return;
    }
    if (beginCycle == currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        return;
      }
    }
    //withdraw the latest cycle reward
    if (beginCycle + 1 == endCycle && beginCycle < currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        reward = computeReward(beginCycle, endCycle, account);
        adjustAllowance(address, reward);
        reward = 0;
        logger.info("Latest cycle reward {}, {}.", beginCycle, account.getVotesList());
      }
      beginCycle += 1;
    }
    //
    endCycle = currentCycle;
    if (CollectionUtils.isEmpty(accountCapsule.getVotesList())) {
      delegationStore.setBeginCycle(address, endCycle + 1);
      return;
    }
    if (beginCycle < endCycle) {
      reward += computeReward(beginCycle, endCycle, accountCapsule);
      adjustAllowance(address, reward);
    }
    delegationStore.setBeginCycle(address, endCycle);
    delegationStore.setEndCycle(address, endCycle + 1);
    delegationStore.setAccountVote(endCycle, address, accountCapsule);
    logger.info("Adjust {} allowance {}, now currentCycle {}, beginCycle {}, endCycle {}, "
            + "account vote {}.", Hex.toHexString(address), reward, currentCycle,
        beginCycle, endCycle, accountCapsule.getVotesList());
  }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L215-228)
```java
    if (beginCycle < endCycle) {
      for (Pair<byte[], Long>  vote : srAddresses) {
        byte[] srAddress = vote.getKey();
        BigInteger beginVi = delegationStore.getWitnessVi(beginCycle - 1, srAddress);
        BigInteger endVi = delegationStore.getWitnessVi(endCycle - 1, srAddress);
        BigInteger deltaVi = endVi.subtract(beginVi);
        if (deltaVi.signum() <= 0) {
          continue;
        }
        long userVote = vote.getValue();
        reward += deltaVi.multiply(BigInteger.valueOf(userVote))
            .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
      }
    }
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L94-101)
```java
    DynamicPropertiesStore dynamicPropertiesStore = consensusDelegate.getDynamicPropertiesStore();
    DelegationStore delegationStore = consensusDelegate.getDelegationStore();
    if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
      long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L152-164)
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

```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L181-190)
```java
    voteContract.getVotesList().forEach(vote -> {
      logger.debug("countVoteAccount, address[{}]",
          ByteArray.toHexString(vote.getVoteAddress().toByteArray()));

      votesCapsule.addNewVotes(vote.getVoteAddress(), vote.getVoteCount());
      accountCapsule.addVotes(vote.getVoteAddress(), vote.getVoteCount());
    });

    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
    votesStore.put(ownerAddress, votesCapsule);
```
