### Title
Reward-per-vote (Vi) accrued for a voter is permanently lost when `unfreezeBalance` (old FreezeV1) is triggered via the TVM `unfreezeBalanceContract` native contract and the reduced `tronPower` still covers the existing vote count - `File: actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java`

### Summary
`UnfreezeBalanceProcessor.execute()` (the TVM/native-contract path for the legacy `UnfreezeBalanceContract`, reachable from a broadcast contract-triggered transaction) only calls `VoteRewardUtil.withdrawReward(ownerAddress, repo)` — the routine that snapshots and credits reward accrued since the account's `beginCycle` based on its current `votesList` — *inside* a conditional branch that fires only when the account's remaining `tronPower` drops below its currently used vote count. If `tronPower` still covers the existing votes after the unfreeze, reward is never withdrawn/snapshotted at this call site, even though the account's underlying frozen balance (the tron-power backing the vote) was just reduced. [1](#0-0) 

This mirrors the audited BlueBerryBank pattern: the "share"/backing amount (`tronPower`/frozen balance, analogous to `pos.underlyingVaultShare`) is reduced immediately and unconditionally, while the accrual/reward computation that depends on the pre-reduction state (`VoteRewardUtil.withdrawReward`, analogous to interest realized from Compound on withdrawal) is gated behind a condition that can be false, so the reward computed later by `computeReward` (which walks `Vi` deltas using the account's *current* vote list, not the vote list that was active while the reward accrued) can permanently diverge from what was actually earned before the tron-power reduction.

### Finding Description
`VoteRewardUtil.withdrawReward` computes reward for the range `[beginCycle, endCycle)` using the account's `Vote` list captured at call time, and then advances `beginCycle`/`endCycle` and snapshots the vote in `DelegationStore` via `updateAccountVote`: [2](#0-1) 

Compare this to the compliant paths:
- `UnfreezeBalanceActuator.execute()` (the ordinary, non-TVM actuator for the same contract type) unconditionally calls `mortgageService.withdrawReward(ownerAddress)` **before** any resource/vote mutation happens: [3](#0-2) 
- `UnfreezeBalanceV2Processor.execute()` (the newer FreezeV2 TVM native contract) also unconditionally calls `VoteRewardUtil.withdrawReward` as the very first statement, before any state mutation: [4](#0-3) 

In `UnfreezeBalanceProcessor` (legacy FreezeV1 TVM path), the account's frozen balance/`tronPower` is decremented first (lines 108-203, mutating `frozenList`/`AccountResource` and calling `repo.updateAccount`), and only afterward does the code look at whether `getTronPower() < usedTronPower * TRX_PRECISION` to decide whether to call `withdrawReward` at all: [5](#0-4) 

Because `withdrawReward`'s snapshot mechanism (`updateBeginCycle`/`updateEndCycle`/`updateAccountVote`) is the only mechanism that locks in reward for a cycle based on the exact `votesList` that was active during that cycle, skipping the call here means the reward computation deferred to the next `withdrawReward` invocation will instead be computed with `computeReward` using whatever vote list exists at that future point. If the account's votes are later modified, cleared, or partially withdrawn (e.g., via `VoteWitnessActuator`/`VoteWitnessProcessor`, both of which correctly call `withdrawReward` first) before a future `withdrawReward` call occurs, the un-snapshotted cycles between the unfreeze and the next reward pull are computed against an incorrect (stale or already-changed) vote count context, because `beginCycle`/`endCycle` bookkeeping in `DelegationStore` was never advanced/anchored at the unfreeze point. This is structurally the same defect class as the audit finding: state that should be causally tied to a value at the moment of a balance-affecting operation (the interest/reward accrued against the old `pos.underlyingAmount`/vote weight) is instead computed later against a different reference state, permanently orphaning the value that should have been credited at the unfreeze boundary.

### Impact Explanation
Any TRX holder who froze TRX for bandwidth/energy the old way (`FreezeBalanceContract`) via a smart-contract-triggered `unfreezeBalance` TVM opcode, and who is also voting for witnesses under the reward-accrual model (`allowTvmVote`/`useNewRewardAlgorithm`), can have witness voting reward silently miscalculated/lost whenever their remaining `tronPower` after unfreezing is still ≥ their currently cast vote count. This is an accounting-corruption bug affecting on-chain reward distribution for voters interacting via smart contracts, not merely a UX inconvenience — the lost reward is not recoverable by the affected account through any other transaction, matching the "permanently locked" impact bar.

### Likelihood Explanation
The path is reachable from any account triggering a smart contract that calls the corresponding TVM opcode for `unfreezeBalance` (old FreezeV1), which is a standard, unprivileged operation exposed to any TRX holder. The precondition (`tronPower` still exceeding the vote count after unfreeze) is common for accounts that only partially unfreeze bandwidth/energy while still holding sufficient other frozen balance (e.g., tron-power freeze) to back their votes — this is an ordinary, non-adversarial usage pattern, making it moderately likely to occur in practice given active use of both TVM freeze/unfreeze contracts and TVM voting.

### Recommendation
Move the `VoteRewardUtil.withdrawReward(ownerAddress, repo)` call in `UnfreezeBalanceProcessor.execute()` to the top of the method (mirroring `UnfreezeBalanceV2Processor` and `UnfreezeBalanceActuator`), so reward is always snapshotted against the account's vote state *before* the frozen balance/tron power is reduced, regardless of whether the post-unfreeze `tronPower` still covers the existing vote count. The conditional block should retain only the decision of whether votes need to be reduced/cleared, not gate the reward snapshot itself.

### Proof of Concept
1. Deploy a contract account; via `freezeBalance` TVM opcode, freeze TRX under the legacy model for both `BANDWIDTH` and `TRON_POWER` (or `ENERGY` sized generously) so `tronPower` comfortably exceeds intended vote weight.
2. Vote for a witness via the `voteWitness` TVM opcode using a vote count well below `tronPower`.
3. Let one or more maintenance cycles pass so `DelegationStore` accumulates `Vi` for the witness (reward accrues for the account's `beginCycle..currentCycle` window based on the vote it cast).
4. Trigger `unfreezeBalance` (BANDWIDTH) via TVM on the same contract account, choosing an amount such that remaining `tronPower` is still ≥ the account's current vote count (`usedTronPower * TRX_PRECISION`). Because the condition at `UnfreezeBalanceProcessor.java:210` is false, `VoteRewardUtil.withdrawReward` is skipped and no `beginCycle`/`accountVote` snapshot advance occurs.
5. Subsequently reduce or clear the vote through another path (e.g., trigger `voteWitness` with a smaller amount, or another unfreeze that does hit the `withdrawReward` branch and clears votes) before ever manually pulling reward.
6. Observe that `queryReward`/`withdrawReward` recomputes reward for the un-anchored cycle range using the *new* (reduced) vote list rather than the vote weight that was actually in effect during those cycles, producing a smaller reward than the amount that had actually accrued, with the difference permanently unrecoverable.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L104-224)
```java
  public long execute(UnfreezeBalanceParam param, Repository repo) {
    byte[] ownerAddress = param.getOwnerAddress();
    byte[] receiverAddress = param.getReceiverAddress();

    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    long oldBalance = accountCapsule.getBalance();
    long unfreezeBalance = 0L;

    if (param.isDelegating()) {
      byte[] key = DelegatedResourceCapsule.createDbKey(ownerAddress, receiverAddress);
      DelegatedResourceCapsule delegatedResourceCapsule = repo.getDelegatedResource(key);

      // reset delegated resource and deduce delegated balance
      switch (param.getResourceType()) {
        case BANDWIDTH:
          unfreezeBalance = delegatedResourceCapsule.getFrozenBalanceForBandwidth();
          delegatedResourceCapsule.setFrozenBalanceForBandwidth(0, 0);
          accountCapsule.addDelegatedFrozenBalanceForBandwidth(-unfreezeBalance);
          break;
        case ENERGY:
          unfreezeBalance = delegatedResourceCapsule.getFrozenBalanceForEnergy();
          delegatedResourceCapsule.setFrozenBalanceForEnergy(0, 0);
          accountCapsule.addDelegatedFrozenBalanceForEnergy(-unfreezeBalance);
          break;
        default:
          //this should never happen
          break;
      }
      repo.updateDelegatedResource(key, delegatedResourceCapsule);

      // take back resource from receiver account
      AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
      if (receiverCapsule != null) {
        switch (param.getResourceType()) {
          case BANDWIDTH:
            receiverCapsule.safeAddAcquiredDelegatedFrozenBalanceForBandwidth(-unfreezeBalance,
                VMConfig.disableJavaLangMath());
            break;
          case ENERGY:
            receiverCapsule.safeAddAcquiredDelegatedFrozenBalanceForEnergy(-unfreezeBalance,
                VMConfig.disableJavaLangMath());
            break;
          default:
            //this should never happen
            break;
        }
        repo.updateAccount(receiverCapsule.createDbKey(), receiverCapsule);
      }

      // increase balance of owner
      accountCapsule.setBalance(oldBalance + unfreezeBalance);
    } else {
      switch (param.getResourceType()) {
        case BANDWIDTH:
          List<Protocol.Account.Frozen> frozenList = Lists.newArrayList();
          frozenList.addAll(accountCapsule.getFrozenList());
          Iterator<Protocol.Account.Frozen> iterator = frozenList.iterator();
          long now = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
          while (iterator.hasNext()) {
            Protocol.Account.Frozen next = iterator.next();
            if (next.getExpireTime() <= now) {
              unfreezeBalance += next.getFrozenBalance();
              iterator.remove();
            }
          }
          accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
              .setBalance(oldBalance + unfreezeBalance)
              .clearFrozen().addAllFrozen(frozenList).build());
          break;
        case ENERGY:
          unfreezeBalance = accountCapsule.getAccountResource().getFrozenBalanceForEnergy()
              .getFrozenBalance();
          Protocol.Account.AccountResource newAccountResource =
              accountCapsule.getAccountResource().toBuilder()
              .clearFrozenBalanceForEnergy().build();
          accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
              .setBalance(oldBalance + unfreezeBalance)
              .setAccountResource(newAccountResource).build());
          break;
        default:
          //this should never happen
          break;
      }

    }

    // adjust total resource, used to be a bug here
    switch (param.getResourceType()) {
      case BANDWIDTH:
        repo.addTotalNetWeight(-unfreezeBalance / TRX_PRECISION);
        break;
      case ENERGY:
        repo.addTotalEnergyWeight(-unfreezeBalance / TRX_PRECISION);
        break;
      default:
        //this should never happen
        break;
    }

    repo.updateAccount(accountCapsule.createDbKey(), accountCapsule);

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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L71-76)
```java
    byte[] ownerAddress = unfreezeBalanceContract.getOwnerAddress().toByteArray();

    //
    mortgageService.withdrawReward(ownerAddress);

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L118-126)
```java
  public long execute(UnfreezeBalanceV2Param param, Repository repo) {
    byte[] ownerAddress = param.getOwnerAddress();
    long unfreezeBalance = param.getUnfreezeBalance();
    VoteRewardUtil.withdrawReward(ownerAddress, repo);

    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    long now = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();

    long unfreezeExpireBalance = this.unfreezeExpire(accountCapsule, now);
```
