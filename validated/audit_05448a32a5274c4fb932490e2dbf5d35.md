### Title
Stale vote-reward accounting when delegating frozen resources without settling rewards first - ([File: actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java])

### Summary
`DelegateResourceActuator.execute()` reduces an account's own frozen V2 balance (the TRX stake backing its `TronPower`/voting weight) when the owner delegates `BANDWIDTH` or `ENERGY` resources to another address, but it never calls `mortgageService.withdrawReward()` to settle the pending vote-based reward for the current cycle before doing so. Every other actuator in the codebase that mutates the frozen/staked balance backing votes (`UnfreezeBalanceActuator`, `UnfreezeBalanceV2Actuator`, `VoteWitnessActuator`, `WithdrawBalanceActuator`) explicitly calls `mortgageService.withdrawReward(ownerAddress)` first, precisely to avoid the class of bug described in the external report (staking/vote balance removed without settling reward state first).

### Finding Description
The Usual report's root cause is: a function that decreases a user's "staked" balance (`claimOriginalAllocation()`) does so without first calling the reward-update hook (`_updateReward`), so the reward-per-token accounting becomes permanently detached from the user's true stake history, and the user's earned-but-unclaimed rewards are lost/unclaimable.

In java-tron, `MortgageService.withdrawReward()` is the analogous "settle rewards before state changes" hook: it computes and credits the witness-vote reward accrued for the account up to the current cycle, and then snapshots the account's current vote list via `delegationStore.setAccountVote(endCycle, address, accountCapsule)` [1](#0-0) . Because reward computation for a cycle is based on the account's vote/TronPower snapshot recorded at the time `withdrawReward` is called, every code path that changes the frozen balance underpinning `TronPower` or clears/modifies votes calls this method first:

