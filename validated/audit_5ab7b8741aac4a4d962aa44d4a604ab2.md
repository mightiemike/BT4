### Title
Native-contract `unfreezebalance` precompile path may skip settling vote rewards before mutating stake/vote state, corrupting reward accounting - (File: `actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java`)

### Summary
The `purger.cairo` report describes a class of bug where a state-mutating operation (`redistribute`) proceeds without first calling the routine that accrues/settles the dependent state (`charge`/interest), because that settlement call is only invoked conditionally on another code path (`shrine.melt`, gated by `can_absorb_any`). The result is state (redistributed debt) computed from stale, unaccrued values. The same "conditional/asymmetric settlement" pattern exists in java-tron's TVM native-contract unfreeze path: `UnfreezeBalanceProcessor.execute` mutates global stake weight and account frozen balance unconditionally, but only calls `VoteRewardUtil.withdrawReward` (the routine analogous to `charge`, which settles/finalizes the VI-based reward accounting) when a specific condition holds.

### Finding Description
In `UnfreezeBalanceProcessor.execute`, the account's frozen balance is cleared and the global total weight is adjusted immediately and unconditionally: [1](#0-0) 

Only afterward, and only if the account's votes list is non-empty **and** its remaining `tronPower` is now less than the votes it already cast, does the processor call `VoteRewardUtil.withdrawReward` to settle/finalize reward accounting and clear the stale votes: [2](#0-1) 

This mirrors the report's root cause exactly: a settlement/accrual routine (`withdrawReward`/`charge`) is invoked conditionally rather than unconditionally before/along with the state mutation it depends on. Compare this with the two other call sites that settle rewards *unconditionally* before making any account/vote change:
- The legacy actuator path calls `mortgageService.withdrawReward(ownerAddress)` first, before any frozen-balance or vote mutation: [3](#0-2) 
- `VoteWitnessProcessor.execute` also calls `VoteRewardUtil.withdrawReward` unconditionally as its first step: [4](#0-3) 

`VoteRewardUtil.withdrawReward` (and its actuator-side counterpart `MortgageService.withdrawReward`) is the function responsible for computing and finalizing VI-based rewards up to the current cycle and advancing `beginCycle`/`endCycle` bookkeeping in `DelegationStore`: [5](#0-4) 

Because `UnfreezeBalanceProcessor` reduces `tronPower`-backing stake and adjusts `addTotalNetWeight`/`addTotalEnergyWeight` before this settlement, an account whose remaining power still covers its existing votes (`accountCapsule.getTronPower() >= usedTronPower * TRX_PRECISION`) will skip `withdrawReward` entirely on this path — leaving its votes counted at their old weight and leaving unsettled reward-cycle bookkeeping stale, even though the underlying frozen stake backing that vote weight has already been reduced and removed from the global total.

### Impact Explanation
When reward settlement is skipped or deferred relative to the stake-reducing mutation, later reward computations (`computeReward` using per-cycle `witnessVi` deltas multiplied by the still-recorded vote count) can be based on a vote weight that no longer corresponds to actual frozen/staked TRX, since votes are only cleared inside the same conditional block that gates `withdrawReward`. This can lead to over- or under-crediting of `allowance` (claimable reward) for the account and to a mismatch between the global total net/energy weight (already decremented) and the still-outstanding vote records used in per-witness reward distribution — an accounting corruption analogous to the "more bad debt than was actually redistributed" impact described in the report.

### Likelihood Explanation
This path is reachable by any smart contract executing the TVM `unfreezebalance`-equivalent precompile/native contract operation (`UnfreezeBalanceProcessor`, gated by `VMConfig.allowTvmVote()`), i.e. from an ordinary broadcast transaction that calls a contract exercising this opcode — no privileged role is required. The asymmetry only manifests for accounts whose remaining `tronPower` after unfreezing still exceeds their currently cast vote total, a state reachable through normal freeze/unfreeze/vote sequences.

### Recommendation
- **Short term:** Call `VoteRewardUtil.withdrawReward(ownerAddress, repo)` unconditionally and before mutating frozen balance / total weight in `UnfreezeBalanceProcessor.execute`, mirroring the ordering already used in `UnfreezeBalanceActuator.execute` and `VoteWitnessProcessor.execute`.
- **Long term:** Establish and enforce an invariant that any operation which can change the "power" backing an account's votes (freeze, unfreeze, transfer, delegate) must settle reward accrual first, and add regression tests asserting this ordering across every actuator/native-contract processor pair.

### Proof of Concept
1. Freeze a balance for `BANDWIDTH`/`ENERGY` via a contract call and vote for a witness such that `usedTronPower` is comfortably below total `tronPower`.
2. Let one or more reward cycles elapse (witness rewards accrue via `payReward`/`delegationStore.addReward`), without ever calling `withdrawReward` for this account.
3. Call the native-contract unfreeze path (`UnfreezeBalanceProcessor.execute`) to unfreeze part of the balance, keeping `accountCapsule.getTronPower() >= usedTronPower * TRX_PRECISION`. Observe: total net/energy weight is decremented immediately (`repo.addTotalNetWeight`/`addTotalEnergyWeight`), but `VoteRewardUtil.withdrawReward` is never invoked because the `if` condition at [6](#0-5)  is false.
4. The account's vote/allowance state remains unsettled while the backing stake and global weight have already shrunk, producing reward accounting that does not match the actual current power distribution.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L190-203)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L71-76)
```java
    byte[] ownerAddress = unfreezeBalanceContract.getOwnerAddress().toByteArray();

    //
    mortgageService.withdrawReward(ownerAddress);

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L39-41)
```java
  public void execute(VoteWitnessParam param, Repository repo) throws ContractExeException {
    byte[] ownerAddress = param.getVoterAddress();
    VoteRewardUtil.withdrawReward(ownerAddress, repo);
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
