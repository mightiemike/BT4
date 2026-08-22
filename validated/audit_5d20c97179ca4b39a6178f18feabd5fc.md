### Title
Floating-Point Reward Split In Legacy `computeReward` Can Cause Voter Payouts To Exceed The Witness's Allocated Cycle Reward Pool - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
This is the strongest reachable analog of the PoolTogether `TwabRewards.claimRewards` bug class in java-tron: a per-claimant reward calculation that is never checked against the finite pool it is drawn from, executed independently for every claimant, with no aggregate accounting guard.

### Finding Description
`MortgageService.payReward` credits a fixed, bounded reward amount for a specific witness/cycle pair via `delegationStore.addReward(cycle, witnessAddress, value)` [1](#0-0) . That value is exactly analogous to the "total allotted balance provided by the promotion creator" in the PoolTogether report — it is the finite amount that voters of that witness in that cycle are collectively entitled to.

When a delegator withdraws, `MortgageService.withdrawReward` (and the equivalent TVM-reachable `VoteRewardUtil.withdrawReward`) computes that delegator's individual share via the legacy `computeReward(long cycle, List<Pair<byte[], Long>> votes)` helper, which is still exercised through `getOldReward` for any cycle prior to `newRewardAlgorithmEffectiveCycle`: [2](#0-1) 

Each user's share is computed independently as `voteRate = (double) userVote / totalVote; reward += voteRate * totalReward;` using `double` (binary floating point) arithmetic, per witness, per delegator, per cycle. The individual `computeReward` calls performed via `withdrawReward`/`queryReward` never sum across all delegators of the same witness/cycle to check that the total amount actually paid out does not exceed `delegationStore.getReward(cycle, srAddress)` [3](#0-2) [4](#0-3) .

This mirrors the PoolTogether root cause precisely: `_calculateRewardAmount` is invoked per claimant with no invariant that `Σ claimed <= totalRewardsAllocated`. Here, `computeReward` is invoked per delegator withdrawal with no invariant that `Σ withdrawReward(cycle, witness) <= delegationStore.getReward(cycle, witness)`. Because `double` multiplication/division is not exact and not associative, the sum of many small floating-point shares computed independently at different withdrawal times can, in aggregate, diverge from (and potentially exceed) the exact pool value that was credited by `payReward`, `payBlockReward`, or `payTransactionFeeReward`.

The reachable path is fully unprivileged: any account holder who voted for a witness before the cycle where `useNewRewardAlgorithm()`/`newRewardAlgorithmEffectiveCycle` took effect can trigger this path by broadcasting a `WithdrawBalanceContract` (via `WithdrawBalanceActuator`) or by calling the TVM `withdrawReward` opcode from a contract [5](#0-4) [6](#0-5) .

### Impact Explanation
If aggregate floating-point drift causes the sum of `allowance` credited to delegators of a witness/cycle to exceed the actual `getReward(cycle, address)` pool, this constitutes an accounting/asset corruption: TRX balance is minted into user `allowance`/`balance` fields beyond what was actually allotted by the block/fee-reward issuance for that cycle, silently inflating effective circulating supply outside of the documented reward schedule. This is the direct DPoS/reward-accounting analog of "more tokens being sent out than allocated by a promotion creator" — the "promotion" here is the witness's per-cycle reward pool, and the "ticket holders" are the delegators who voted for that witness.

### Likelihood Explanation
Likelihood is bounded because this code path (`getOldReward`/legacy `computeReward`) is only exercised for cycles prior to the new Vi-based algorithm's effective cycle, and `allowOldRewardOpt` further routes those calculations to the safer `RewardViCalService.getNewRewardAlgorithmReward` (BigInteger-based) when enabled [7](#0-6) . So exploitability today depends on whether `allowOldRewardOpt` is active on the network and whether any account still has an un-withdrawn balance/beginCycle predating the algorithm switch. Where that condition holds, the floating-point divergence is deterministic and reachable by any ordinary account simply by calling withdraw — no privileged role or malicious peer is required.

### Recommendation
- Replace the `double`-based `voteRate`/`totalReward` multiplication in `computeReward(long cycle, List<Pair<byte[], Long>> votes)` with exact integer/`BigInteger` arithmetic consistent with the newer Vi-based algorithm, eliminating floating-point rounding in reward distribution entirely.
- Alternatively/additionally, when computing a delegator's share for a given witness/cycle, track and cap the cumulative amount already paid out from that cycle's `delegationStore.getReward(cycle, address)` pool so that the sum of all withdrawals for that witness/cycle can never exceed the credited amount, analogous to the "Σ claimed <= allotted balance" mitigation recommended in the referenced report.

### Proof of Concept
Not applicable — root cause is demonstrated via static code analysis of the deterministic floating-point summation logic in `computeReward(long, List<Pair<byte[], Long>>)` [2](#0-1) , reachable from any account via `WithdrawBalanceActuator.execute` [8](#0-7)  or the TVM `withdrawReward` precompiled opcode path [9](#0-8) .

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L79-87)
```java
  private void payReward(byte[] witnessAddress, long value) {
    long cycle = dynamicPropertiesStore.getCurrentCycleNumber();
    int brokerage = delegationStore.getBrokerage(cycle, witnessAddress);
    double brokerageRate = (double) brokerage / 100;
    long brokerageAmount = (long) (brokerageRate * value);
    value -= brokerageAmount;
    delegationStore.addReward(cycle, witnessAddress, value);
    adjustAllowance(witnessAddress, brokerageAmount);
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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L209-214)
```java
    if (beginCycle < newAlgorithmCycle) {
      long oldEndCycle = min(endCycle, newAlgorithmCycle,
          dynamicPropertiesStore.disableJavaLangMath());
      reward = getOldReward(beginCycle, oldEndCycle, srAddresses);
      beginCycle = oldEndCycle;
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L34-73)
```java
  @Override
  public boolean execute(Object result) throws ContractExeException {
    TransactionResultCapsule ret = (TransactionResultCapsule) result;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    long fee = calcFee();
    final WithdrawBalanceContract withdrawBalanceContract;
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    MortgageService mortgageService = chainBaseManager.getMortgageService();
    try {
      withdrawBalanceContract = any.unpack(WithdrawBalanceContract.class);
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }

    mortgageService.withdrawReward(withdrawBalanceContract.getOwnerAddress()
        .toByteArray());

    AccountCapsule accountCapsule = accountStore.
        get(withdrawBalanceContract.getOwnerAddress().toByteArray());
    long oldBalance = accountCapsule.getBalance();
    long allowance = accountCapsule.getAllowance();

    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
        .setBalance(oldBalance + allowance)
        .setAllowance(0L)
        .setLatestWithdrawTime(now)
        .build());
    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
    ret.setWithdrawAmount(allowance);
    ret.setStatus(fee, code.SUCESS);

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2329-2348)
```java
  public long withdrawReward() {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, owner, 0, null,
        "withdrawReward", nonce, null);

    WithdrawRewardParam param = new WithdrawRewardParam();
    param.setOwnerAddress(owner);
    param.setNowInMs(getTimestamp().longValue() * 1000);
    try {
      WithdrawRewardProcessor processor = new WithdrawRewardProcessor();
      processor.validate(param, repository);
      long allowance = processor.execute(param, repository);
      repository.commit();
      if (internalTx != null) {
        internalTx.setValue(allowance);
      }
      return allowance;
```

**File:** actuator/src/main/java/org/tron/core/vm/OperationActions.java (L921-929)
```java
  public static void withdrawRewardAction(Program program) {
    if (program.isStaticCall()) {
      throw new Program.StaticCallModificationException();
    }

    long allowance = program.withdrawReward();
    program.stackPush(new DataWord(allowance));
    program.step();
  }
```
