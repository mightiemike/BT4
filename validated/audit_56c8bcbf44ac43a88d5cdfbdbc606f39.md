### Title
Vote reward sniping via premature `beginCycle` assignment in `MortgageService.withdrawReward` - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
The Zeta Markets `add_stake` bug lets a user reset `stake_start_epoch` to the *current* epoch instead of the *next* one, letting freshly-added stake earn a full epoch of rewards. Java-tron's SR-vote reward accounting has the same class of flaw: when a user casts/changes a vote, `MortgageService.withdrawReward` (invoked from `VoteWitnessActuator`) sets the voter's `beginCycle` to the **current** cycle rather than the next one, so a vote cast at any point—including the very last block—of a cycle is treated as if it had been held for the entire cycle when rewards are later computed.

### Finding Description
`VoteWitnessActuator.countVoteAccount` calls `mortgageService.withdrawReward(ownerAddress)` *before* applying the new vote weights to the account: [1](#0-0) 

Inside `withdrawReward`, once the outstanding reward up to the current cycle is settled, the function sets:
```
endCycle = currentCycle;
...
delegationStore.setBeginCycle(address, endCycle);      // == currentCycle
delegationStore.setEndCycle(address, endCycle + 1);
delegationStore.setAccountVote(endCycle, address, accountCapsule);
``` [2](#0-1) 

Because the actuator applies the new (larger) vote weights to `accountCapsule` right after this call, the voter's *new* vote amount becomes attributable to `beginCycle == currentCycle`. When the voter later withdraws/queries rewards, `computeReward` (and the identical logic in `RewardViCalService.getNewRewardAlgorithmReward`) computes:
```
BigInteger beginVi = getWitnessVi(beginCycle - 1, srAddress);
BigInteger endVi   = getWitnessVi(endCycle - 1, srAddress);
reward += deltaVi.multiply(userVote) / DECIMAL_OF_VI_REWARD;
``` [3](#0-2) [4](#0-3) 

`deltaVi` represents the reward-per-vote accrued over the *entire* `currentCycle` (from `beginCycle-1` to `endCycle-1`), and it is multiplied by the voter's full, newly-increased `userVote`. There is no time-weighting within the cycle — the exact same root cause pattern as the `add_stake` bug, where `stake_start_epoch` is pinned to the *current* period instead of the *next* one, letting the actor claim a full period's rewards for a fractional holding period.

The only guard is the `beginCycle == currentCycle` re-entrancy check, which prevents double-crediting on repeated votes in the same cycle, but does **not** prevent the initial reward window from including the entire current cycle: [5](#0-4) 

### Impact Explanation
This is an accounting/economic-fairness bug: an unprivileged account can freeze TRX (via `FreezeBalanceActuator`/`FreezeBalanceV2Actuator`) and cast/increase a vote to a witness at the very end of a cycle (i.e., just before the DPoS maintenance/cycle rollover), and be credited a share of that witness's *entire* cycle reward pool as if the vote had been held throughout the whole cycle. Since witness rewards for a cycle are distributed pro-rata (`userVote / totalWitnessVote`) using end-of-cycle snapshots (`DelegationStore.getWitnessVote`/`getWitnessVi`), a large last-moment voter both dilutes genuine long-term voters' share and extracts rewards disproportionate to actual holding time — a form of reward "sniping"/front-running that misallocates SR voting rewards, which is a real accounting/settlement divergence from intended semantics (reward should reflect the voting-weighted duration, not an instantaneous end-of-cycle snapshot).

### Likelihood Explanation
Reachable by any unprivileged account through normal `VoteWitnessContract` transactions (`VoteWitnessActuator`) or the equivalent TVM vote precompile path (`VoteRewardUtil`/`VoteWitnessProcessor`), requiring only that the actor time a vote/vote-increase transaction near the end of a voting cycle (maintenance interval), which is publicly observable on-chain (block timestamps and `currentCycleNumber` are public). No special privileges or race conditions beyond transaction timing are required, making this straightforward for any motivated user to reproduce, though the per-cycle economic gain is bounded by the size of one witness's cycle reward pool and the size of the vote the attacker can muster at that moment.

### Recommendation
Change the reward-window bookkeeping so a new or increased vote only starts accruing rewards from the **next** cycle rather than the current one — i.e., set `beginCycle = currentCycle + 1` (analogous to using `get_next_epoch()` in the original report) for the incremental vote weight introduced by the current transaction, while still correctly settling any reward owed for the voter's *prior* vote weight through the end of the current cycle. This requires distinguishing "reward attributable to previously-held vote weight" from "reward attributable to newly-added vote weight" within the same cycle in `MortgageService.withdrawReward` / `VoteRewardUtil.withdrawReward`.

### Proof of Concept
1. Attacker freezes a small amount of TRX and casts a minimal vote for witness `W` early, so `beginCycle` becomes some cycle `N`.
2. Attacker waits until just before cycle `N+k` ends (right before DPoS maintenance rolls the cycle), then calls `VoteWitnessContract` again with a very large new vote count for `W` (having frozen a large amount of TRX beforehand).
3. `VoteWitnessActuator.countVoteAccount` calls `mortgageService.withdrawReward` first, settling old rewards, then sets `beginCycle = N+k` (current cycle) via `delegationStore.setBeginCycle`, and finally applies the large new vote weight to the account.
4. After cycle `N+k` ends and maintenance computes `getWitnessVi(N+k, W)` based on the total reward pool accrued by `W` during the entire cycle `N+k` (which included the attacker's vote for only a fraction of a block), the attacker calls `withdrawReward`/`queryReward` again. `computeReward(beginCycle=N+k, endCycle=N+k+1, ...)` credits the attacker `deltaVi * (large new userVote)`, granting a full cycle's worth of proportional reward for holding the large vote for a negligible fraction of that cycle, at the expense of diluting other, genuine long-term voters of `W`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L152-190)
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
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L101-106)
```java
    if (beginCycle == currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        return;
      }
    }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L118-134)
```java
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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L199-229)
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
```

**File:** chainbase/src/main/java/org/tron/core/service/RewardViCalService.java (L143-171)
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

    long reward = 0;
    if (beginCycle < endCycle) {
      for (Pair<byte[], Long> vote : votes) {
        byte[] srAddress = vote.getKey();
        BigInteger beginVi = getWitnessVi(beginCycle - 1, srAddress);
        BigInteger endVi = getWitnessVi(endCycle - 1, srAddress);
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

```
