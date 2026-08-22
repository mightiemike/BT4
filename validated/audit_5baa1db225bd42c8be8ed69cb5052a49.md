### Title
Reward-Snapshot Timing Attack: Voters Can Withdraw a Witness's Full-Cycle Vote After Only Briefly Holding It, Siphoning Rewards From Long-Term Voters - (File: chainbase/src/main/java/org/tron/core/service/MortgageService.java, chainbase/src/main/java/org/tron/core/service/RewardViCalService.java, consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java, actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java)

### Summary
TRON's SR-vote reward mechanism computes rewards per full cycle using a per-witness "Vi" (reward-per-vote accumulator), analogous to `xToken`'s share-based reward pool in the referenced report. The per-account reward snapshot (`setAccountVote`) is taken at the moment a voter changes/removes their vote, capturing their *full* vote weight for the cycle even if the voter only held that vote for a fraction of the cycle. Combined with the fact that the witness-level vote-count denominator used for a cycle's Vi calculation is fixed at the *start* of that cycle from the aggregate of all vote changes made during the previous cycle, a voter can add votes right before a maintenance boundary and remove them immediately after, yet still be credited via the stored snapshot with the entire next cycle's reward rate multiplied by their full vote amount — exactly the "deposit right before distribution, withdraw right after" pattern described in the referenced NFTX report.

### Finding Description
Vote/reward accounting in java-tron works as follows:

