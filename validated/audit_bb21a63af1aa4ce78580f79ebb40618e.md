## Title
Silent `BigInteger.longValue()` truncation in the witness-vote reward integral (Vi) accounting can corrupt reward payouts when vote count is small relative to accumulated reward - (File: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java`, `chainbase/src/main/java/org/tron/core/service/MortgageService.java`, `chainbase/src/main/java/org/tron/core/service/RewardViCalService.java`, `actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java`)

### Summary
java-tron's "new" per-witness reward accounting uses a cumulative reward-per-vote integral (`Vi`), computed the same way the disclosed `ConvexStakingWrapper.reward.integral` bug computes reward-per-token: `deltaVi = reward * 1e18 / voteCount`. When `voteCount` (the denominator, analogous to `_supply`) is small relative to the accumulated `reward` (numerator), `deltaVi` grows to values far beyond `Long.MAX_VALUE`. Because the accumulator is stored as a `BigInteger` it never throws on overflow the way Solidity's `uint128` would, but every consumer of this integral eventually converts the delta back to a `long` with `BigInteger.longValue()`, which silently discards the high-order bits instead of throwing, producing an arbitrary/wrapped reward value.

### Finding Description
The reward integral is accumulated per cycle in `DelegationStore.accumulateWitnessVi` and `RewardViCalService.accumulateWitnessVi`: [1](#0-0) 

```
BigInteger deltaVi = BigInteger.valueOf(reward)
    .multiply(DECIMAL_OF_VI_REWARD)      // * 1e18
    .divide(BigInteger.valueOf(voteCount));
setWitnessVi(cycle, address, preVi.add(deltaVi));
```

This is precisely the `reward.integral += (d_reward * 1e20) / _supply` pattern from the reported bug, with `voteCount` playing the role of `_supply` and `reward` playing the role of `d_reward`. `preVi` accumulates unboundedly across cycles (there is no reset), so even moderate rewards divided by a small `voteCount` compound over time into astronomically large `BigInteger` values.

Every place that turns this integral back into a spendable `long` reward performs the multiply/divide and then calls `.longValue()`, which per the JDK contract "if this BigInteger is too big to fit in a long, only the low-order 64 bits are returned" — i.e. a silent, un-checked truncation, functionally equivalent to the unmitigated `uint128` overflow in the original report: [2](#0-1) [3](#0-2) [4](#0-3) 

These are reached from unprivileged, anonymous paths:
- Broadcasting a `WithdrawBalanceContract` transaction → `WithdrawBalanceActuator.execute` → `MortgageService.withdrawReward` → `computeReward` (new-algorithm branch shown above), which then directly sets `accountCapsule` balance/allowance from the (possibly corrupted) `reward` value. [5](#0-4) 
- A contract call invoking the TVM vote/reward precompile → `VoteRewardUtil.withdrawReward`/`queryReward`, reachable from any smart-contract execution.

### Impact Explanation
If `deltaVi * userVote / 1e18` overflows 64 bits at withdrawal time, `.longValue()` silently wraps to a essentially arbitrary (possibly negative) `long`. Since this value feeds directly into `AccountCapsule.setAllowance`/`setBalance` (real spendable TRX), the result is an accounting/asset corruption: a voter or witness operator could receive a wildly incorrect (potentially enormous or negative) balance credit, disrupting reward distribution for that witness's voters and potentially creating/destroying TRX balance inconsistent with the issuance schedule — a direct asset/accounting integrity bug reachable from a normal broadcast transaction with no special privilege required.

### Likelihood Explanation
Triggering the overflow requires `Vi` to accumulate to a magnitude on the order of `2^63 / voteCount` over the withdrawal window. This is realistic on networks where a witness/SR can maintain a very small `voteCount` (e.g. a single self-vote) while still receiving witness rewards over a long, unwithdrawn period (`beginCycle`/`endCycle` can span very many cycles, as demonstrated by the codebase's own 73,000-cycle benchmark test `testRewardAlgorithmBenchmark`). This is most plausible on smaller/private/consortium java-tron-based chains with few competing witness candidates (where a low-vote node can still be selected as an active or standby witness), but the arithmetic weakness itself exists unconditionally in the shared codebase. Likelihood is Medium: it needs a specific but not exotic precondition (small vote count sustained over a long unclaimed reward window), similar to the Medium severity assigned to the original finding.

### Recommendation
- Replace the silent `.longValue()` conversions in `MortgageService.computeReward`, `VoteRewardUtil.computeReward`, and `RewardViCalService.getNewRewardAlgorithmReward` with `.longValueExact()` (or an explicit range check) so an out-of-range integral throws instead of silently wrapping.
- Consider bounding/normalizing `Vi` growth (e.g., cap `DECIMAL_OF_VI_REWARD` precision or reset/scale down periodically) so that legitimate low-vote witnesses cannot accumulate unbounded integrals over long cycle ranges.
- Add validation before crediting `accountCapsule` balance/allowance that the computed reward is non-negative and within a sane bound relative to total possible issuance for the cycle range.

### Proof of Concept
1. Deploy/operate a witness `W` on a java-tron network where the candidate set is small enough that `W` becomes an active or standby witness while holding a minimal `voteCount` (e.g. `1`), via a normal `VoteWitnessContract`/freeze transaction — no privileged action required.
2. Allow `W` to continue earning block/standby rewards (`MortgageService.payBlockReward` / `payStandbyWitness`) for many cycles without withdrawing, so `DelegationStore.accumulateWitnessVi` keeps adding `deltaVi = reward * 1e18 / 1` to `preVi` cycle after cycle (mirroring the existing `testRewardAlgorithmBenchmark` 73,000-cycle scenario in `framework/src/test/java/org/tron/common/runtime/vm/VoteTest.java`).
3. Once `endVi - beginVi` multiplied by `userVote` and divided by `1e18` exceeds `Long.MAX_VALUE`, broadcast a `WithdrawBalanceContract` (or trigger the vote-reward TVM path). `MortgageService.computeReward`'s `.longValue()` call silently truncates the oversized `BigInteger`, and `WithdrawBalanceActuator` credits the corrupted value to the account's real TRX balance.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L133-145)
```java
  public void accumulateWitnessVi(long cycle, byte[] address, long voteCount) {
    BigInteger preVi = getWitnessVi(cycle - 1, address);
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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L215-227)
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
```

**File:** chainbase/src/main/java/org/tron/core/service/RewardViCalService.java (L156-168)
```java
    if (beginCycle < endCycle) {
      for (Pair<byte[], Long> vote : votes) {
        byte[] srAddress = vote.getKey();
        BigInteger beginVi = getWitnessVi(beginCycle - 1, srAddress);
        BigInteger endVi = getWitnessVi(endCycle - 1, srAddress);
        BigInteger deltaVi = endVi.subtract(beginVi);
        if (deltaVi.signum() <= 0) {
          continue;
        }
        long userVote = vote.getValue();
        reward += deltaVi.multiply(BigInteger.valueOf(userVote))
            .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L54-68)
```java
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
```
