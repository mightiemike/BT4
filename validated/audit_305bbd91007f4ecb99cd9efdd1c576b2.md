### Title
Reward-per-vote ratio manipulation causes silent `BigInteger.longValue()` truncation in witness voting rewards - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
java-tron's witness voting reward system uses a virtual-rewards-per-share accumulator (`Vi`, the "reward-per-vote" index) that is structurally identical to the SALTY `StakingRewards` pattern flagged in the external report: rewards accrue proportionally to a share (vote) denominator, and the accumulated ratio is later multiplied by a user's share and truncated into a fixed-width integer without an overflow check. Just as SALTY's first depositor could inflate `totalRewards/existingTotalShares` and overflow the `uint128` cast, a witness with an abnormally small `voteCount` can cause the `Vi` ratio to grow extremely large, and the subsequent `BigInteger -> long` truncation via `.longValue()` silently corrupts reward payouts instead of reverting.

### Finding Description
The core reward accumulation is: [1](#0-0) 

Here `deltaVi = reward * DECIMAL_OF_VI_REWARD / voteCount` (with `DECIMAL_OF_VI_REWARD = 10^18`), and this is monotonically accumulated into `Vi[cycle]` every maintenance cycle for every witness. This is exactly analogous to SALTY's `virtualRewardsToAdd = totalRewards * increaseShareAmount / existingTotalShares` — a rewards-per-share ratio driven by a denominator (`voteCount`/`shares`) that an unprivileged user can push to a very small value.

The ratio is later consumed by multiplying it back by a (potentially much larger) user vote amount and truncating to a `long` with no overflow protection: [2](#0-1) 

The identical unguarded pattern (`deltaVi.multiply(BigInteger.valueOf(userVote)).divide(DECIMAL_OF_VI_REWARD).longValue()`) is duplicated in: [3](#0-2) [4](#0-3) 

`BigInteger.longValue()` does not throw on overflow — it silently returns the low 64 bits of the value in two's-complement form (per the Java spec). Because `voteCount` can legitimately be as small as `1` (the minimum permitted vote, gated only by `voteCount > 0` in `VoteWitnessActuator`/`VoteWitnessProcessor`): [5](#0-4) 

a witness that accrues block/transaction-fee rewards while having a very small total vote denominator produces an extremely inflated `deltaVi`. Any later large voter's reward is then computed as `deltaVi * userVote / 10^18`, which for a sufficiently large `deltaVi`/`userVote` combination exceeds `Long.MAX_VALUE` (~9.2×10^18) and wraps/truncates silently rather than reverting, corrupting the `reward` value added into `long reward` accumulators in `MortgageService.computeReward`, `VoteRewardUtil.computeReward`, and `RewardViCalService.getNewRewardAlgorithmReward`.

### Impact Explanation
This breaks reward accounting integrity in the same way as the SALTY finding: a manipulable ratio combined with an unguarded fixed-width truncation. Depending on the specific bit pattern produced by the truncation, an affected voter's computed reward can become arbitrarily wrong — including negative (reducing `allowance` unexpectedly on the next add, since `reward` feeds into `adjustAllowance`) or an unrelated large/small value — silently diverging from the intended proportional reward. This is an invalid-state/accounting-divergence impact directly analogous to the "loss of matured yield" impact confirmed as High severity in the original report, since it corrupts on-chain witness/voter reward balances without reverting.

### Likelihood Explanation
Voting is fully permissionless and unprivileged (any account can vote with as little as the minimum vote unit), and `voteCount` for an elected witness is not bounded from below by protocol rule — it only needs to be large enough to be within the active/standby witness set relative to other candidates, which is realistic in scenarios with limited voter participation (private/consortium chains built on this codebase, early bootstrap periods, or witnesses that temporarily have very few votes). No additional privilege or trusted role is required to trigger the ratio distortion; a second, larger, independent voter later withdrawing/computing rewards is sufficient to hit the truncation path.

### Recommendation
Replace the unchecked `BigInteger.longValue()` truncation in `MortgageService.computeReward`, `VoteRewardUtil.computeReward`, and `RewardViCalService.getNewRewardAlgorithmReward` with an overflow-checked conversion (e.g., `longValueExact()` or an explicit bounds check that fails safely/caps the value), and consider bounding the minimum effective `voteCount` used as the `Vi` denominator (e.g., enforce a floor) to prevent the ratio from being driven to extreme values by low-vote-count witnesses.

### Proof of Concept
1. Witness `W` becomes active with `voteCount = 1` (minimum allowed by `VoteWitnessProcessor`/`VoteWitnessActuator`, permissionless).
2. `W` earns block rewards over several cycles; `DelegationStore.accumulateWitnessVi` computes `deltaVi = reward * 10^18 / 1`, driving `Vi[W]` to a very large `BigInteger` value.
3. A second voter later casts a large vote (e.g., near the account's `TronPower`) for `W`.
4. When that voter's reward is computed via `MortgageService.computeReward` (or the TVM/`RewardViCalService` equivalents), `deltaVi.multiply(BigInteger.valueOf(userVote)).divide(DECIMAL_OF_VI_REWARD)` exceeds `Long.MAX_VALUE`, and `.longValue()` silently truncates, yielding a corrupted `reward` value (potentially negative or otherwise incorrect) instead of throwing, which is then added into the voter's on-chain `allowance` via `adjustAllowance`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L133-146)
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

**File:** chainbase/src/main/java/org/tron/core/service/RewardViCalService.java (L155-171)
```java
    long reward = 0;
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
    }
    return reward;

```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L75-86)
```java
        long voteCount = vote.getVoteCount();
        if (voteCount < 0) {
          throw new ContractExeException("Vote count must not be less than 0");
        } else if (voteCount == 0) {
          iterator.remove();
        } else {
          sum = LongMath.checkedAdd(sum, voteCount);
          // merge vote for same witness
          voteMap.put(vote.getVoteAddress(),
              LongMath.checkedAdd(voteMap.getOrDefault(vote.getVoteAddress(), 0L), voteCount));
        }
      }
```
