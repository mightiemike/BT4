### Title
Per-witness truncation before summation causes systematic reward underpayment - ([File: chainbase/src/main/java/org/tron/core/service/MortgageService.java])

### Summary
`MortgageService.computeReward(long, long, AccountCapsule)` and its VM-path twin `VoteRewardUtil.computeReward` distribute a voter's accumulated reward across every witness the account has voted for, dividing each witness's contribution by `DECIMAL_OF_VI_REWARD` (`10^18`) individually and then summing the already-truncated `long` results, instead of summing the un-truncated `BigInteger` terms first and dividing once. This is the exact bug class from the referenced Sherlock report (`getCommunityVotingPower` in `Staking.sol`): dividing each of several weighted terms by a common denominator before adding them together accumulates truncation loss, versus adding first and dividing once.

### Finding Description
In `MortgageService.java`: [1](#0-0) 

and identically in the VM native-contract path: [2](#0-1) 

For each vote the account has cast, `deltaVi.multiply(BigInteger.valueOf(userVote)).divide(DECIMAL_OF_VI_REWARD).longValue()` is computed and accumulated into `reward` as a `long`. `DECIMAL_OF_VI_REWARD = 10^18` [3](#0-2) , so each per-witness division can lose up to `(10^18 - 1)` in the numerator before the final divide, i.e., up to just under 1 unit of reward is silently dropped per witness voted for. Because the division is performed once per witness *before* the terms are summed, rather than once on the combined `BigInteger` sum, the losses accumulate linearly with the number of witnesses a user has delegated votes to (up to 5 for TRON's `MAX_VOTE_NUMBER`, and this pattern repeats every reward-withdrawal cycle for every voting account on-chain).

This mirrors the audited bug precisely: `getCommunityVotingPower` in the referenced report divided three weighted terms by `PERCENT` individually before summing; here, N weighted terms (`deltaVi * userVote`) are divided by `DECIMAL_OF_VI_REWARD` individually before summing, when the mathematically correct approach is `(Σ deltaVi_i * userVote_i) / DECIMAL_OF_VI_REWARD`.

### Impact Explanation
This is a protocol-wide accounting/reward computation reachable by any unprivileged TRON account that stakes and votes for witnesses (`FreezeBalanceV2` → `VoteWitness` → `WithdrawReward`, or the TVM-native equivalents). The truncation systematically underpays voters' rewards relative to the intended pro-rata VI-based distribution, and the "lost" dust is neither credited to the voter nor explicitly reconciled elsewhere, representing an accounting divergence between the intended reward-accrual invariant (`Σ per-witness VI-share == total accrued reward share`) and the actual computed value. Because this executes on every reward withdrawal/query for every voting account across every cycle, the aggregate leakage compounds chain-wide over time, even though the per-call amount is small (bounded by `MAX_VOTE_NUMBER - 1` units of `10^-18`-scaled precision loss).

### Likelihood Explanation
High likelihood of occurrence: any account that spreads its votes across more than one witness (a common, encouraged pattern for risk diversification) will trigger this on every reward computation, with no special conditions or privileges required — it happens automatically inside `withdrawReward`/`queryReward` and the VM `VoteRewardUtil.withdrawReward` path.

### Recommendation
Restructure both `computeReward` implementations to accumulate the un-divided `BigInteger` products first, and perform the division by `DECIMAL_OF_VI_REWARD` only once after summing all witnesses' contributions, e.g.:
```java
BigInteger rewardSum = BigInteger.ZERO;
for (Pair<byte[], Long> vote : srAddresses) {
  ...
  rewardSum = rewardSum.add(deltaVi.multiply(BigInteger.valueOf(userVote)));
}
reward += rewardSum.divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
```
Apply the same fix to `VoteRewardUtil.computeReward`.

### Proof of Concept
Given `DECIMAL_OF_VI_REWARD = 10^18`:
- Witness A: `deltaVi_A * userVote_A = 10^18 - 1` → contributes `(10^18-1)/10^18 = 0` (floored), losing `10^18-1`.
- Witness B: `deltaVi_B * userVote_B = 10^18 - 1` → contributes `0`, losing another `10^18-1`.
- Current code sums these already-truncated zeros: `reward = 0`.
- Correct approach: `(10^18-1) + (10^18-1) = 2*10^18-2`, divided once by `10^18` = `1` (floored) — the voter should have received `1` unit of reward, but the current implementation pays `0`, a 100% loss in this instance.

This demonstrates that dividing per-term before summing (current code) versus summing before a single division (correct code) can produce materially different, always-lower reward payouts, exactly matching the root cause identified in the referenced `getCommunityVotingPower` report.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L20-22)
```java
  public static final long REMARK = -1L;
  public static final int DEFAULT_BROKERAGE = 20;
  public static final BigInteger DECIMAL_OF_VI_REWARD = BigInteger.valueOf(10).pow(18);
```