- `VoteWitnessActuator.execute()` → `countVoteAccount()` records new votes into `VotesCapsule`/`AccountCapsule` immediately and calls `mortgageService.withdrawReward()` first to settle prior rewards. [1](#0-0) 

- At each maintenance boundary, `MaintenanceManager.doMaintenance()` aggregates all vote deltas made during the ending cycle via `countVote()`, updates each witness's `voteCount`, and then **fixes the vote-count denominator for the next cycle** with `delegationStore.setWitnessVote(nextCycle, witness, witness.getVoteCount())` — this value includes any votes cast at the very last moment of the previous cycle. [2](#0-1) 

- `RewardViCalService`/`DelegationStore.accumulateWitnessVi` computes the cycle's Vi delta using this fixed total-vote denominator together with the reward accrued for that witness during the cycle. [3](#0-2) 

- `MortgageService.withdrawReward()` (and its VM-native equivalent `VoteRewardUtil.withdrawReward`) snapshots the voter's **current, full** vote list via `delegationStore.setAccountVote(endCycle, address, accountCapsule)` at the moment the voter's vote changes — this happens *before* the vote is actually cleared/reduced in the same transaction. This snapshot is later used, once the cycle has fully elapsed, to compute `computeReward(beginCycle, endCycle, account)` = `deltaVi(cycle) * userVote(from snapshot)`. [4](#0-3) [5](#0-4) 

Because the snapshot captures the account's vote weight as it existed for the *entire* cycle in which the change happened (there is no time-weighting of how long the voter actually held that vote within the cycle), a voter who:
1. casts a large vote for witness W in the last block of cycle N (getting folded into the vote-count denominator fixed for cycle N+1), and
2. removes/changes that vote in the very first transaction of cycle N+1 (which immediately snapshots their *full* vote weight for N+1 before clearing it),

will, once cycle N+1's Vi is later computed at the following maintenance, receive the full cycle N+1 reward rate (`deltaVi`) multiplied by their full vote amount — identical economic exposure to a voter who held that vote through the entire cycle, despite having effectively "staked" for only a moment around the reward-accrual boundary. This dilutes/redirects rewards that should accrue to voters who genuinely held their vote for the full cycle, mirroring the `xToken`/`NFTXInventoryStaking` share-based reward-siphoning pattern in the referenced report (enter right before reward accrual snapshot, exit right after, still get full-period credit).

This is reachable by any unprivileged account via ordinary `VoteWitnessContract` broadcast transactions or the equivalent `vote`-native TVM contract call path (`VoteWitnessProcessor`), requiring no special privilege — only sufficient TronPower (frozen balance), which, unlike freezing/unfreezing TRX itself, has no minimum holding period for *reallocating* an existing vote. [6](#0-5) 

### Impact Explanation
This allows a well-capitalized voter to repeatedly harvest full-cycle reward credit while only exposing capital around cycle boundaries, extracting value that should belong to honest, continuously-voting stakers. Over many cycles this constitutes a systematic reward-accounting corruption/theft from other voters, analogous to a "flash staking" attack on the vote-reward pool. The severity is bounded by the requirement to already hold TronPower (frozen TRX) and to time vote transactions precisely around the (relatively long, multi-hour) maintenance cycle boundary, which reduces — but does not eliminate — the practicality of repeated exploitation.

### Likelihood Explanation
Exploitation requires: (a) holding TronPower via previously frozen TRX (no new freeze/unfreeze needed, since only vote reallocation is time-unrestricted), and (b) submitting a vote transaction near the end of one cycle and an unvote/re-vote transaction near the start of the next — both are ordinary, permissionless transactions with no additional access control or delay. Because maintenance cycles are long (multi-hour), only two well-timed transactions per cycle are needed, making this a low-cost, repeatable attack for anyone already holding significant frozen TRX, though it does not scale down to flash-loan-style atomic execution since TronPower itself must have been acquired beforehand.

### Recommendation
- Track a time-weighted vote contribution within each cycle (e.g., record the block/timestamp of vote changes and pro-rate `userVote` in `computeReward` by the fraction of the cycle actually held), rather than snapshotting the full vote amount at the moment of any vote change.
- Alternatively, require that a vote must have been held continuously since before the cycle began (i.e., only count votes present at the maintenance boundary snapshot, and invalidate/zero-out reward eligibility for votes added and removed within the same cycle) — mirroring the report's recommendation to add a delay before newly entered stakes/votes become reward-eligible.
- Ensure the vote-count denominator (`setWitnessVote`) and the individual voter's snapshot (`setAccountVote`) are computed consistently, so momentary vote changes near cycle boundaries cannot decouple an inflated personal-reward snapshot from genuine multi-cycle participation.

### Proof of Concept
1. Attacker holds sufficient frozen TRX (TronPower) acquired well in advance (no timing constraint here).
2. Near the end of cycle N (just before a maintenance execution), attacker submits `VoteWitnessContract` voting a large TronPower amount for witness W. This vote is folded into `countVote()`'s aggregation at maintenance and becomes part of `witness.getVoteCount()` used to fix `delegationStore.setWitnessVote(N+1, W, ...)` for cycle N+1 (see `MaintenanceManager.doMaintenance`, lines 89-162).
3. Immediately after maintenance (start of cycle N+1), attacker submits another `VoteWitnessContract` transaction removing/changing the vote. Inside `MortgageService.withdrawReward` (or `VoteRewardUtil.withdrawReward`), because `beginCycle == currentCycle == N+1` and no snapshot exists yet, the code falls through to `delegationStore.setAccountVote(N+1, attacker, accountCapsule)`, capturing the attacker's full vote weight for cycle N+1 — before the vote is cleared in the same transaction's later steps.
4. Cycle N+1 elapses normally; witness W earns block/transaction-fee rewards, accumulated into `deltaVi(N+1)` via `RewardViCalService.accumulateWitnessVi`.
5. At any later point, when `withdrawReward` runs again for the attacker (e.g., triggered by any subsequent vote/unfreeze/withdraw-reward transaction), the branch `beginCycle + 1 == endCycle && beginCycle < currentCycle` retrieves the stored snapshot for cycle N+1 and computes `reward = deltaVi(N+1) * attacker'sFullVoteWeight`, crediting the attacker as if they had held the vote for the entire cycle N+1, even though it was held for only the single transaction at the start of the cycle.

### Citations

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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L89-162)
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

      incentiveManager.reward(newWitnessAddressList);

      List<ByteString> newWits = consensusDelegate.getActiveWitnesses();
      if (!CollectionUtils.isEqualCollection(currentWits, newWits)) {
        currentWits.forEach(address -> {
          WitnessCapsule witnessCapsule = consensusDelegate.getWitness(address.toByteArray());
          witnessCapsule.setIsJobs(false);
          consensusDelegate.saveWitness(witnessCapsule);
        });
        newWits.forEach(address -> {
          WitnessCapsule witnessCapsule = consensusDelegate.getWitness(address.toByteArray());
          witnessCapsule.setIsJobs(true);
          consensusDelegate.saveWitness(witnessCapsule);
        });

        SRMetrics.recordSrSetChange(currentWits, newWits);
      }

      logger.info("Update witness success. \nbefore: {} \nafter: {}",
          getAddressStringList(currentWits),
          getAddressStringList(newWits));
    }

    if (dynamicPropertiesStore.allowChangeDelegation()) {
      long nextCycle = dynamicPropertiesStore.getCurrentCycleNumber() + 1;
      dynamicPropertiesStore.saveCurrentCycleNumber(nextCycle);
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.setBrokerage(nextCycle, witness.createDbKey(),
            delegationStore.getBrokerage(witness.createDbKey()));
        delegationStore.setWitnessVote(nextCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
```

**File:** chainbase/src/main/java/org/tron/core/service/RewardViCalService.java (L215-229)
```java
  private void accumulateWitnessVi(long cycle, byte[] address) {
    BigInteger preVi = getWitnessVi(cycle - 1, address);
    long voteCount = getWitnessVote(cycle, address);
    long reward = getReward(cycle, address);
    if (reward == 0 || voteCount == 0) { // Just forward pre vi
      if (!BigInteger.ZERO.equals(preVi)) { // Zero vi will not be record
        setWitnessVi(cycle, address, preVi);
      }
    } else { // Accumulate delta vi
      BigInteger deltaVi = BigInteger.valueOf(reward)
          .multiply(DECIMAL_OF_VI_REWARD)
          .divide(BigInteger.valueOf(voteCount));
      setWitnessVi(cycle, address, preVi.add(deltaVi));
    }
  }
```

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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L199-230)
```java
  private long computeReward(long beginCycle, long endCycle, AccountCapsule accountCapsule) {
    if (beginCycle >= endCycle) {
      return 0;
    }

    long reward = 0;
    long newAlgorithmCycle = dynamicPropertiesStore.getNewRewardAlgorithmEffectiveCycle();
    List<Pair<byte[], Long>> srAddresses = accountCapsule.getVotesList().stream()
        .map(vote -> new Pair<>(vote.getVoteAddress().toByteArray(), vote.getVoteCount()))
        .collect(Collectors.toList());
    if (beginCycle < newAlgorithmCycle) {
      long oldEndCycle = min(endCycle, newAlgorithmCycle,
          dynamicPropertiesStore.disableJavaLangMath());
      reward = getOldReward(beginCycle, oldEndCycle, srAddresses);
      beginCycle = oldEndCycle;
    }
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
    return reward;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L39-111)
```java
  public void execute(VoteWitnessParam param, Repository repo) throws ContractExeException {
    byte[] ownerAddress = param.getVoterAddress();
    VoteRewardUtil.withdrawReward(ownerAddress, repo);

    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);

    VotesCapsule votesCapsule = repo.getVotes(ownerAddress);
    if (votesCapsule == null) {
      votesCapsule = new VotesCapsule(ByteString.copyFrom(ownerAddress),
          accountCapsule.getVotesList());
    }

    accountCapsule.clearVotes();
    votesCapsule.clearNewVotes();

    Map<ByteString, Long> voteMap = new HashMap<>();
    Iterator<Protocol.Vote> iterator = param.getVotes().iterator();
    try {
      long sum = 0;
      while (iterator.hasNext()) {
        Protocol.Vote vote = iterator.next();

        byte[] witnessAddress = vote.getVoteAddress().toByteArray();
        /*
          Already covered while doing maintenance in MaintenanceManager.java, for tvm performance,
          we remove the account check
         */
//        if (repo.getAccount(witnessAddress) == null) {
//          throw new ContractValidateException(
//              ACCOUNT_EXCEPTION_STR + StringUtil.encode58Check(witnessAddress) + NOT_EXIST_STR);
//        }
        if (repo.getWitness(witnessAddress) == null) {
          throw new ContractExeException(
              WITNESS_EXCEPTION_STR + StringUtil.encode58Check(witnessAddress) + NOT_EXIST_STR);
        }

        long voteCount = vote.getVoteCount();
        if (voteCount < 0) {
          throw new ContractExeException("Vote count must not be less than 0");
        } else if (voteCount == 0) {
          iterator.remove();
        } else {
          sum = LongMath.checkedAdd(sum, voteCount);
          // merge vote for same witness
          voteMap.put(vote.getVoteAddress(),
              LongMath.checkedAdd(voteMap.getOrDefault(vote.getVoteAddress(), 0L), voteCount));
        }
      }

      long tronPower;
      if (repo.getDynamicPropertiesStore().supportUnfreezeDelay()
          && repo.getDynamicPropertiesStore().supportAllowNewResourceModel()) {
        tronPower = accountCapsule.getAllTronPower();
      } else {
        tronPower = accountCapsule.getTronPower();
      }
      sum =  LongMath.checkedMultiply(sum, TRX_PRECISION);
      if (sum > tronPower) {
        throw new ContractExeException(
            "The total number of votes[" + sum + "] is greater than the tronPower[" + tronPower
                + "]");
      }
    } catch (ArithmeticException e) {
      throw new ContractExeException(e.getMessage());
    }

    for (Map.Entry<ByteString, Long> entry : voteMap.entrySet()) {
      accountCapsule.addVotes(entry.getKey(), entry.getValue());
      votesCapsule.addNewVotes(entry.getKey(), entry.getValue());
    }
    repo.updateAccount(accountCapsule.createDbKey(), accountCapsule);
    repo.updateVotes(ownerAddress, votesCapsule);
  }
```
