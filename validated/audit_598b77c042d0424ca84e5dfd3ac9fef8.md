### Title
Unbounded per-cycle loop in vote-reward calculation causes energy exhaustion / permanent DoS for long-dormant accounts - ([File: chainbase/src/main/java/org/tron/core/service/MortgageService.java])

### Summary
`MortgageService.withdrawReward`/`queryReward` and their TVM-side counterpart `VoteRewardUtil.withdrawReward`/`queryReward` compute a voter's reward by calling `computeReward(beginCycle, endCycle, accountCapsule)`. When the "new reward algorithm" (Vi-based O(1) accumulator) is not yet effective for a given cycle range, or when the `allowOldRewardOpt` optimization has not been activated, the code falls back to `getOldReward`, which iterates once per maintenance cycle between `beginCycle` and `endCycle`:

```java
private long getOldReward(long begin, long end, List<Pair<byte[], Long>> votes) {
  if (dynamicPropertiesStore.allowOldRewardOpt()) {
    return rewardViCalService.getNewRewardAlgorithmReward(begin, end, votes);
  }
  long reward = 0;
  for (long cycle = begin; cycle < end; cycle++) {
    reward += computeReward(cycle, votes);
  }
  return reward;
}
``` [1](#0-0) 

Each loop iteration performs a nested loop over the account's votes list, plus store reads (`delegationStore.getReward`, `getWitnessVote`) per vote per cycle: [2](#0-1) 

This is the same bug class as the external report's `VotingEscrow._checkpoint`/`GaugeController` loops: an on-chain, state-dependent loop bound (number of elapsed maintenance cycles since the account last interacted) that grows without an enforced cap, executed inside a single transaction.

### Finding Description
`beginCycle`/`endCycle` track the last cycle for which an account's reward was settled. If an account (voter) never calls anything that triggers `withdrawReward` (vote, freeze/unfreeze, withdraw reward) for a long period, `currentCycle - beginCycle` grows unbounded with time (maintenance cycles occur automatically, roughly every ~6 hours, independent of user action). The very first time such a dormant account performs any voting-related operation, `computeReward`/`getOldReward` must iterate over every cycle in that gap:

```java
private long computeReward(long beginCycle, long endCycle, AccountCapsule accountCapsule) {
  ...
  if (beginCycle < newAlgorithmCycle) {
    long oldEndCycle = min(endCycle, newAlgorithmCycle, ...);
    reward = getOldReward(beginCycle, oldEndCycle, srAddresses);
    beginCycle = oldEndCycle;
  }
  ...
}
``` [3](#0-2) 

This code path is reached from `withdrawReward(address)`/`queryReward(address)`: [4](#0-3) 

which in turn is invoked from the ordinary vote/unfreeze/withdraw actuator and TVM-native-contract code paths that any account can trigger via a broadcast transaction, e.g. `VoteWitnessActuator.countVoteAccount` calling `mortgageService.withdrawReward(ownerAddress)`: [5](#0-4) 

and the TVM equivalent `VoteRewardUtil.withdrawReward` (same per-cycle loop when `allowOldRewardOpt` is disabled and the range predates `newRewardAlgorithmEffectiveCycle`), invoked from `VoteWitnessProcessor.execute`, `UnfreezeBalanceProcessor.execute`, `UnfreezeBalanceV2Processor`, and `WithdrawRewardProcessor.execute`: [6](#0-5) [7](#0-6) 

Neither `beginCycle`/`endCycle` range nor the number of iterations of the `for (long cycle = begin; cycle < end; cycle++)` loop is capped inside `getOldReward`; the only mitigation is the `allowOldRewardOpt`/`RewardViCalService` optimization, which is itself gated by a committee proposal (`allowOldRewardOpt`) and a chain-wide "new reward algorithm effective cycle" checkpoint. Until/unless those parameters are activated network-wide, or for the range of cycles before that checkpoint became effective, the unbounded per-cycle loop remains the active code path for any voter whose `beginCycle` predates it by a large margin.

### Impact Explanation
If the number of unsettled cycles for an account is large enough that iterating over them (with the nested per-vote store lookups) exceeds the transaction's energy/CPU-time budget, every transaction touching that account's votes (`VoteWitnessContract`, `WithdrawRewardContract`, `UnfreezeBalanceContract`/`UnfreezeBalanceV2Contract`, and their TVM equivalents) will run out of energy or hit `checkCPUTimeLimit`/`OutOfTimeException` and fail deterministically, every time it is attempted — the account becomes permanently unable to vote, unfreeze, or withdraw rewards. This is a self-inflicted DoS against a specific account's normal, unprivileged operations, and it can also degrade node/block processing performance broadly since the same computation runs during block validation.

### Likelihood Explanation
Likelihood is time-dependent rather than requiring an active attacker: any voter (or any account that is delegated votes) who does not interact with the network for a sufficiently long stretch, combined with the reward optimization flags not yet being activated for that historical cycle range, will trigger the unbounded loop the next time they interact. An attacker could also deliberately create many long-lived voting accounts and structure interactions to maximize the cycle gap before finally reactivating them, though the more realistic likelihood driver is simply chain age/inactivity combined with un-activated `allowOldRewardOpt`.

### Recommendation
- Cap the number of cycles processed by `getOldReward`/`computeReward` in a single transaction (batch/checkpoint the settlement across multiple transactions, similar to the report's suggestion to allow multi-transaction catch-up for `GaugeController`).
- Ensure `allowOldRewardOpt` (and the underlying `RewardViCalService` Vi-precomputation) is activated as early and universally as possible, and add defensive bounds so that even before activation the fallback loop cannot process an unbounded number of cycles in one call.
- Add gas/energy stress tests (equivalent to the report's Brownie `--gas`/Echidna gas-fuzzing recommendation) that simulate large cycle gaps to confirm actuators do not run out of energy for legitimately dormant accounts.

### Proof of Concept
1. Deploy/observe a network where `allowOldRewardOpt` is not yet enabled and `newRewardAlgorithmEffectiveCycle` is not yet reached for a large span of cycles (or is far in the future, i.e., `Long.MAX_VALUE` as tracked by `RewardViCalService.newRewardCalStartCycle`) — see `MortgageService.computeReward` branch selection: [8](#0-7) .
2. Have an account vote for a witness, then remain inactive (no vote/withdraw/unfreeze transaction) across a very large number of maintenance cycles (each cycle occurs automatically regardless of user action).
3. Submit a `VoteWitnessContract`, `WithdrawRewardContract`, or `UnfreezeBalanceV2Contract` transaction for that account; internally this calls `MortgageService.withdrawReward`/`VoteRewardUtil.withdrawReward`, which invokes `getOldReward` iterating `cycle` from the account's stale `beginCycle` to `endCycle` [1](#0-0) .
4. With a sufficiently large cycle gap, the transaction's energy usage/CPU time for this single loop exceeds the allowed limit, causing the transaction to fail with an energy/timeout exception every time it is retried, leaving the account's votes/rewards effectively frozen.

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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L171-188)
```java
  private long computeReward(long cycle, List<Pair<byte[], Long>> votes) {
    long reward = 0;
    for (Pair<byte[], Long> vote : votes) {
      byte[] srAddress = vote.getKey();
      long totalReward = delegationStore.getReward(cycle, srAddress);
      if (totalReward <= 0) {
        continue;
      }
      long totalVote = delegationStore.getWitnessVote(cycle, srAddress);
      if (totalVote == DelegationStore.REMARK || totalVote == 0) {
        continue;
      }
      long userVote = vote.getValue();
      double voteRate = (double) userVote / totalVote;
      reward += voteRate * totalReward;
    }
    return reward;
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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L260-269)
```java
  private long getOldReward(long begin, long end, List<Pair<byte[], Long>> votes) {
    if (dynamicPropertiesStore.allowOldRewardOpt()) {
      return rewardViCalService.getNewRewardAlgorithmReward(begin, end, votes);
    }
    long reward = 0;
    for (long cycle = begin; cycle < end; cycle++) {
      reward += computeReward(cycle, votes);
    }
    return reward;
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawRewardProcessor.java (L38-42)
```java
  public long execute(WithdrawRewardParam param, Repository repo) throws ContractExeException {
    byte[] ownerAddress = param.getOwnerAddress();

    VoteRewardUtil.withdrawReward(ownerAddress, repo);

```
