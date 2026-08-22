### Title
Unchecked `BigInteger.longValue()` truncation in vote-reward accounting can silently corrupt witness reward payouts - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`, `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java`)

### Summary
Both the standard TRX voting-reward path (`MortgageService.computeReward`) and the TVM vote-reward path (`VoteRewardUtil.computeReward`) accumulate an unbounded, precision-scaled `BigInteger` value (`deltaVi`) and narrow it to a `long` using the unchecked `BigInteger.longValue()` method. Unlike the reported Paladin bug (which reverted on overflow), this codebase's analog silently wraps/truncates on overflow, producing an arbitrary, potentially negative or wildly incorrect `reward` value that is then credited to the user's `allowance`/balance — an accounting-corruption variant of the same unsafe-narrowing-conversion bug class.

### Finding Description
`RewardViCalService.accumulateWitnessVi` maintains a per-witness, per-cycle accumulator `Vi` that is monotonically non-decreasing across the life of the chain: [1](#0-0) 

When a voter withdraws, `computeReward` calculates `deltaVi = endVi - beginVi`, scales it by the voter's `userVote`, divides by `DelegationStore.DECIMAL_OF_VI_REWARD`, and narrows the resulting `BigInteger` to `long` with the unchecked `.longValue()`: [2](#0-1) [3](#0-2) 

This mirrors the `_newRewardPerToken()` → `toUint96()` pattern in the report: a value scaled by a large precision constant, computed from unbounded accumulator state, is narrowed to a fixed-width integer without an overflow check. The critical difference is that Java's `BigInteger.longValue()` (per the JDK spec) does **not** throw on overflow — it silently discards high-order bits — whereas the rest of this same codebase explicitly uses `longValueExact()` in numerous other places (e.g. `ResourceProcessor.getUsage`, `ResourceProcessor.divideCeilExact`, `RepositoryImpl.calculateGlobalEnergyLimit`) specifically to *detect and reject* overflow via `ArithmeticException`: [4](#0-3) 

The reward-accounting code path was not hardened the same way, so an overflow here degrades from "revert/DoS" (as in the Paladin report) to silent value corruption — a strictly worse outcome because it is undetectable at the time it occurs and directly manipulates `AccountCapsule.allowance`, which is later added to `balance`: [5](#0-4) 

### Impact Explanation
If `deltaVi.multiply(userVote)/DECIMAL_OF_VI_REWARD` exceeds `Long.MAX_VALUE`, the wrapped result can be an arbitrary (including negative) `long`. Since `reward` is subsequently added unchecked to `allowance` and eventually to the account's TRX `balance` in `WithdrawRewardProcessor.execute` / `WithdrawBalanceActuator`, this can result in:
- A voter receiving a grossly inflated reward (asset creation / inflation bug), or
- A voter receiving a negative/garbage reward, effectively losing accrued rewards permanently (funds-lock equivalent of the original report).

This is "asset or accounting corruption," which is explicitly within scope.

### Likelihood Explanation
Reaching this condition requires `Vi` (a chain-wide, ever-growing accumulator keyed by witness address) to have grown large enough, combined with the withdrawing voter's `userVote`, that the product exceeds `Long.MAX_VALUE` (~9.22e18) after dividing by `DECIMAL_OF_VI_REWARD`. Because `Vi` never resets and only grows across the lifetime of the network (bounded in practice by cumulative TRX block rewards distributed to that witness, potentially over years), and voters can defer withdrawal indefinitely (`beginCycle`/`endCycle` bookkeeping supports arbitrarily long dormancy), this is a **plausible long-horizon overflow**, not a one-transaction attack. I could not verify the exact numeric value of `DelegationStore.DECIMAL_OF_VI_REWARD` from the index, so I cannot give a precise numeric threshold or timeline; this is an acknowledged gap that would need to be confirmed by reading `DelegationStore.java` directly (a Devin session would have full file access to compute the exact overflow threshold and realistic time-to-trigger under mainnet reward-issuance rates).

### Recommendation
Replace the unchecked `.longValue()` calls in `MortgageService.computeReward` and `VoteRewardUtil.computeReward` with `.longValueExact()` (as already used elsewhere in the codebase for hardened resource math), or perform the addition in `BigInteger` and only narrow once at the very end with an explicit range check, throwing a well-defined exception (and failing the withdraw transaction) rather than silently wrapping. This should be paired with monitoring/alerting rather than a raw uncaught `ArithmeticException`, since a hard revert here would reintroduce the original Paladin-style "reward permanently unclaimable" DoS — the fix must both prevent silent corruption and provide accounts a safe way to still withdraw (e.g., partial/capped withdrawal) if legitimate values ever approach the boundary.

### Proof of Concept
Conceptual (bounded by unknown constant value, see Likelihood section):
1. Witness `W` accrues block rewards every cycle for `N` cycles without any voter withdrawing, causing `witnessVi(W)` to grow monotonically via `accumulateWitnessVi`.
2. A large voter `V` (large `userVote`) who voted for `W` since cycle 0 finally calls `withdrawReward` (via `WithdrawBalanceContract` or the TVM `withdrawReward()` native contract).
3. `computeReward` computes `deltaVi = Vi(endCycle-1) - Vi(beginCycle-1)`, multiplies by `V`'s `userVote`, and divides by `DECIMAL_OF_VI_REWARD`.
4. If the intermediate `BigInteger` result exceeds `Long.MAX_VALUE`, `.longValue()` wraps silently (no exception), yielding a corrupted `reward` that is added to `V`'s `allowance`/`balance`.

Verifying the exact `N`/`userVote` combination needed to trigger this requires reading the concrete value of `DelegationStore.DECIMAL_OF_VI_REWARD` and modeling realistic mainnet reward-issuance rates, which was not available in the indexed context — recommend a full-repo Devin session to confirm the numeric feasibility and construct a deterministic unit-test PoC analogous to `ResourceProcessorHardenTest`'s overflow tests.

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/RewardViCalService.java (L215-228)
```java
  private void accumulateWitnessVi(long cycle, byte[] address) {
    BigInteger preVi = getWitnessVi(cycle - 1, address);
    long voteCount = getWitnessVote(cycle, address);
    long reward = getReward(cycle, address);
    if (reward == 0 || voteCount == 0) { // Just forward pre vi
      if (!BigInteger.ZERO.equals(preVi)) { // Zero vi will not be record
        setWitnessVi(cycle, address, preVi);
      }
    } else { // Accumulate delta vi
      BigInteger deltaVi = BigInteger.valueOf(reward)
          .multiply(DECIMAL_OF_VI_REWARD)
          .divide(BigInteger.valueOf(voteCount));
      setWitnessVi(cycle, address, preVi.add(deltaVi));
    }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L215-228)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java (L96-108)
```java
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
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L285-291)
```java
  private long getUsage(long usage, long windowSize) {
    if (hardenCalculation()) {
      return BigInteger.valueOf(usage).multiply(BigInteger.valueOf(windowSize))
          .divide(BigInteger.valueOf(precision)).longValueExact();
    }
    return usage * windowSize / precision;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawRewardProcessor.java (L38-67)
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
```
