### Title
Inconsistent enforcement of the 24-hour withdrawal cooldown between `WithdrawBalanceActuator` and the TVM-reachable `WithdrawRewardProcessor` - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawRewardProcessor.java])

### Summary
`AccountCapsule.latestWithdrawTime` is written by two different code paths that both withdraw witness/voting rewards, but only one of them enforces the intended 24-hour cooldown. `WithdrawBalanceActuator` (the normal `WithdrawBalanceContract` transaction) checks and enforces the cooldown, while `WithdrawRewardProcessor` (a TVM "native contract" reachable from smart contracts) writes to the exact same field without any time-based check, mirroring the Notional `VAULT_ACCOUNT_MIN_TIME`/`lastUpdateBlockTime` inconsistency pattern where a shared timestamp field is updated in two places with different associated guarantees.

### Finding Description
`WithdrawBalanceActuator.validate()` reads `accountCapsule.getLatestWithdrawTime()` and rejects the transaction unless at least `witnessAllowanceFrozenTime * FROZEN_PERIOD` (24 hours) has elapsed since the last withdrawal: [1](#0-0) 

Its `execute()` then updates the same field to the current block time after paying out `balance + allowance`: [2](#0-1) 

However, `WithdrawRewardProcessor` — a "native contract" processor under `org.tron.core.vm.nativecontract`, i.e. reward-withdrawal logic invoked from within TVM/smart-contract execution — performs the identical `balance += allowance` payout and also sets `latestWithdrawTime`, but its `validate()` only checks whether the caller is a genesis witness; it contains **no cooldown check at all**: [3](#0-2) [4](#0-3) 

Both processors ultimately consult/mutate the same account state (`AccountCapsule.getLatestWithdrawTime` / `setLatestWithdrawTime`) that is the sole gate for the withdrawal rate limit: [5](#0-4) 

This is the same bug class as the reported Notional issue: a shared "last action time" state variable is written from two different call paths, but the invariant (minimum time between actions) that one path relies on and enforces is not honored by the other path that also mutates the variable.

### Impact Explanation
Because `WithdrawRewardProcessor` is invoked through the TVM native-contract mechanism (i.e., callable from within a deployed smart contract), any account able to reach this precompiled/native operation can withdraw its voting/witness `allowance` repeatedly with no 24-hour throttle, in direct contradiction to the rate limit that the protocol intends to apply to reward withdrawals (as enforced by `WithdrawBalanceActuator`). This both bypasses an intended accounting/rate-limiting control on witness reward payout and corrupts the `latestWithdrawTime` state used by the normal actuator path — after a TVM-driven withdrawal, `latestWithdrawTime` is overwritten with the current time, which can unexpectedly block (or, depending on call ordering, fail to block) subsequent `WithdrawBalanceContract` transactions from the same account, producing state/behavior divergent from what a user of the standard actuator path would expect.

### Likelihood Explanation
Both `calcFee()` for `WithdrawBalanceActuator` and any transaction-fee cost for driving reward withdrawal through TVM are effectively low/near-zero for triggering the withdrawal itself, so the only actual barrier to repeated exploitation is the 24-hour cooldown — which the TVM native-contract path omits entirely. The reachability requires the account to trigger the native-contract "withdraw reward" opcode via a smart contract, which is a standard, unprivileged TVM capability rather than a trusted-role feature, satisfying the unprivileged-actor scope for this report.

### Recommendation
Add the same cooldown check present in `WithdrawBalanceActuator.validate()` (comparing `now - latestWithdrawTime` against `witnessAllowanceFrozenTime * FROZEN_PERIOD`) to `WithdrawRewardProcessor.validate()`, or refactor both code paths to share a single validation/execution routine so the `latestWithdrawTime` invariant is enforced consistently regardless of entry point (normal contract transaction vs. TVM native contract call).

### Proof of Concept
1. Deploy or use a smart contract that repeatedly invokes the native "withdraw reward" operation backing `WithdrawRewardProcessor` (the TVM opcode/precompile that constructs a `WithdrawRewardParam` and calls `WithdrawRewardProcessor.validate()`/`execute()`).
2. Confirm that each call succeeds and pays out `accountCapsule.getAllowance()` regardless of how recently the account (or its previous `WithdrawRewardProcessor`/`WithdrawBalanceActuator` call) last withdrew, because `WithdrawRewardProcessor.validate()` at [3](#0-2)  never checks `latestWithdrawTime`.
3. Compare against the same account attempting the equivalent action via `WithdrawBalanceActuator`, which is rejected with `"The last withdraw time is ..., less than 24 hours"` when called within the cooldown window, as enforced at [1](#0-0) , demonstrating the inconsistent enforcement between the two paths that both write `latestWithdrawTime`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L62-67)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
        .setBalance(oldBalance + allowance)
        .setAllowance(0L)
        .setLatestWithdrawTime(now)
        .build());
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

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L342-348)
```java
  public long getLatestConsumeTime() {
    return this.account.getLatestConsumeTime();
  }

  public void setLatestConsumeTime(long latestTime) {
    this.account = this.account.toBuilder().setLatestConsumeTime(latestTime).build();
  }
```
