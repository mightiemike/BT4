### Title
Voters can capture a full cycle's witness reward by voting immediately before the maintenance/cycle boundary and withdrawing right after - ([File: chainbase/src/main/java/org/tron/core/service/MortgageService.java])

### Summary
TRON distributes SR-voting rewards on a per-cycle (epoch) basis via an accumulator ("VI") pattern similar to MasterChef-style reward-per-share systems. A voter's share of a cycle's reward is computed from the *total* vote count recorded for that witness at the *end* of the cycle, not from a time-weighted average. Any unprivileged user who already holds TronPower (frozen balance) can cast a `VoteWitnessContract` vote for a witness immediately before the maintenance event that closes the current cycle, be counted in full for that entire cycle's reward, and then reallocate/withdraw right after — exactly the "deposit right before reward distribution, withdraw right after" pattern described in the referenced NFTX report.

### Finding Description
Vote rewards accrue through a value-index (`Vi`) mechanism. During `MaintenanceManager.doMaintenance()`, for the cycle that is closing, `DelegationStore.accumulateWitnessVi` is invoked using the witness's *current* total vote count, and this call happens before the vote count for the *next* cycle is snapshotted: [1](#0-0) 

The VI accumulation itself divides the cycle's accrued reward by the current `voteCount` with no notion of when during the cycle that vote count was reached: [2](#0-1) 

When a user later withdraws or queries their reward, `MortgageService.computeReward` (and its TVM analog `VoteRewardUtil.computeReward`) computes `deltaVi = Vi[endCycle-1] - Vi[beginCycle-1]` and multiplies it by the user's *current* vote count for that address — it has no per-block/per-time weighting inside a cycle: [3](#0-2) 

Casting a vote is unrestricted and can be done at any time by any account holding TronPower via `VoteWitnessActuator.countVoteAccount`, which simply withdraws any prior reward, clears old votes, and immediately records the new votes — there is no minimum holding duration or cooldown before the vote counts toward the current cycle: [4](#0-3) 

The equivalent TVM-triggered path (`VoteWitnessProcessor.execute`) exhibits the identical behavior: [5](#0-4) 

Because a vote is recorded for the *whole* current cycle as soon as it is cast (before the maintenance event ends that cycle), and reward computation only tracks `beginCycle`/`endCycle` boundaries rather than intra-cycle timing, a voter who casts their vote just before the maintenance transaction that closes the cycle receives the same full-cycle reward share as a voter who held that vote from the start of the cycle. This mirrors the core root cause identified in the NFTX report: reward distribution keyed to a coarse-grained checkpoint that ignores the actual holding duration within that checkpoint window, allowing late entrants to capture a full share of rewards.

### Impact Explanation
An attacker with already-frozen TronPower (no special privilege required) can:
1. Monitor for a witness about to receive/finalize a large reward for the current cycle (e.g., due to accumulated `payBlockReward`/`payTransactionFeeReward`/`payStandbyWitness` calls) as the maintenance boundary approaches.
2. Submit a `VoteWitnessContract` (or the equivalent TVM `vote` native call) directing votes to that witness just before the maintenance transaction executes.
3. After the cycle rolls over, call `WithdrawBalanceContract`/`withdrawReward` (or re-vote, which auto-withdraws) to claim the full pro-rata reward for the just-closed cycle, even though their vote was only in effect for a tiny fraction of the cycle.
4. Immediately reallocate votes elsewhere and repeat next cycle.

This dilutes the rewards that should accrue to voters who held their position for the full cycle, and lets short-term/flash-style voters extract disproportionate rewards funded by the network's reward pool (block rewards, transaction fee pool, and standby-witness allowance) without providing the intended long-term voting/staking commitment. The impact is an underpriced/miscalculated distribution of protocol-level accounting rewards — a direct accounting-fairness issue, not merely theoretical, since the vote/withdraw actuators are fully unprivileged and reachable on mainnet.

### Likelihood Explanation
Likelihood is bounded by the cycle length (`maintenanceTimeInterval`, default ~6 hours on mainnet, but configurable and much shorter in test/private networks). An attacker needs only ordinary TronPower (frozen TRX) they already control (or briefly acquire on a chain without long freeze lockups), and a way to time their `VoteWitnessContract` transaction to land in the last block(s) before the maintenance transition — a bot watching block timestamps/cycle numbers can do this deterministically without needing to "frontrun a mempool tx" as in the NFTX case, making it arguably easier to execute reliably (no race condition against another party's pending tx; the maintenance trigger is deterministic based on block time).

### Recommendation
Time-weight reward accrual within a cycle instead of using an end-of-cycle vote-count snapshot for the entire cycle's reward, e.g.:
- Track vote-weighted-seconds (or vote-weighted-blocks) within each cycle rather than only the terminal vote count, similar to a streaming/continuous accrual model.
- Alternatively, require votes to be held for a minimum number of blocks/cycles before counting toward that cycle's `Vi` denominator/numerator (a "cliff" similar to freeze lock-up periods already used elsewhere in the protocol, e.g. `FROZEN_PERIOD`).
- Consider snapshotting the vote allocation used for `Vi` calculation at the *start* of the cycle rather than allowing late votes cast within the same cycle to retroactively claim the cycle's full reward share.

### Proof of Concept
1. Attacker account `A` already has TronPower `P` from frozen balance but currently has zero votes cast (or votes for witnesses unlikely to be rewarded this cycle).
2. Near the end of cycle `N` (just before the block that triggers `MaintenanceManager.doMaintenance()`), `A` submits `VoteWitnessContract` voting `P` for witness `W`, which is confirmed accrued reward this cycle via `payBlockReward`/`payTransactionFeeReward` (see `MortgageService.payReward`, `chainbase/src/main/java/org/tron/core/service/MortgageService.java:79-87`).
3. `VoteWitnessActuator.countVoteAccount` records the vote instantly (`actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java:152-191`), setting `beginCycle = N` for `A` via the underlying `withdrawReward` call.
4. Maintenance runs: `DelegationStore.accumulateWitnessVi(N, W, witness.getVoteCount())` computes `Vi[N]` using `W`'s vote count which already includes `A`'s freshly-added `P` votes (`consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java:94-101`, `chainbase/src/main/java/org/tron/core/store/DelegationStore.java:133-146`).
5. Cycle advances to `N+1`. `A` calls `WithdrawBalanceContract` (`actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java:54-56`), triggering `MortgageService.withdrawReward`, which computes `reward = deltaVi(N,N+1) * P` — the full cycle's per-vote reward for `P`, even though the vote was live for only the last block(s) of cycle `N` (`chainbase/src/main/java/org/tron/core/service/MortgageService.java:89-134`, `199-230`).
6. `A` reallocates `P` to a new target witness expected to be rewarded in cycle `N+1` and repeats.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L133-146)
```java
  public void accumulateWitnessVi(long cycle, byte[] address, long voteCount) {
    BigInteger preVi = getWitnessVi(cycle - 1, address);
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L39-53)
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

```
