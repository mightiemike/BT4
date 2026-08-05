### Title
Missing reward/vote reconciliation on `DelegateResourceActuator` allows permanent over-accrual of voting rewards after resource is delegated away - (File: `actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java`)

### Summary
Every actuator that reduces a user's effective staked balance (`UnfreezeBalanceActuator`, `UnfreezeBalanceV2Actuator`, `VoteWitnessActuator`, `WithdrawBalanceActuator`) first calls `mortgageService.withdrawReward(ownerAddress)` to checkpoint the reward accounting, and — where the reduction can make TRON Power fall below the votes already cast — also calls a vote-reconciliation routine (`updateVote()`) to shrink/clear the stale votes. `DelegateResourceActuator`, which also reduces the owner's own frozen (staked) balance by moving it into `DelegatedFrozenV2Balance`, does neither. This is directly analogous to the reported issue: winding down a staked position without invoking the hook that manages reward/vote accounting lets the user keep accruing rewards on stake that no longer backs it.

### Finding Description
`UnfreezeBalanceV2Actuator.execute()` demonstrates the expected pattern when a user's frozen balance is reduced: [1](#0-0) 

It calls `mortgageService.withdrawReward(ownerAddress)` before mutating the account, and then `updateVote()` which explicitly reduces or clears the account's votes if the account's TRON Power (backed by frozen balance) is no longer sufficient to support them: [2](#0-1) 

`VoteWitnessActuator` and `WithdrawBalanceActuator` also call `mortgageService.withdrawReward()` before mutating vote/balance state: [3](#0-2) [4](#0-3) 

`DelegateResourceActuator.execute()`, however, directly reduces the owner's own frozen balance (`addFrozenBalanceForBandwidthV2(-delegateBalance)` / `addFrozenBalanceForEnergyV2(-delegateBalance)`) — moving stake out to a delegate — without ever calling `mortgageService.withdrawReward()` or any vote-reconciliation logic: [5](#0-4) 

Reward accrual is computed in `MortgageService.withdrawReward()`/`computeReward()`, which uses the account's `votesList` snapshot recorded at the last checkpoint (`delegationStore.setAccountVote`) to compute rewards for every subsequent cycle until the next checkpoint: [6](#0-5) 

Because `DelegateResourceActuator` never triggers this checkpoint nor reduces the account's votes, a user can freeze balance, vote for witnesses using that balance's TRON Power, then delegate that same frozen balance away to another account (zeroing out their real backing stake) while their `votesList` and outstanding vote weight remain untouched in `AccountStore`/`VotesStore`. The account keeps earning `computeReward()` payouts each maintenance cycle based on the stale, now-unbacked votes, exactly mirroring the "position wound down without triggering `onDecreasePosition` reward-accounting hook" pattern from the report — the resource here (frozen balance / TRON Power) is decreased without the corresponding reward/vote hook firing, leaving stale reward-bearing state (`votesList`) in place indefinitely.

### Impact Explanation
This allows an attacker to earn voting rewards without real economic backing: freeze TRX, vote for witnesses, delegate the entire frozen balance away (keeping votes and reward eligibility intact), and repeat the freeze/delegate cycle with the same principal across multiple sub-accounts, multiplying reward-bearing "TRON Power" beyond what is actually staked. This directly manipulates the protocol's staking/reward accounting (state/accounting impact class) and can drain the community/witness reward pool disproportionately.

### Likelihood Explanation
The path is reachable by any unprivileged account with no special permissions: `FreezeBalanceV2Contract` → `VoteWitnessContract` → `DelegateResourceContract`, all standard user transactions gated only by `dynamicStore.supportDR()` and `supportUnfreezeDelay()`, both of which are already enabled on mainnet-equivalent configurations that support the Delegate Resource feature. No governance/committee action or trusted role is required.

### Recommendation
In `DelegateResourceActuator.execute()`, before (or as part of) reducing the owner's `FrozenBalanceForBandwidthV2`/`FrozenBalanceForEnergyV2`, invoke `mortgageService.withdrawReward(ownerAddress)` to checkpoint reward accounting, and reuse (or factor out) the `updateVote()`-style logic from `UnfreezeBalanceV2Actuator` to reduce/clear the owner's votes whenever the post-delegation TRON Power is insufficient to back the currently cast votes.

### Proof of Concept
1. Account A freezes `X` TRX for bandwidth (`FreezeBalanceV2Contract`), obtaining TRON Power `X`.
2. Account A votes for witness W with TRON Power `X` (`VoteWitnessContract` → `VoteWitnessActuator`, which checkpoints reward via `mortgageService.withdrawReward`).
3. Account A delegates the entire `X` frozen balance to Account B (`DelegateResourceContract` → `DelegateResourceActuator.execute()`), reducing A's real backing balance to 0 without any call to `withdrawReward()` or vote reduction.
4. At each subsequent maintenance cycle, `MortgageService.computeReward()` still credits A's `allowance` based on the stale `votesList` recorded for witness W, even though A's TRON Power is now 0. This reward keeps accruing until A performs an action that finally triggers `withdrawReward`/`updateVote` (e.g., an `UnfreezeBalanceV2` on a different resource type or `WithdrawBalanceContract`), at which point the excess reward has already been permanently credited to `allowance` and is withdrawable.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L340-388)
```java
    long totalVote = 0;
    for (Protocol.Vote vote : accountCapsule.getVotesList()) {
      totalVote += vote.getVoteCount();
    }
    long ownedTronPower;
    if (dynamicStore.supportAllowNewResourceModel()) {
      ownedTronPower = accountCapsule.getAllTronPower();
    } else {
      ownedTronPower = accountCapsule.getTronPower();
    }

    // tron power is enough to total votes
    if (ownedTronPower >= totalVote * TRX_PRECISION) {
      return;
    }
    if (totalVote == 0) {
      return;
    }

    VotesCapsule votesCapsule;
    if (!votesStore.has(ownerAddress)) {
      votesCapsule = new VotesCapsule(
          unfreezeBalanceV2Contract.getOwnerAddress(),
          accountCapsule.getVotesList()
      );
    } else {
      votesCapsule = votesStore.get(ownerAddress);
    }

    // Update Owner Voting
    List<Vote> addVotes = new ArrayList<>();
    for (Vote vote : accountCapsule.getVotesList()) {
      long newVoteCount = (long)
          ((double) vote.getVoteCount() / totalVote * ownedTronPower / TRX_PRECISION);
      if (newVoteCount > 0) {
        Vote newVote = Vote.newBuilder()
            .setVoteAddress(vote.getVoteAddress())
            .setVoteCount(newVoteCount)
            .build();
        addVotes.add(newVote);
      }
    }
    votesCapsule.clearNewVotes();
    votesCapsule.addAllNewVotes(addVotes);
    votesStore.put(ownerAddress, votesCapsule);

    accountCapsule.clearVotes();
    accountCapsule.addAllVotes(addVotes);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L152-163)
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

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L54-58)
```java
    mortgageService.withdrawReward(withdrawBalanceContract.getOwnerAddress()
        .toByteArray());

    AccountCapsule accountCapsule = accountStore.
        get(withdrawBalanceContract.getOwnerAddress().toByteArray());
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L64-98)
```java
    AccountCapsule ownerCapsule = accountStore
        .get(delegateResourceContract.getOwnerAddress().toByteArray());
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    long delegateBalance = delegateResourceContract.getBalance();
    boolean lock = delegateResourceContract.getLock();
    long lockPeriod = getLockPeriod(dynamicStore.supportMaxDelegateLockPeriod(),
            delegateResourceContract);
    byte[] receiverAddress = delegateResourceContract.getReceiverAddress().toByteArray();

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
