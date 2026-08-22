Found a strong direct analog. `UnfreezeBalanceProcessor.execute()` (the TVM native-precompile path reachable from any smart contract via `unfreezeBalance()`) mutates account balance, frozen list, and total resource weight, and only *conditionally* calls `VoteRewardUtil.withdrawReward()` — and only after the balance/weight state has already changed — whereas the transaction-level counterpart `UnfreezeBalanceActuator.execute()` calls `mortgageService.withdrawReward(ownerAddress)` unconditionally at the very start, before any balance/vote state mutation.

### Title
Reward snapshot updated after (and only conditionally on) state mutation in TVM `unfreezeBalance` precompile, unlike the transaction-level actuator - (File: actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java)

### Summary
The Velodrome finding is about a reward-accounting function that must run *before* a user's stake/balance changes; when a public path bypasses that ordering, reward math is computed against already-mutated state and rewards are lost/corrupted. java-tron has the same "reward update must precede balance/vote-affecting state change" invariant, enforced via `MortgageService.withdrawReward` / `VoteRewardUtil.withdrawReward`, and it is inconsistently applied across the two implementations of "unfreeze" logic.

### Finding Description
For the transaction-level `UnfreezeBalanceContract`, `UnfreezeBalanceActuator.execute()` calls `mortgageService.withdrawReward(ownerAddress)` immediately, before `oldBalance`/frozen state is read or mutated: [1](#0-0) 

For the TVM-reachable path (`unfreezeBalance` native contract exposed to any smart contract), `UnfreezeBalanceProcessor.execute()` instead mutates balance, frozen list and `TotalNetWeight`/`TotalEnergyWeight` first, persists the account (`repo.updateAccount`), and only afterward — and only if `accountCapsule.getTronPower() < usedTronPower * TRX_PRECISION` — calls `VoteRewardUtil.withdrawReward(ownerAddress, repo)`: [2](#0-1) 

`VoteRewardUtil.withdrawReward` computes and finalizes the reward for the *current* cycle based on the account's snapshot of `getVotesList()` and the begin/end cycle bookkeeping: [3](#0-2) . Because the account has already been re-persisted with the new (post-unfreeze) tron-power/weight state before this call runs, and because the call is skipped entirely whenever `tronPower >= usedTronPower`, the cycle-snapshot recorded by `repository.updateAccountVote(address, endCycle, accountCapsule)` can capture the *already-mutated* account instead of the pre-mutation snapshot that should be used to close out the prior cycle's reward. This mirrors the Velodrome root cause: the reward bookkeeping step is not unconditionally executed *before* the balance-affecting mutation that the reward math depends on. The sibling `FreezeBalanceV2Processor`/`FreezeBalanceV2Actuator`/`DelegateResourceProcessor` similarly never call `withdrawReward` at all when they increase/move frozen (tron-power) balance, in contrast to `UnfreezeBalanceV2Actuator`/`VoteWitnessActuator`/`WithdrawBalanceActuator`, which uniformly call `withdrawReward` first: [4](#0-3) [5](#0-4) 

### Impact Explanation
If the reward-cycle snapshot is taken after tron-power/weight has already changed, or is skipped for accounts whose used vote count still fits under their (now different) tron power, the per-cycle reward computed by `computeReward`/`adjustAllowance` in `VoteRewardUtil` can diverge from the correct historical vote-weight the account actually held during the cycle being closed. This can let a smart-contract-controlled account (any TRC-20/TRX holder that freezes/votes/unfreezes via a deployed contract, which is fully attacker-controlled and unprivileged) lose or, depending on ordering, unexpectedly gain allowance/reward relative to `computeReward`'s intended per-vote-weight distribution — an accounting/reward corruption reachable from ordinary contract calls, not requiring any privileged role.

### Likelihood Explanation
Reachable by any contract-calling account that freezes v1-resources and votes through a deployed contract, then calls the `unfreezeBalance` precompile directly (which any contract can do, unlike the human-facing wallet flow that always goes through the wrapping transaction contract). This is a pure protocol/state-machine path, not privileged, not P2P, not test-only — it is directly reachable from a broadcast transaction that triggers a smart contract using the freeze/vote/unfreeze precompiles.

### Recommendation
Make `VoteRewardUtil.withdrawReward(ownerAddress, repo)` unconditional and call it at the very start of `UnfreezeBalanceProcessor.execute()` (mirroring `UnfreezeBalanceActuator`, which calls `mortgageService.withdrawReward` before any balance/weight mutation), rather than conditionally after the account has already been re-persisted with the new frozen/weight state. Audit `FreezeBalanceV2Actuator`/`FreezeBalanceV2Processor`/`DelegateResourceActuator`/`DelegateResourceProcessor` for the same missing/late `withdrawReward` call and align all resource-mutating actuators/processors to withdraw/checkpoint rewards before, not after or conditionally around, any tron-power/vote-affecting balance mutation.

### Proof of Concept
1. Deploy a contract, freeze BANDWIDTH via `freezeBalance` (V1), then vote for a witness through the contract so `accountCapsule.getVotesList()` is non-empty and `usedTronPower` roughly equals `tronPower`.
2. Let a reward cycle elapse (maintenance) so a pending reward exists for the account's vote snapshot.
3. Call the `unfreezeBalance` TVM precompile (not the plain `UnfreezeBalanceContract` transaction) to unfreeze BANDWIDTH. Observe in `UnfreezeBalanceProcessor.execute()` that the account's balance/frozen-list/`TotalNetWeight` are mutated and `repo.updateAccount` is called at line 203 before the conditional `VoteRewardUtil.withdrawReward` check at line 210-211 even runs.
4. Compare the resulting `delegationStore`/repository vote-cycle snapshot and computed reward against the value that `VoteRewardUtil.queryReward`/`MortgageService.queryReward` would have returned had `withdrawReward` been called prior to the mutation (as `UnfreezeBalanceActuator` does for the equivalent transaction-level flow) — the two paths for functionally identical unfreeze operations produce different reward-accounting outcomes depending on whether the wallet transaction or the TVM precompile path is used.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L71-77)
```java
    byte[] ownerAddress = unfreezeBalanceContract.getOwnerAddress().toByteArray();

    //
    mortgageService.withdrawReward(ownerAddress);

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
    long oldBalance = accountCapsule.getBalance();
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L188-224)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L69-76)
```java
    byte[] ownerAddress = unfreezeBalanceV2Contract.getOwnerAddress().toByteArray();
    long now = dynamicStore.getLatestBlockHeaderTimestamp();

    mortgageService.withdrawReward(ownerAddress);

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
    long unfreezeAmount = this.unfreezeExpire(accountCapsule, now);
    long unfreezeBalance = unfreezeBalanceV2Contract.getUnfreezeBalance();
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L155-162)
```java
    MortgageService mortgageService = chainBaseManager.getMortgageService();
    byte[] ownerAddress = voteContract.getOwnerAddress().toByteArray();

    VotesCapsule votesCapsule;

    //
    mortgageService.withdrawReward(ownerAddress);

```
