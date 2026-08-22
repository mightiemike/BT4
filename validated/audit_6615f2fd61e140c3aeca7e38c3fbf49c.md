### Title
Inconsistent voting-reward computation between actuator path (`MortgageService`) and TVM precompile path (`VoteRewardUtil`) causes divergent reward payouts for the same vote history - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`, `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java`)

### Summary
Just as the reported Registrar bug computes the same conceptual value (registration price) in two different ways depending on the entry point (`register` vs `renew`), java-tron computes the same conceptual value — accumulated SR voting reward for a given account/cycle range — via two independently maintained implementations that are NOT guaranteed to be mathematically identical: `MortgageService.computeReward`/`getOldReward` (used by ordinary broadcast transactions such as `WithdrawBalanceContract`, `VoteWitnessContract`, `UnfreezeBalanceV2Contract`) and `VoteRewardUtil.computeReward` (used when a smart contract invokes the TVM native "vote" / "withdrawReward" / "rewardBalance" precompiles). Users can choose which entry point to withdraw through, and get different reward amounts for identical vote history.

### Finding Description
`MortgageService.withdrawReward` computes rewards with `computeReward(beginCycle, endCycle, accountCapsule)`: [1](#0-0) 

For cycles prior to `newRewardAlgorithmEffectiveCycle`, this delegates to `getOldReward`, which — when `allowOldRewardOpt()` is false — sums per-cycle rewards using **double-precision floating point** (`voteRate * totalReward`): [2](#0-1) [3](#0-2) 

By contrast, `VoteRewardUtil.computeReward`, used by the TVM native-contract processors (`VoteWitnessProcessor`, `WithdrawRewardProcessor`) that back the "vote"/"withdrawReward" precompiles callable from any smart contract, unconditionally uses the **BigInteger fixed-point Vi-delta formula** for the entire `[beginCycle, endCycle)` range, with no branch for cycles before `newRewardAlgorithmEffectiveCycle` and no fallback to the double-based per-cycle algorithm: [4](#0-3) 

Both code paths are reachable directly from ordinary user activity: `MortgageService.withdrawReward` is invoked from `WithdrawRewardProcessor`/`VoteWitnessProcessor`... actually more precisely from `WithdrawBalanceActuator`/`VoteWitnessActuator`/`UnfreezeBalanceV2Actuator` (broadcast transactions), while `VoteRewardUtil.withdrawReward` is invoked from the TVM native-contract processors that implement the same-named precompiles reachable by any deployed smart contract: [5](#0-4) [6](#0-5) 

Because double-precision floating-point rounding and BigInteger/fixed-decimal integer division rounding do not produce bit-identical results in general, the reward amount credited to `AccountCapsule.allowance` for the *same* underlying vote/reward history can differ depending on which of the two code paths is used to trigger the withdrawal — exactly the same class of bug as the reported Registrar issue, where two functions computing the same economic quantity (price vs. reward) diverge because one path applies a calculation/adjustment that the other omits or implements differently.

### Impact Explanation
An account can pick the code path (plain `WithdrawBalanceContract`/`VoteWitnessContract` transaction vs. a smart-contract call to the TVM vote/reward precompiles) that yields it the more favorable rounding outcome, systematically extracting slightly more reward than it is entitled to (or, for the other path, receiving slightly less), which is an accounting/asset-integrity defect on a resource/reward accounting mechanism. Repeated across many cycles and many accounts this is a genuine (if small-magnitude per instance) value-extraction/accounting-corruption vector, and it disincentivizes voters from using one path over the other exactly as the original report describes for renew vs register.

### Likelihood Explanation
The divergence requires votes/rewards accrued in cycles prior to `newRewardAlgorithmEffectiveCycle` while `allowOldRewardOpt` is disabled (the "old" reward computation path), and requires the attacker to route the withdrawal through a smart contract using the TVM vote/reward precompiles instead of a normal transaction (or vice-versa) to realize the more favorable rounding. This is fully reachable by any ordinary account with no special privileges — anyone can deploy a trivial contract that calls the `vote`/`withdrawReward` precompiles. The magnitude of divergence per call is bounded by floating-point rounding error, so exploitation for meaningful gain requires repetition across many accounts/cycles, which is why this is rated as a real but lower-severity discrepancy compared to a critical fund-drain bug.

### Recommendation
Unify the reward computation into a single implementation shared by both `MortgageService` and `VoteRewardUtil` (or have one delegate to the other), ensuring the same rounding/algorithm-selection logic (old double-based vs Vi-based, gated by `newRewardAlgorithmEffectiveCycle` and `allowOldRewardOpt`) is applied identically regardless of whether the reward withdrawal is triggered by a plain transaction or by a TVM precompile call from a smart contract.

### Proof of Concept
Conceptual PoC (cannot be executed without the full build environment, but the code paths are directly traceable):
1. Configure the chain so `allowOldRewardOpt()` is disabled and `newRewardAlgorithmEffectiveCycle` is set to some cycle `N > 1`.
2. Have account `A` vote for SR `W` starting at cycle 1, and let rewards accrue through several cycles that straddle `N` (some cycles `< N`, some `>= N`).
3. Path 1: withdraw via a normal `WithdrawBalanceContract` transaction — this calls `MortgageService.withdrawReward`, which for cycles `< N` uses `getOldReward` (double-precision per-cycle sum).
4. Path 2 (separately, with an identical fresh vote history replicated for account `B`): deploy a trivial contract that calls the TVM `withdrawReward`/`rewardBalance` precompile — this calls `VoteRewardUtil.withdrawReward`/`queryReward`, which always uses the BigInteger Vi-delta formula for the same cycle range, bypassing `getOldReward` entirely.
5. Compare the `allowance`/payout credited to `A` versus `B`: for identical vote/reward inputs, the two payouts are computed via different arithmetic (`double` multiplication in `MortgageService.computeReward(cycle, votes)` at [2](#0-1)  vs. integer `BigInteger` division in `VoteRewardUtil.computeReward` at [4](#0-3) ) and will not, in general, be equal, demonstrating the same "same economic value calculated two different ways depending on code path" class of bug reported in the Registrar `renew` vs `register` discrepancy.

Note: I was unable to fully trace every configuration flag combination (`allowOldRewardOpt`, `disableJavaLangMath`, `supportUnfreezeDelay`) to confirm the exact numeric divergence bound in this session; a background Devin session with full build/test tooling would be needed to construct and run an executable JUnit PoC (e.g., extending `DelegationServiceTest`/`ComputeRewardTest`) that prints the two payout values side-by-side for a controlled reward/vote fixture.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawRewardProcessor.java (L38-68)
```java
  public long execute(WithdrawRewardParam param, Repository repo) throws ContractExeException {
    byte[] ownerAddress = param.getOwnerAddress();

    VoteRewardUtil.withdrawReward(ownerAddress, repo);

    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    long oldBalance = accountCapsule.getBalance();
    long allowance = accountCapsule.getAllowance();
    long newBalance = 0;

    try {
      newBalance = LongMath.checkedAdd(oldBalance, allowance);
    } catch (ArithmeticException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractExeException(e.getMessage());
    }

    // If no allowance, do nothing and just return zero.
    if (allowance <= 0) {
      return 0;
    }

    accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
        .setBalance(newBalance)
        .setAllowance(0L)
        .setLatestWithdrawTime(param.getNowInMs())
        .build());

    repo.updateAccount(accountCapsule.createDbKey(), accountCapsule);
    return allowance;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L39-41)
```java
  public void execute(VoteWitnessParam param, Repository repo) throws ContractExeException {
    byte[] ownerAddress = param.getVoterAddress();
    VoteRewardUtil.withdrawReward(ownerAddress, repo);
```
