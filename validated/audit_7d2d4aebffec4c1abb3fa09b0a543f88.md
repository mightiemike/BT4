Based on my investigation, I found a strong analog to the reported bug class: two independently-maintained implementations of the same accounting/pricing calculation that can diverge, exactly mirroring the `virtualPrice()` vs `_updatePrices()` divergence in the original report.

### Title
Duplicate reward-computation logic (`MortgageService.queryReward`/`withdrawReward` vs `VoteRewardUtil.queryReward`/`withdrawReward`) can diverge, corrupting witness reward accounting - (File: chainbase/src/main/java/org/tron/core/service/MortgageService.java, actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java)

### Summary
java-tron maintains two parallel implementations of the vote-reward "price"/accounting calculation for the exact same underlying data (per-cycle witness `Vi` reward index): `MortgageService.queryReward`/`withdrawReward`/`computeReward` (used by the legacy `WithdrawBalanceActuator`/`UnfreezeBalanceActuator` and by RPC `getReward`), and `VoteRewardUtil.queryReward`/`withdrawReward`/`computeReward` (used by the TVM native `withdrawreward`/`rewardBalance` precompiles reachable from smart contracts). This is structurally the same anti-pattern flagged in the IdleCDO report: the "read" price/value function and the "state-mutating" function are implemented twice, in different modules, and any divergence between them can violate accounting invariants such as `queried reward == actually paid reward`.

### Finding Description
`MortgageService` implements the account-level reward bookkeeping used by ordinary transactions (`WithdrawBalanceActuator.execute` calls `mortgageService.withdrawReward(...)`, RPC `getRewardInfoCommon` calls `mortgageService.queryReward(...)`): [1](#0-0) [2](#0-1) [3](#0-2) 

Separately, `VoteRewardUtil` re-implements the identical algorithm for the TVM path (invoked from `WithdrawRewardProcessor.execute`, reachable via a `withdrawreward`/`rewardBalance` opcode from any smart contract): [4](#0-3) [5](#0-4) [6](#0-5) 

The two implementations are not literally identical: `MortgageService.computeReward` contains an additional "old reward algorithm" branch (`getOldReward`, gated by `newRewardAlgorithmEffectiveCycle` and `allowOldRewardOpt`) that switches between per-cycle iteration and `RewardViCalService`, a code path that has no counterpart in `VoteRewardUtil.computeReward`, which unconditionally uses the `Vi`-delta BigInteger formula: [3](#0-2) [7](#0-6) [5](#0-4) 

Because `VoteRewardUtil` is only gated by `VMConfig.allowTvmVote()` and does not check/branch on `newRewardAlgorithmEffectiveCycle`/`allowOldRewardOpt`, an account whose reward history spans the "old" and "new" reward-algorithm cycles can receive a different computed reward depending on whether the reward is queried/withdrawn through the ordinary `WithdrawBalanceActuator`/`getReward` RPC path (`MortgageService`) versus through a smart-contract `withdrawreward` call (`VoteRewardUtil`), for the exact same underlying `beginCycle`/`endCycle`/vote state.

### Impact Explanation
If the two implementations diverge for accounts whose vote history straddles the old/new reward-algorithm boundary, an account could withdraw a different (potentially larger) reward amount via the TVM path than what `MortgageService`/off-chain tooling would compute and expect, or vice versa, leading to inconsistent `allowance`/`delegationStore` reward-pool accounting between the two code paths. This is an accounting-corruption class issue analogous to the IdleCDO price divergence: any place that assumes "reward computed = reward paid" (e.g., total reward pool invariants, off-chain audits using `getReward` RPC) can be violated by transactions routed through the TVM `withdrawreward` opcode.

### Likelihood Explanation
Likelihood is limited by two factors: (1) `VoteRewardUtil` is only active when `VMConfig.allowTvmVote()` is enabled (a chain-level feature flag), and (2) divergence only manifests for accounts with reward history spanning the old→new reward algorithm transition cycle, which is a narrowing window tied to `newRewardAlgorithmEffectiveCycle`. Within that window, however, the divergence is triggerable by any unprivileged account/contract simply calling the standard `withdrawreward` TVM opcode — no privileged access or malicious peer/node behavior required.

### Recommendation
Consolidate reward computation into a single shared implementation (e.g., have `VoteRewardUtil.computeReward` delegate to the same `computeReward`/`getOldReward` logic path used by `MortgageService`, including the `newRewardAlgorithmEffectiveCycle`/`allowOldRewardOpt` branching), so that the TVM-reachable `withdrawreward` opcode and the ordinary `WithdrawBalanceActuator`/RPC `getReward` path always compute identical results for identical account/cycle state.

### Proof of Concept
Not independently reproducible from static analysis alone — confirming an actual value mismatch requires constructing an account with `beginCycle < newRewardAlgorithmEffectiveCycle < endCycle` and comparing `MortgageService.queryReward(address)` against `VoteRewardUtil.queryReward(address, repo)` for the same delegation-store state at that point (as the codebase's own `RewardAlgorithmNo3`-style tests in `VoteTest.java` already exercise adjacent behavior but do not directly cross-check the two implementations against each other). This gap should be validated with a dedicated unit test comparing both APIs across the algorithm-transition cycle.

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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L136-169)
```java
  public long queryReward(byte[] address) {
    if (!dynamicPropertiesStore.allowChangeDelegation()) {
      return 0;
    }

    AccountCapsule accountCapsule = accountStore.get(address);
    long beginCycle = delegationStore.getBeginCycle(address);
    long endCycle = delegationStore.getEndCycle(address);
    long currentCycle = dynamicPropertiesStore.getCurrentCycleNumber();
    long reward = 0;
    if (accountCapsule == null) {
      return 0;
    }
    if (beginCycle > currentCycle) {
      return accountCapsule.getAllowance();
    }
    //withdraw the latest cycle reward
    if (beginCycle + 1 == endCycle && beginCycle < currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        reward = computeReward(beginCycle, endCycle, account);
      }
      beginCycle += 1;
    }
    //
    endCycle = currentCycle;
    if (CollectionUtils.isEmpty(accountCapsule.getVotesList())) {
      return reward + accountCapsule.getAllowance();
    }
    if (beginCycle < endCycle) {
      reward += computeReward(beginCycle, endCycle, accountCapsule);
    }
    return reward + accountCapsule.getAllowance();
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

**File:** actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java (L16-88)
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

  public static long queryReward(byte[] address, Repository repository) {
    if (!VMConfig.allowTvmVote()) {
      return 0;
    }
    AccountCapsule accountCapsule = repository.getAccount(address);
    long beginCycle = repository.getBeginCycle(address);
    long endCycle = repository.getEndCycle(address);
    long currentCycle = repository.getDynamicPropertiesStore().getCurrentCycleNumber();
    long reward = 0;
    if (accountCapsule == null) {
      return 0;
    }
    if (beginCycle > currentCycle) {
      return accountCapsule.getAllowance();
    }
    //withdraw the latest cycle reward
    if (beginCycle + 1 == endCycle && beginCycle < currentCycle) {
      AccountCapsule account = repository.getAccountVote(beginCycle, address);
      if (account != null) {
        reward = computeReward(beginCycle, endCycle, account, repository);
      }
      beginCycle += 1;
    }
    endCycle = currentCycle;
    if (CollectionUtils.isEmpty(accountCapsule.getVotesList())) {
      return reward + accountCapsule.getAllowance();
    }
    if (beginCycle < endCycle) {
      reward += computeReward(beginCycle, endCycle, accountCapsule, repository);
    }
    return reward + accountCapsule.getAllowance();
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
