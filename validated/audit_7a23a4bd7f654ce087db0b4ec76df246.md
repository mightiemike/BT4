### Title
Unbounded growth of `VotesStore` allows griefing of the consensus-critical `MaintenanceManager.doMaintenance()` vote-tally loop - (File: `consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java`)

### Summary
`VoteWitnessActuator` lets any account cast a vote for zero fee (`calcFee() == 0`), creating one entry per distinct voter address in the global `VotesStore`. There is no cap on the total number of distinct voter accounts, only a per-transaction cap on the number of witnesses voted for (`MAX_VOTE_NUMBER`) and a per-account cap on total vote weight (`sum <= tronPower`). Every maintenance cycle, `MaintenanceManager.doMaintenance()` unconditionally iterates **every single entry** in `VotesStore` via `countVote(votesStore)`, and additionally calls `votesStore.delete(next.getKey())` for each entry — all inside the block-application critical path executed identically by every full node. This is the same bug class as the `StakePet` report: an actor can cheaply mint an unbounded number of low-cost state entries (a "vote" row per account) with no minimum-cost gate, which are later force-iterated in full by a system routine that assumes the entry count stays small.

### Finding Description
`VoteWitnessActuator.validate()` only enforces:
- `votesCount > 0` and `<= MAX_VOTE_NUMBER` (limits per-transaction witness count, not total accounts)
- `sum(voteCount) * TRX_PRECISION <= tronPower` (limits vote weight per account, requiring only that the account has *some* frozen balance) [1](#0-0) 

`VoteWitnessActuator.calcFee()` returns `0`, meaning casting a vote (which materializes a new key in `VotesStore` keyed by the voter's address) costs nothing beyond ordinary bandwidth/energy consumption for the transaction itself. [2](#0-1) 

Each distinct funded account that freezes even a minimal amount of TRX (the minimum unit for `tronPower` is `TRX_PRECISION`, i.e. 1 TRX) via `FreezeBalanceV2Actuator` can vote, adding one row to `VotesStore`. Freezing does not burn funds — it is fully recoverable later — so the marginal cost of creating a new voter entry is effectively just the tiny bandwidth/account-activation cost plus temporarily-locked (not lost) TRX. [3](#0-2) 

Every maintenance cycle (roughly every 6 hours, driven automatically by `applyBlock` on every full node, not opt-in), `MaintenanceManager.doMaintenance()` calls `countVote(votesStore)`, which does a full linear scan of `VotesStore` and, for every entry, iterates its `oldVotes`/`newVotes` lists and deletes the row: [4](#0-3) [5](#0-4) 

This scan is unconditional and unbounded — the loop runs against however many entries exist in `VotesStore`, with no cap, batching, or amortization across blocks. Since this code runs inside `applyBlock`, i.e. on the consensus-critical block-processing path shared by every full node (see the call site): [6](#0-5) 

an attacker who cheaply mass-creates many funded accounts, each freezing a minimal amount and casting one vote, can inflate `VotesStore` to an arbitrarily large size before a maintenance boundary is crossed, causing every full node in the network to perform an unbounded amount of work at that exact moment.

### Impact Explanation
Because `doMaintenance()` executes synchronously as part of applying the block that crosses the maintenance-time boundary, an inflated `VotesStore` directly increases the CPU/time cost of block validation for every node on the network at that moment — a network-wide denial-of-service / degraded-availability condition rather than a per-caller gas exhaustion. In the worst case this could slow down or stall block production/validation across the network at maintenance boundaries, which is more severe than the original `StakePet` griefing report because it affects consensus-critical, node-wide processing rather than a single dApp's optional maintenance call.

### Likelihood Explanation
Likelihood is moderate: this requires the attacker to fund and activate a very large number of accounts, and to freeze at least 1 TRX per account, all of which is capital-intensive (although fully recoverable) and consumes bandwidth for each freeze/vote transaction. There is no protocol-level cap analogous to `MAX_VOTE_NUMBER` limiting the *total number of distinct voter accounts* system-wide, so the attack is bounded only by the attacker's available capital and time to submit transactions, not by any protocol defense. This is directly analogous to the reported bug class (missing minimum-cost gate on an unboundedly-growable, later fully-iterated store) and is reachable purely through normal broadcast transactions (`FreezeBalanceV2Contract` + `VoteWitnessContract`) from unprivileged accounts.

### Recommendation
- Introduce an economically meaningful cost or cap for adding new entries to `VotesStore` — e.g., a minimum vote-weight threshold well above a token amount, or a small non-refundable fee for a first-time vote from a new voter, mirroring the recommended "minimum deposit" mitigation from the referenced report.
- Alternatively, bound the work done inside `doMaintenance()`/`countVote()` per maintenance cycle (e.g., process `VotesStore` incrementally across multiple blocks or cap the number of entries processed per call), so a large backlog cannot be forced into a single block-processing pass.
- Add monitoring/alerting on `VotesStore` size growth and consider pruning/aging out zero-weight or stale voter entries.

### Proof of Concept
1. Programmatically create N funded accounts (e.g., via `TransferContract`, minimal TRX each).
2. For each account, broadcast `FreezeBalanceV2Contract` freezing the minimum unit (1 TRX) for `BANDWIDTH` or `ENERGY`.
3. For each account, broadcast `VoteWitnessContract` with `votesCount = 1` for any existing witness — validated successfully since `sum * TRX_PRECISION <= tronPower` holds for `tronPower = 1 TRX`.
4. This inserts one row per account into `VotesStore` via `VoteWitnessActuator.countVoteAccount()` (`actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java:152-191`).
5. Repeat for a large N (e.g., hundreds of thousands) before the next maintenance boundary.
6. Observe that the block crossing the maintenance boundary triggers `MaintenanceManager.doMaintenance()` → `countVote(votesStore)`, which must iterate and delete all N entries synchronously (`consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java:89-192`), increasing block-processing latency for every full node validating that block.

Note: I was not able to execute this PoC or measure actual timing impact/thresholds within this environment (no runtime access), so the magnitude of the slowdown (whether it is severe enough to stall block production) is not empirically confirmed here and would need to be validated with a running testnet/benchmark by a Devin session with full node access.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L89-143)
```java
    if (contract.getVotesCount() == 0) {
      throw new ContractValidateException(
          "VoteNumber must more than 0");
    }
    int maxVoteNumber = MAX_VOTE_NUMBER;
    if (contract.getVotesCount() > maxVoteNumber) {
      throw new ContractValidateException(
          "VoteNumber more than maxVoteNumber " + maxVoteNumber);
    }
    try {
      Iterator<Vote> iterator = contract.getVotesList().iterator();
      Long sum = 0L;
      while (iterator.hasNext()) {
        Vote vote = iterator.next();
        byte[] witnessCandidate = vote.getVoteAddress().toByteArray();
        if (!DecodeUtil.addressValid(witnessCandidate)) {
          throw new ContractValidateException("Invalid vote address!");
        }
        long voteCount = vote.getVoteCount();
        if (voteCount <= 0) {
          throw new ContractValidateException("vote count must be greater than 0");
        }
        String readableWitnessAddress = StringUtil.createReadableString(vote.getVoteAddress());
        if (!accountStore.has(witnessCandidate)) {
          throw new ContractValidateException(
              ACCOUNT_EXCEPTION_STR + readableWitnessAddress + NOT_EXIST_STR);
        }
        if (!witnessStore.has(witnessCandidate)) {
          throw new ContractValidateException(
              WITNESS_EXCEPTION_STR + readableWitnessAddress + NOT_EXIST_STR);
        }
        sum = LongMath.checkedAdd(sum, vote.getVoteCount());
      }

      AccountCapsule accountCapsule = accountStore.get(ownerAddress);
      if (accountCapsule == null) {
        throw new ContractValidateException(
            ACCOUNT_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
      }

      long tronPower;
      DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
      if (dynamicStore.supportAllowNewResourceModel()) {
        tronPower = accountCapsule.getAllTronPower();
      } else {
        tronPower = accountCapsule.getTronPower();
      }

      sum = LongMath
          .checkedMultiply(sum, TRX_PRECISION); //trx -> drop. The vote count is based on TRX
      if (sum > tronPower) {
        throw new ContractValidateException(
            "The total number of votes[" + sum + "] is greater than the tronPower[" + tronPower
                + "]");
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L152-201)
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

  @Override
  public ByteString getOwnerAddress() throws InvalidProtocolBufferException {
    return any.unpack(VoteWitnessContract.class).getOwnerAddress();
  }

  @Override
  public long calcFee() {
    return 0;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L57-84)
```java
    long frozenBalance = freezeBalanceV2Contract.getFrozenBalance();
    long newBalance = accountCapsule.getBalance() - frozenBalance;

    switch (freezeBalanceV2Contract.getResource()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(frozenBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        dynamicStore.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(frozenBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        dynamicStore.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(frozenBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        dynamicStore.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        logger.debug("Resource Code Error.");
    }

    accountCapsule.setBalance(newBalance);
    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L89-103)
```java
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

    Map<ByteString, Long> countWitness = countVote(votesStore);
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L165-192)
```java
  private Map<ByteString, Long> countVote(VotesStore votesStore) {
    final Map<ByteString, Long> countWitness = Maps.newHashMap();
    Iterator<Entry<byte[], VotesCapsule>> dbIterator = votesStore.iterator();
    long sizeCount = 0;
    while (dbIterator.hasNext()) {
      Entry<byte[], VotesCapsule> next = dbIterator.next();
      VotesCapsule votes = next.getValue();
      votes.getOldVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        if (countWitness.containsKey(voteAddress)) {
          countWitness.put(voteAddress, countWitness.get(voteAddress) - voteCount);
        } else {
          countWitness.put(voteAddress, -voteCount);
        }
      });
      votes.getNewVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        if (countWitness.containsKey(voteAddress)) {
          countWitness.put(voteAddress, countWitness.get(voteAddress) + voteCount);
        } else {
          countWitness.put(voteAddress, voteCount);
        }
      });
      sizeCount++;
      votesStore.delete(next.getKey());
    }
```