- `UnfreezeBalanceV2Actuator.execute()` calls `mortgageService.withdrawReward(ownerAddress)` before altering frozen balances and votes [2](#0-1) 
- `VoteWitnessActuator.countVoteAccount()` does the same before updating votes [3](#0-2) 
- The TVM native-contract equivalent `UnfreezeBalanceProcessor.execute()` even performs an explicit runtime check — if the account's remaining `TronPower` drops below its currently-used vote weight, it calls `VoteRewardUtil.withdrawReward(...)` and clears votes before allowing the resource to be released [4](#0-3) 

`DelegateResourceActuator.execute()`, however, reduces the owner's own `FrozenBalanceForBandwidthV2`/`FrozenBalanceForEnergyV2` (i.e., reduces the TRX backing `TronPower`) directly, with no call to `mortgageService.withdrawReward()` and no check/adjustment of the owner's existing votes if the delegated amount pushes `usedTronPower` above the account's remaining `TronPower`: [5](#0-4) 

This is the same bug class as the Usual report: a function decreases the balance that backs reward/voting weight without first settling reward state for the prior period, and — unlike the sibling `UnfreezeBalance*` paths — with no compensating vote-consistency check either.

### Impact Explanation
When an account with active witness votes delegates part of its frozen V2 balance without a preceding `withdrawReward()` call:
- The reward computed for the *current, still-open* cycle at the next `withdrawReward()` invocation (e.g., triggered later by `WithdrawBalanceActuator`, `VoteWitnessActuator`, or `UnfreezeBalanceV2Actuator`) uses `computeReward(beginCycle, endCycle, accountCapsule)` against the vote-count snapshot recorded in `delegationStore`, not the live, now-reduced `TronPower`. If the reduced stake causes the account's votes to exceed its remaining `TronPower` (a state the `UnfreezeBalanceProcessor` path explicitly detects and corrects, but `DelegateResourceActuator` does not), the account can retain a stale vote list backed by a stake it no longer effectively holds, corrupting the reward snapshot used for that cycle and leading to incorrect (either under- or unfairly favorable) reward settlement, and a divergence between actual voting eligibility and recorded votes.

### Likelihood Explanation
Delegation of already-frozen resources is a common, unprivileged user operation (`DelegateResourceContract`), and any account that has voted for witnesses and later delegates part of its frozen balance to another account can trigger this path without any special conditions — no permission checks are involved, only ordinary resource-delegation and voting actions available to any staker.

### Recommendation
Update `DelegateResourceActuator.execute()` to mirror the pattern already used in `UnfreezeBalanceProcessor`/`UnfreezeBalanceV2Actuator`: call `mortgageService.withdrawReward(ownerAddress)` before mutating `FrozenBalanceForBandwidthV2`/`FrozenBalanceForEnergyV2`, and add the same `usedTronPower` vs. remaining `TronPower` check found in `UnfreezeBalanceProcessor.execute()` to clear/adjust stale votes when delegation reduces the account's effective `TronPower` below its currently cast vote weight.

### Proof of Concept
Conceptual reproduction (cannot be executed without repo access to the test harness):
1. Account A freezes TRX via `FreezeBalanceV2Contract` for BANDWIDTH, accruing `TronPower`.
2. Account A votes for a witness using the full `TronPower` via `VoteWitnessContract` (this calls `mortgageService.withdrawReward` and records the vote snapshot).
3. Time passes; a reward accrues on A's vote weight for the open cycle.
4. Account A delegates part of the frozen BANDWIDTH balance to Account B via `DelegateResourceContract`. `DelegateResourceActuator.execute()` reduces A's `FrozenBalanceForBandwidthV2` without calling `mortgageService.withdrawReward()` and without adjusting A's votes, even though A's votes now exceed A's remaining `TronPower`.
5. When A later calls `WithdrawBalanceContract`/`UnfreezeBalanceV2Contract`, the reward computed by `MortgageService.computeReward()` is based on the stale vote snapshot from step 2, taken over a stake basis that no longer matches A's real holdings for part of the cycle — producing an inconsistent/incorrect reward settlement, analogous to the loss/mismatch of rewards described in the Usual report.

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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L69-89)
```java
    byte[] ownerAddress = unfreezeBalanceV2Contract.getOwnerAddress().toByteArray();
    long now = dynamicStore.getLatestBlockHeaderTimestamp();

    mortgageService.withdrawReward(ownerAddress);

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
    long unfreezeAmount = this.unfreezeExpire(accountCapsule, now);
    long unfreezeBalance = unfreezeBalanceV2Contract.getUnfreezeBalance();

    if (dynamicStore.supportAllowNewResourceModel()
        && accountCapsule.oldTronPowerIsNotInitialized()) {
      accountCapsule.initializeOldTronPower();
    }

    ResourceCode freezeType = unfreezeBalanceV2Contract.getResource();

    long expireTime = this.calcUnfreezeExpireTime(now);
    accountCapsule.addUnfrozenV2List(freezeType, unfreezeBalance, expireTime);

    this.updateTotalResourceWeight(accountCapsule, unfreezeBalanceV2Contract, unfreezeBalance);
    this.updateVote(accountCapsule, unfreezeBalanceV2Contract, ownerAddress);
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L155-187)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L205-224)
```java
    if (VMConfig.allowTvmVote() && !accountCapsule.getVotesList().isEmpty()) {
      long usedTronPower = 0;
      for (Protocol.Vote vote : accountCapsule.getVotesList()) {
        usedTronPower += vote.getVoteCount();
      }
      if (accountCapsule.getTronPower() < usedTronPower * TRX_PRECISION) {
        VoteRewardUtil.withdrawReward(ownerAddress, repo);
        VotesCapsule votesCapsule = repo.getVotes(ownerAddress);
        accountCapsule = repo.getAccount(ownerAddress);
        if (votesCapsule == null) {
          votesCapsule = new VotesCapsule(ByteString.copyFrom(ownerAddress),
              accountCapsule.getVotesList());
        } else {
          votesCapsule.clearNewVotes();
        }
        accountCapsule.clearVotes();
        repo.updateVotes(ownerAddress, votesCapsule);
        repo.updateAccount(ownerAddress, accountCapsule);
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L73-97)
```java
    // delegate resource to receiver
    switch (delegateResourceContract.getResource()) {
      case BANDWIDTH:
        delegateResource(ownerAddress, receiverAddress, true,
            delegateBalance, lock, lockPeriod);

        ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(delegateBalance);
        ownerCapsule.addFrozenBalanceForBandwidthV2(-delegateBalance);
        break;
      case ENERGY:
        delegateResource(ownerAddress, receiverAddress, false,
            delegateBalance, lock, lockPeriod);

        ownerCapsule.addDelegatedFrozenV2BalanceForEnergy(delegateBalance);
        ownerCapsule.addFrozenBalanceForEnergyV2(-delegateBalance);
        break;
      default:
        logger.debug("Resource Code Error.");
    }

    accountStore.put(ownerCapsule.createDbKey(), ownerCapsule);

    ret.setStatus(fee, code.SUCESS);

    return true;
```
