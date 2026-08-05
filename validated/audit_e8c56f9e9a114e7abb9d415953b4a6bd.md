### Title
Silent swallowing of `withdrawReward` failures in TVM native `withdrawReward()` allows stale reward accounting divergence - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
The Lybra `esLBR` bug is caused by wrapping a mandatory reward-checkpoint call (`refreshReward()`) in a try/catch, so that when it reverts, the balance-changing operation (`mint`/`burn`) still completes while the reward accounting checkpoint is skipped, allowing the balance and the recorded reward-basis to diverge and be manipulated. In java-tron's TVM staking path, `Program.withdrawReward()` invokes `WithdrawRewardProcessor` (which itself calls `VoteRewardUtil.withdrawReward()` to checkpoint/roll forward the voter's begin/end cycle and allowance) inside a try/catch that catches `ContractValidateException` and `ContractExeException` and, on failure, merely logs a warning, rejects the internal transaction, and returns `0` — without re-throwing or halting the enclosing contract execution. [1](#0-0) 

### Finding Description
`Program.withdrawReward()` is the TVM opcode-level handler that a smart contract invokes to withdraw its accumulated voting reward. It builds a `WithdrawRewardParam`, then calls `WithdrawRewardProcessor.validate()`/`execute()` inside a repository child scope, wrapped in a try/catch for both `ContractValidateException` and `ContractExeException`: [2](#0-1) 

`WithdrawRewardProcessor.execute()` first calls `VoteRewardUtil.withdrawReward(ownerAddress, repo)`, which is the checkpoint logic that computes reward accrued since `beginCycle`, adjusts the account's `allowance`, and rolls `beginCycle`/`endCycle` forward to the current cycle: [3](#0-2) 

Only after this checkpoint does the processor add `allowance` to `balance` and reset `allowance` to 0: [4](#0-3) 

Because `repository.commit()` is only called after `processor.execute()` completes successfully inside the try block, an exception thrown partway through `execute()` — e.g., the `ArithmeticException`-derived `ContractExeException` from `LongMath.checkedAdd(oldBalance, allowance)` — causes the whole repository child to be discarded (nothing committed), and the outer catch block simply swallows the failure, logs a warning, and returns `0`, allowing contract execution to continue as if nothing happened. This exactly matches the reported bug class: a mandatory reward-checkpoint operation is made "optional" from the caller's perspective via try/catch, so failures do not halt or revert the surrounding state transition (vote/freeze/unfreeze changes elsewhere in the same transaction can still proceed), while the reward-basis checkpoint silently fails to record.

This differs from the direct actuator path (`WithdrawBalanceActuator`, `UnfreezeBalanceV2Actuator`), which call `mortgageService.withdrawReward()` unconditionally with no try/catch around it — those paths are not vulnerable to this analog because a failure there propagates and aborts the whole actuator execution. [5](#0-4) 

### Impact Explanation
If `WithdrawRewardProcessor.execute()` fails after `VoteRewardUtil.withdrawReward()` has already mutated `beginCycle`/`endCycle`/`allowance` state on the child `Repository` (which is then rolled back since `repository.commit()` is never reached), the effect is limited to that child repository being discarded entirely — so in the current code, this specific try/catch mostly acts as a fail-safe that discards the whole operation atomically via the repository-child/commit pattern, rather than allowing partial state commits. The primary conceptually exploitable window is narrower here than in the Solidity bug (which had no analogous atomic rollback), because `repo.commit()` gates all persistence. However, this pattern is still a code-quality/robustness concern: any future refactor that moves stake/vote/freeze mutations outside this repository-child scope, or that calls `VoteRewardUtil.withdrawReward()` directly against the parent repository elsewhere (as `VoteWitnessProcessor` and `UnfreezeBalanceV2Processor`/`UnfreezeBalanceProcessor` do), reintroduces the exact divergence risk: reward checkpoint recorded with a stale vote/stake balance while other unrelated operations in the same call proceed, or vice versa.

### Likelihood Explanation
Low-to-moderate. Triggering `ContractExeException`/`ContractValidateException` inside `WithdrawRewardProcessor.execute()` requires either balance overflow (`LongMath.checkedAdd` — requires near-`Long.MAX_VALUE` balances, impractical) or being a genesis-block guard representative (validate-time rejection, not attacker controlled by a normal user). No unprivileged, economically realistic path was found in this codebase to force the swallowed failure while other reward-relevant state changes in the same transaction still commit, because the surrounding repository-child + explicit `commit()` pattern makes the whole operation effectively atomic. This is a weaker/lower-likelihood analog than the original Lybra finding.

### Recommendation
Given the atomic repository-child/commit structure already present, the concrete residual risk is architectural fragility rather than a currently exploitable state divergence: any code path that calls `VoteRewardUtil.withdrawReward()` or `mortgageService.withdrawReward()` should either (a) always propagate failures up so the surrounding contract call fails entirely, or (b) never be wrapped in a broad try/catch that swallows exceptions and returns a "safe-looking" value (like `0`) while other state mutations in the same call proceed uncommitted separately. Audit all call sites of `VoteRewardUtil.withdrawReward()` (`VoteWitnessProcessor`, `WithdrawRewardProcessor`, `Program.withdrawRewardAndCancelVote`) to confirm none of them commit vote/stake changes independently of the reward checkpoint commit.

### Proof of Concept
Not able to construct a concrete unprivileged PoC: the failure conditions for `WithdrawRewardProcessor.execute()` (long overflow, guard-representative check) are not practically triggerable by an ordinary user, and the enclosing `repository.commit()` gating in `Program.withdrawReward()` prevents partial-state persistence in the current code. This is flagged as a defensive/architectural analog rather than a proven exploitable divergence.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2329-2358)
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
    } catch (ContractValidateException e) {
      logger.warn("TVM WithdrawReward: validate failure. Reason: {}", e.getMessage());
    } catch (ContractExeException e) {
      logger.warn("TVM WithdrawReward: execute failure. Reason: {}", e.getMessage());
    }
    if (internalTx != null) {
      internalTx.reject();
    }
    return 0;
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

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L46-56)
```java
    try {
      withdrawBalanceContract = any.unpack(WithdrawBalanceContract.class);
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }

    mortgageService.withdrawReward(withdrawBalanceContract.getOwnerAddress()
        .toByteArray());

```
