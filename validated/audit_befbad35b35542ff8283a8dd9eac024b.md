This confirms the mechanism clearly enough to write up the finding. The `countVote` logic accumulates `oldVotes`/`newVotes` deltas per epoch/cycle in `VotesCapsule`, and `MaintenanceManager.doMaintenance()` applies these deltas to `witnessCapsule.setVoteCount()` only once per maintenance cycle, right after computing that cycle's `Vi` via `accumulateWitnessVi()` using the vote count that was in effect for the entire preceding cycle.

### Title
Cycle-boundary vote/reward-weight snapshotting lets an attacker earn a full cycle's Standby/SR voting reward for holding a vote only across a single block boundary - ([File: consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java])

### Summary
TRON's DPoS reward accounting (`MortgageService`/`DelegationStore`/`MaintenanceManager`) grants voting reward ("Vi") credit at whole-cycle granularity: a vote change submitted at any point during cycle `N` only becomes reflected in `witnessCapsule.voteCount` at the `N → N+1` maintenance boundary, and that vote count then stays fixed and earns the full `Vi` delta for the *entire* cycle `N+1`, regardless of how long the voter actually kept the vote/frozen balance active in real time. This mirrors the reported PRISMA `TokenLocker` bug class: weight is bucketed by discrete period index rather than by continuous time held, so a user who acts right at the edge of a period boundary gets credited as if they participated for the whole next period.

### Finding Description
Vote changes are recorded transaction-by-transaction into `VotesCapsule.oldVotes/newVotes` via `VoteWitnessActuator.countVoteAccount()` [1](#0-0) , but they are not applied to the authoritative `witnessCapsule.getVoteCount()` until the next `doMaintenance()` call, which aggregates all outstanding deltas via `countVote(votesStore)` [2](#0-1)  and then bumps `witnessCapsule.setVoteCount()` for use starting the *next* cycle [3](#0-2) .

Crucially, `doMaintenance()` computes `Vi` (the per-share reward accumulator) for the cycle that is *ending* using the vote count that has been fixed since the *previous* boundary — **before** applying this maintenance's new deltas: [4](#0-3) 

This means any `VoteWitnessContract` submitted anywhere within cycle `N` (first block or last block, it doesn't matter) is not credited for cycle `N` at all, but becomes fully credited for the *entirety* of cycle `N+1` once the boundary is crossed, because `witnessCapsule.voteCount` (and thus the `Vi` delta computed at the `N+1 → N+2` boundary) does not change again until the next maintenance. `MortgageService.withdrawReward()`/`queryReward()` then compute a voter's proportional reward strictly from `deltaVi = Vi[endCycle-1] - Vi[beginCycle-1]` [5](#0-4) , giving full-cycle-equivalent reward regardless of how briefly the vote was actually held.

Because there is no minimum holding/lock requirement tied to voting, and `FreezeBalanceV2Actuator` imposes no minimum freeze duration before the frozen TRX can back a vote (only a 1-TRX minimum amount check) [6](#0-5) , an attacker can:
1. Freeze the minimum TRX and cast `VoteWitnessContract` in the last block before a maintenance boundary (cycle `N → N+1`).
2. As soon as the boundary is crossed, immediately submit another `VoteWitnessContract` removing the vote (or unfreeze) in the first block of cycle `N+1`.
3. Because the removal-vote also only becomes effective for cycle `N+2` (not retroactively for `N+1`), the attacker's `witnessCapsule.voteCount` contribution — and hence their proportional `Vi` reward share — is preserved for the *entire* cycle `N+1`, even though economic exposure was limited to roughly one block interval straddling the boundary.

### Impact Explanation
This allows extraction of a disproportionate share of a witness's cycle reward pool (`DelegationStore.getReward`/`Vi` accumulation) with negligible real capital-time cost, diluting genuine long-term voters' rewards and corrupting the intended "reward proportional to sustained vote weight" accounting semantics — an accounting/economic corruption analogous to unauthorized value extraction from the reward pool.

### Likelihood Explanation
The attack requires only two ordinary, unprivileged broadcast transactions (`FreezeBalanceV2Contract`/`VoteWitnessContract`) timed around a publicly known/predictable maintenance boundary (`getNextMaintenanceTime`), which any account can observe and target; no special privileges, keys, or node access are required.

### Recommendation
Prorate `Vi`/reward accrual within a cycle by the actual number of blocks/time a vote was held (e.g., snapshot and interpolate mid-cycle vote changes rather than only applying them at the following boundary), or require votes/frozen balances to be held for a minimum duration (e.g., a full cycle) before they contribute to that cycle's `Vi` calculation, analogous to requiring a minimum lock duration in `TokenLocker`.

### Proof of Concept
1. Observe `dynamicPropertiesStore.getNextMaintenanceTime()`.
2. Freeze the 1-TRX minimum via `FreezeBalanceV2Contract` and immediately submit `VoteWitnessContract` voting for target SR `W` in the last block before the maintenance boundary of cycle `N`.
3. Let `doMaintenance()` run: `witnessCapsule.voteCount` for `W` is bumped to include the attacker's votes, effective for cycle `N+1`.
4. In the very first block of cycle `N+1`, submit another `VoteWitnessContract` clearing the attacker's vote for `W` (and/or `UnfreezeBalanceV2Contract`).
5. At the `N+1 → N+2` boundary, `accumulateWitnessVi` still uses the vote count that included the attacker throughout cycle `N+1` (`consensus/.../MaintenanceManager.java` lines 96-101), so `Vi[N+1]` reflects the attacker's full weight for the whole cycle.
6. Call `withdrawReward`/`queryReward` (`chainbase/.../MortgageService.java` lines 89-134, 136-169): the attacker's `computeReward(beginCycle, endCycle, ...)` uses `deltaVi` spanning the full cycle `N+1`, yielding a full-cycle reward share for real exposure of ~1 block interval.

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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L165-195)
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
    logger.info("There is {} new votes in this epoch", sizeCount);
    return countWitness;
  }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L199-227)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L131-141)
```java
    long frozenBalance = freezeBalanceV2Contract.getFrozenBalance();
    if (frozenBalance <= 0) {
      throw new ContractValidateException("frozenBalance must be positive");
    }
    if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("frozenBalance must be greater than or equal to 1 TRX");
    }

    if (frozenBalance > accountCapsule.getBalance()) {
      throw new ContractValidateException("frozenBalance must be less than or equal to accountBalance");
    }
```
