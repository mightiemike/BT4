### Title
Missing cooldown enforcement in TVM `withdrawReward` native contract path allows unrestricted reward withdrawal, bypassing the 24-hour `WithdrawBalanceContract` throttle - (File: `actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawRewardProcessor.java`)

### Summary
The reported Sui bug class is: a state-mutating function (`suifren_update_last_epoch_mixed`) that is supposed to be gated by a cooldown check is instead reachable through a path that skips that check, letting a caller manipulate the guarded field directly and bypass the invariant enforced elsewhere (`capy_labs::mix`). The java-tron analog is the `allowance` / `latestWithdrawTime` account fields, which are guarded by a 24-hour cooldown in the ordinary `WithdrawBalanceContract` actuator, but the equivalent TVM native-contract path (`Program.withdrawReward()` → `WithdrawRewardProcessor`) mutates the very same fields without performing that cooldown check at all.

### Finding Description
`WithdrawBalanceActuator.validate()` enforces a mandatory waiting period before an account can withdraw its accumulated `allowance`: [1](#0-0) 

This check compares `accountCapsule.getLatestWithdrawTime()` against `now` using `dynamicStore.getWitnessAllowanceFrozenTime() * FROZEN_PERIOD`, and on success `execute()` updates `balance`, `allowance`, and `latestWithdrawTime` together: [2](#0-1) 

However, the TVM-reachable equivalent, invoked via the `WITHDRAWREWARD` TVM opcode from `Program.withdrawReward()`, calls `WithdrawRewardProcessor`, whose `validate()` only checks whether the address is a genesis "guard representative" — it performs no cooldown/time check whatsoever: [3](#0-2) 

`execute()` then mutates the same `balance`, `allowance`, and `latestWithdrawTime` fields as the actuator path, but with no restriction on how often this can be triggered: [4](#0-3) 

This is reachable from `Program.withdrawReward()`, which any deployed smart contract can invoke on every call/transaction, fully unprivileged: [5](#0-4) 

A structurally identical unrestricted path exists in `withdrawRewardAndCancelVote()` (triggered on contract `SUICIDE`), which also updates `latestWithdrawTime` without any cooldown check: [6](#0-5) 

This mirrors the Sui bug class exactly: the field intended to gate a rate-limited operation (`last_epoch_mixed` / `latestWithdrawTime`) is protected in one code path (`capy_labs::mix` / `WithdrawBalanceActuator.validate`) but is writable through another, unrestricted, publicly reachable path (`suifren_update_last_epoch_mixed` / `WithdrawRewardProcessor`/`Program.withdrawReward`).

### Impact Explanation
Any account (or any contract acting on an account's behalf, including via `DELEGATECALL`-style internal contract logic) can call the TVM `WITHDRAWREWARD` opcode as often as desired, e.g. once per transaction/block, to flush its accumulated `allowance` into `balance` and reset `latestWithdrawTime`, completely bypassing the 24-hour throttle that `WithdrawBalanceContract` enforces for the same underlying account state. This breaks the intended invariant that withdrawal frequency for witness/voting allowance is rate-limited, and could be used to defeat downstream logic or monitoring that assumes withdrawals are bounded to once per `FROZEN_PERIOD` window. It does not appear to allow withdrawing funds the account isn't entitled to (the amount is still bounded by `allowance`), so the primary impact is an accounting/rate-limit-invariant violation rather than direct fund theft — this needs to be weighed against the original report's own scope (bypass of a rate limiter), which is a legitimate but comparatively lower-severity class than fund theft or consensus divergence.

### Likelihood Explanation
High reachability: the trigger is a plain `TriggerSmartContractContract` transaction executing the `WITHDRAWREWARD` TVM opcode (available whenever `VMConfig.allowTvmVote()` is enabled), requiring no privileged role, no leaked keys, and no cooperating malicious peer — any address with nonzero `allowance` can exploit it directly and repeatedly.

### Recommendation
Add the same `latestWithdrawTime` / `witnessAllowanceFrozenTime` cooldown check (as in `WithdrawBalanceActuator.validate()`) to `WithdrawRewardProcessor.validate()`, and apply the same check inside `Program.withdrawRewardAndCancelVote()` before allowing balance/allowance mutation, so both entry points enforce a single, consistent invariant on withdrawal frequency.

### Proof of Concept
1. Deploy a simple contract whose fallback/entry function calls the TVM native `WithdrawReward` builtin (as exercised in `framework/src/test/java/org/tron/common/runtime/vm/VoteTest.java`).
2. Ensure the calling account has accrued `allowance` > 0 (e.g. via voting rewards).
3. Call the contract's withdraw function repeatedly (multiple transactions in immediate succession, well within the 24-hour `witnessAllowanceFrozenTime` window).
4. Observe that each call succeeds and updates `balance`/`allowance`/`latestWithdrawTime`, whereas an equivalent sequence of `WithdrawBalanceContract` transactions from the same account within 24 hours would be rejected by `WithdrawBalanceActuator.validate()` with `"The last withdraw time is ..., less than 24 hours"`. [1](#0-0)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L57-68)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L121-128)
```java
    long latestWithdrawTime = accountCapsule.getLatestWithdrawTime();
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    long witnessAllowanceFrozenTime = dynamicStore.getWitnessAllowanceFrozenTime() * FROZEN_PERIOD;

    if (now - latestWithdrawTime < witnessAllowanceFrozenTime) {
      throw new ContractValidateException("The last withdraw time is "
          + latestWithdrawTime + ", less than 24 hours");
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

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L697-725)
```java
  private void withdrawRewardAndCancelVote(byte[] owner, Repository repo) {
    VoteRewardUtil.withdrawReward(owner, repo);

    AccountCapsule ownerCapsule = repo.getAccount(owner);
    if (!ownerCapsule.getVotesList().isEmpty()) {
      VotesCapsule votesCapsule = repo.getVotes(owner);
      if (votesCapsule == null) {
        votesCapsule = new VotesCapsule(ByteString.copyFrom(owner),
            ownerCapsule.getVotesList());
      } else {
        votesCapsule.clearNewVotes();
      }
      ownerCapsule.clearVotes();
      ownerCapsule.setOldTronPower(0);
      repo.updateVotes(owner, votesCapsule);
    }
    try {
      long balance = ownerCapsule.getBalance();
      long allowance = ownerCapsule.getAllowance();
      ownerCapsule.setInstance(ownerCapsule.getInstance().toBuilder()
          .setBalance(addExact(balance, allowance, VMConfig.disableJavaLangMath()))
          .setAllowance(0)
          .setLatestWithdrawTime(getTimestamp().longValue() * 1000)
          .build());
      repo.updateAccount(ownerCapsule.createDbKey(), ownerCapsule);
    } catch (ArithmeticException e) {
      throw new BytecodeExecutionException("Suicide: balance and allowance out of long range.");
    }
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
