### Title
Zero-value `withdrawReward()` TVM native contract is missing the same "reward must be > 0" guard enforced by its sibling actuators - (File: actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawRewardProcessor.java)

### Summary
`WithdrawRewardProcessor`, the TVM native-contract processor backing the Solidity-callable `withdrawReward()` opcode, lets any contract/account invoke it with zero pending reward. Unlike the analogous account-level actuators, `validate()` performs no check that the caller actually has a positive reward/allowance to withdraw, and `execute()` silently proceeds to do unconditional state work before returning `0`.

### Finding Description
The bug class in the external report is: a withdrawal-style entry point accepts (or in this case, is unconditionally reachable with) a zero value, is fully processed, and can therefore be spammed by users who have nothing to withdraw.

In java-tron, every other account-withdrawal actuator explicitly rejects the zero/negative case in `validate()`:

- `WithdrawBalanceActuator.validate()` throws if `accountCapsule.getAllowance() <= 0 && mortgageService.queryReward(ownerAddress) <= 0`. [1](#0-0) 
- `WithdrawExpireUnfreezeActuator.validate()` throws `"no unFreeze balance to withdraw"` when `totalWithdrawUnfreeze <= 0`. [2](#0-1) 
- `UnfreezeBalanceV2Actuator.checkUnfreezeBalance()` requires `unfreezeBalanceV2Contract.getUnfreezeBalance() > 0`. [3](#0-2) 

By contrast, `WithdrawRewardProcessor.validate(...)` — the TVM equivalent invoked from `Program.withdrawReward()` — only checks whether the caller is a genesis-block guard representative; it has no check on `allowance`/reward being positive: [4](#0-3) 

`execute()` then unconditionally calls `VoteRewardUtil.withdrawReward(ownerAddress, repo)` — which performs cycle bookkeeping and can write `updateBeginCycle`, `updateEndCycle`, and `updateAccountVote` to state — before only checking `allowance <= 0` to skip the balance update, silently returning `0` instead of raising an error: [5](#0-4) [6](#0-5) 

The reachable entry point is `Program.withdrawReward()`, callable by any smart contract via the TVM `withdrawReward` native/precompiled opcode, and it always calls `processor.execute(...)` after `validate()` succeeds (which it always does for non-GP addresses regardless of reward amount): [7](#0-6) 

### Impact Explanation
This diverges from the intended invariant — enforced everywhere else in the codebase — that a withdrawal-type operation must reject a no-op (zero-value) claim rather than silently succeed. Any unprivileged contract with zero reward/allowance can repeatedly trigger `withdrawReward()`, causing `VoteRewardUtil.withdrawReward` to run its full cycle-accounting logic (state store writes for begin/end cycle and account-vote snapshots) on every call, producing avoidable state churn analogous to the reported zero-amount withdrawal spam. It does not directly move funds or corrupt balances (the `allowance <= 0` short-circuit prevents balance changes), so the impact is limited to unnecessary/underpriced state writes rather than fund loss or accounting corruption.

### Likelihood Explanation
High reachability: the opcode is callable from any deployed smart contract with no privilege requirement and no cost gate tied to whether a reward actually exists, so it is trivial for any user to trigger the no-op path repeatedly.

### Recommendation
Add a check in `WithdrawRewardProcessor.validate()` (mirroring `WithdrawBalanceActuator` and `WithdrawExpireUnfreezeActuator`) that throws `ContractValidateException` when the queried/pending reward and current `allowance` are both `<= 0`, so zero-value withdraw calls are rejected before any state mutation occurs in `VoteRewardUtil.withdrawReward`.

### Proof of Concept
1. Deploy a contract with no votes and no accumulated allowance (fresh account, never voted / never received rewards).
2. Repeatedly call the TVM `withdrawReward()` builtin from that contract.
3. Observe that `WithdrawRewardProcessor.validate()` never throws (no reward check exists), and `execute()` runs `VoteRewardUtil.withdrawReward`, performing cycle bookkeeping writes each time, before returning `0` — i.e., the "withdrawal" always succeeds despite there being nothing to withdraw, exactly mirroring the reported `requestWithdrawal(0)` spam pattern.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L130-133)
```java
    if (accountCapsule.getAllowance() <= 0 &&
        mortgageService.queryReward(ownerAddress) <= 0) {
      throw new ContractValidateException("witnessAccount does not have any reward");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java (L107-112)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    List<UnFreezeV2> unfrozenV2List = accountCapsule.getInstance().getUnfrozenV2List();
    long totalWithdrawUnfreeze = getTotalWithdrawUnfreeze(unfrozenV2List, now);
    if (totalWithdrawUnfreeze <= 0) {
      throw new ContractValidateException("no unFreeze balance to withdraw ");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L221-224)
```java
    if (unfreezeBalanceV2Contract.getUnfreezeBalance() > 0
        && unfreezeBalanceV2Contract.getUnfreezeBalance() <= frozenAmount) {
      checkOk = true;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawRewardProcessor.java (L21-36)
```java
  public void validate(WithdrawRewardParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    byte[] ownerAddress = param.getOwnerAddress();

    boolean isGP = CommonParameter.getInstance()
        .getGenesisBlock().getWitnesses().stream().anyMatch(witness ->
            Arrays.equals(ownerAddress, witness.getAddress()));
    if (isGP) {
      throw new ContractValidateException(
          ACCOUNT_EXCEPTION_STR + StringUtil.encode58Check(ownerAddress)
              + "] is a guard representative and is not allowed to withdraw Balance");
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawRewardProcessor.java (L38-58)
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
