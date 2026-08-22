### Title
Reward/vote snapshot can be inflated via same-transaction freeze→vote→unfreeze, letting an account capture a full cycle's witness reward for stake held only momentarily - (File: actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java)

### Summary
`FreezeBalanceV2`, `VoteWitness`, and `UnfreezeBalanceV2` are all exposed as TVM native-contract operations that a smart contract can invoke sequentially inside a single atomic transaction. The reward accounting (`VoteRewardUtil.withdrawReward` / `MortgageService.withdrawReward`) snapshots an account's *current* vote list into per-cycle storage (`updateAccountVote`) and later multiplies that snapshotted vote count by a witness's accumulated per-vote reward index (`deltaVi`) for the whole cycle. Because `UnfreezeBalanceV2Processor.execute` calls `VoteRewardUtil.withdrawReward` **before** it reduces the account's votes (`updateVote` runs afterward), an account can freeze TRX, vote with the resulting inflated TRON Power, and unfreeze in the same transaction while the reward snapshot still reflects the inflated vote count — the same "read an instantaneously-inflated balance/weight into a period-wide reward calculation" pattern described in the report.

### Finding Description
The reward computation in `VoteRewardUtil.computeReward` / `MortgageService.computeReward` treats an account's vote count as constant across `[beginCycle, endCycle)` and multiplies it by `deltaVi` (the witness's accumulated reward-per-vote over that range): [1](#0-0) 

This snapshot is written via `repository.updateAccountVote(address, endCycle, accountCapsule)`, using whatever vote list is currently on the account object at the moment `withdrawReward` runs: [2](#0-1) 

Freezing TRX (`FreezeBalanceV2Processor`) increases TRON Power (`repo.addTotalEnergyWeight`/`addTotalTronPowerWeight` and per-account weight) immediately and synchronously: [3](#0-2) 

`VoteWitnessActuator` allows voting up to the current TRON Power and — critically — calls `mortgageService.withdrawReward` (snapshotting the *old* vote state) **before** applying the new, larger vote list: [4](#0-3) 

Finally, `UnfreezeBalanceV2Processor.execute` calls `VoteRewardUtil.withdrawReward(ownerAddress, repo)` **before** `updateVote` shrinks the vote list back down to match the reduced TRON Power: [5](#0-4) 

Because these three native operations (`freezeBalanceV2`, `voteWitness`, `unfreezeBalanceV2`) are all callable from TVM smart-contract code within one transaction, an account can:
1. Freeze a large amount of TRX to obtain a large TRON Power.
2. Vote for a witness with that inflated power (this call's `withdrawReward` locks in `beginCycle = currentCycle` using the *old*, smaller vote list, but writes the *new* inflated vote list to the account).
3. Immediately unfreeze the TRX in the same transaction — `UnfreezeBalanceV2Processor` calls `withdrawReward` first, which snapshots the still-inflated vote list into `updateAccountVote(address, currentCycle, accountCapsule)`, before `updateVote` reduces the vote count to match the now-lower TRON Power.

Once the cycle advances, the next `withdrawReward` call for this account uses the `currentCycle` snapshot (containing the inflated vote count) as `computeReward(beginCycle, endCycle, snapshotAccount)`, multiplying the full-cycle `deltaVi` (accrued from the witness's real, network-wide vote tally) by this inflated vote count — even though the account's actual frozen TRX (and thus its legitimate vote weight) existed for only a fraction of that cycle.

### Impact Explanation
This allows an account to claim a disproportionate share of a witness's block/vote reward pool for an entire voting cycle while only bearing the economic cost of holding TRX frozen for the duration of a single transaction. This is direct accounting/asset corruption: the attacker is paid `allowance` (later withdrawable as real TRX via `WithdrawBalanceActuator`/`WithdrawRewardProcessor`) based on a stake weight it never actually held for the reward period, effectively diluting/stealing reward that should go to genuine long-term voters and to the witness's other stakers.

### Likelihood Explanation
Exploitability requires precise timing relative to cycle/maintenance boundaries (the reward snapshot's `currentCycle` window and the witness's real Vi accrual for that same cycle), and the actual magnitude of gain depends on how the witness-level `Vi`/vote tally used for `deltaVi` is derived at maintenance time versus the attacker's snapshotted vote count. This makes the exploit require capital (must actually hold/own or briefly acquire the TRX to freeze — no external flash-loan primitive for TRX exists in-protocol) and careful timing around cycle boundaries, which reduces (but does not eliminate) likelihood compared to the original DeFi flash-loan case. I was not able to fully trace, within the available search budget, the exact code path that computes a witness's total per-cycle vote tally (used to derive `deltaVi` in `RewardViCalService`/`DelegationStore.accumulateWitnessVi`) to confirm whether it samples votes at a single maintenance instant (making the attack more practical) or aggregates continuously; this should be verified with deeper analysis (e.g., in `WitnessController`/`Maintenance` processing) before treating this as a fully proven, high-confidence exploit.

### Recommendation
- Make the freeze/vote/unfreeze checkpointing atomic and consistent: when `withdrawReward` is invoked as part of an unfreeze that will reduce voting power, either call `updateVote` (which shrinks the vote list) **before** taking the reward snapshot, or otherwise ensure the snapshot used for `computeReward` reflects vote weight actually held for a meaningful duration of the cycle (e.g., minimum holding period, or time-weighted average vote power) rather than an instantaneous value.
- Consider enforcing a minimum lock time between `FreezeBalanceV2` and the corresponding `UnfreezeBalanceV2` for the same resource, closing the "freeze → vote → unfreeze in one tx" pattern.
- Audit `RewardViCalService`/`DelegationStore.accumulateWitnessVi` to confirm whether per-cycle vote tallies used for `deltaVi` can be influenced by within-cycle transient freezes, and add safeguards if so.

### Proof of Concept
Conceptual sequence executed via a smart contract in a single transaction (or across a very short window straddling maintenance):
1. Call native `freezeBalanceV2` to freeze a large TRX amount for TRON_POWER, increasing the account's TRON Power (`FreezeBalanceV2Processor.execute`).
2. Call `voteWitness` to vote for a target witness using the inflated TRON Power (`VoteWitnessActuator.countVoteAccount`), which snapshots the *old* vote state via `withdrawReward` and then writes the new, large vote list to the account.
3. Call native `unfreezeBalanceV2` for the same resource, immediately returning the frozen TRX to spendable balance. This calls `VoteRewardUtil.withdrawReward` (which records `updateAccountVote(address, currentCycle, accountCapsule)` using the still-inflated vote list) before `updateVote` shrinks the vote count.
4. After the cycle ends, call `withdrawReward` (or trigger it via any actuator) — `computeReward` will multiply the witness's cycle-wide `deltaVi` by the inflated vote count captured in step 3, crediting the attacker's `allowance` with reward disproportionate to the actual (momentary) stake held.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java (L16-55)
```java
  public static void withdrawReward(byte[] address, Repository repository) {
    if (!VMConfig.allowTvmVote()) {
      return;
    }
    AccountCapsule accountCapsule = repository.getAccount(address);
    long beginCycle = repository.getBeginCycle(address);
    long endCycle = repository.getEndCycle(address);
    long currentCycle = repository.getDynamicPropertiesStore().getCurrentCycleNumber();
    long reward = 0;
    if (beginCycle > currentCycle || accountCapsule == null) {
      return;
    }
    if (beginCycle == currentCycle) {
      AccountCapsule account = repository.getAccountVote(beginCycle, address);
      if (account != null) {
        return;
      }
    }
    if (beginCycle + 1 == endCycle && beginCycle < currentCycle) {
      AccountCapsule account = repository.getAccountVote(beginCycle, address);
      if (account != null) {
        reward = computeReward(beginCycle, endCycle, account, repository);
        adjustAllowance(address, reward, repository);
        reward = 0;
      }
      beginCycle += 1;
    }
    endCycle = currentCycle;
    if (CollectionUtils.isEmpty(accountCapsule.getVotesList())) {
      repository.updateBeginCycle(address, endCycle + 1);
      return;
    }
    if (beginCycle < endCycle) {
      reward += computeReward(beginCycle, endCycle, accountCapsule, repository);
      adjustAllowance(address, reward, repository);
    }
    repository.updateBeginCycle(address, endCycle);
    repository.updateEndCycle(address, endCycle + 1);
    repository.updateAccountVote(address, endCycle, accountCapsule);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java (L90-110)
```java
  private static long computeReward(long beginCycle, long endCycle,
                                    AccountCapsule accountCapsule, Repository repository) {
    if (beginCycle >= endCycle) {
      return 0;
    }

    long reward = 0;
    for (Protocol.Vote vote : accountCapsule.getVotesList()) {
      byte[] srAddress = vote.getVoteAddress().toByteArray();
      BigInteger beginVi = repository.getDelegationStore().getWitnessVi(beginCycle - 1, srAddress);
      BigInteger endVi = repository.getDelegationStore().getWitnessVi(endCycle - 1, srAddress);
      BigInteger deltaVi = endVi.subtract(beginVi);
      if (deltaVi.signum() <= 0) {
        continue;
      }
      long userVote = vote.getVoteCount();
      reward += deltaVi.multiply(BigInteger.valueOf(userVote))
          .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
    }
    return reward;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceV2Processor.java (L68-105)
```java
  public void execute(FreezeBalanceV2Param param, Repository repo) {
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();

    byte[] ownerAddress = param.getOwnerAddress();
    long frozenBalance = param.getFrozenBalance();
    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    if (dynamicStore.supportAllowNewResourceModel()
        && accountCapsule.oldTronPowerIsNotInitialized()) {
      accountCapsule.initializeOldTronPower();
    }
    switch (param.getResourceType()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(frozenBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        repo.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(frozenBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        repo.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(frozenBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        repo.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        logger.debug("Resource Code Error.");
    }

    // deduce balance of owner account
    long newBalance = accountCapsule.getBalance() - frozenBalance;
    accountCapsule.setBalance(newBalance);
    repo.updateAccount(accountCapsule.createDbKey(), accountCapsule);
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L118-146)
```java
  public long execute(UnfreezeBalanceV2Param param, Repository repo) {
    byte[] ownerAddress = param.getOwnerAddress();
    long unfreezeBalance = param.getUnfreezeBalance();
    VoteRewardUtil.withdrawReward(ownerAddress, repo);

    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    long now = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();

    long unfreezeExpireBalance = this.unfreezeExpire(accountCapsule, now);

    if (repo.getDynamicPropertiesStore().supportAllowNewResourceModel()
        && accountCapsule.oldTronPowerIsNotInitialized()) {
      accountCapsule.initializeOldTronPower();
    }

    long expireTime = this.calcUnfreezeExpireTime(now, repo);
    accountCapsule.addUnfrozenV2List(param.getResourceType(), unfreezeBalance, expireTime);

    this.updateTotalResourceWeight(accountCapsule, param.getResourceType(), unfreezeBalance, repo);
    this.updateVote(accountCapsule, param.getResourceType(), ownerAddress, repo);

    if (repo.getDynamicPropertiesStore().supportAllowNewResourceModel()
        && !accountCapsule.oldTronPowerIsInvalid()) {
      accountCapsule.invalidateOldTronPower();
    }

    repo.updateAccount(accountCapsule.createDbKey(), accountCapsule);
    return unfreezeExpireBalance;
  }
```
