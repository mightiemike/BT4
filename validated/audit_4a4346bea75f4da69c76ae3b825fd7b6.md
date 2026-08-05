### Title
Legacy vote-reward computation truncates per-cycle rewards to zero for low-stake voters via implicit narrowing cast - ([File: chainbase/src/main/java/org/tron/core/service/MortgageService.java])

### Summary
`MortgageService.computeReward(long cycle, List<Pair<byte[], Long>> votes)` computes a voter's share of a Super Representative's total cycle reward using floating point math and then accumulates it into a `long` accumulator via a compound `+=` operator, which performs an implicit narrowing (truncating) cast on every loop iteration. This is the same root-cause pattern as the Derby `storePriceAndRewards()` bug: a per-period reward value that is mathematically positive but smaller than 1 unit is silently rounded down to `0`, so a low-stake voter can receive exactly zero reward for a given SR/cycle pair even though they are entitled to a fractional-but-real amount.

### Finding Description
`computeReward(long cycle, List<Pair<byte[], Long>> votes)` is the "old algorithm" reward calculation, invoked from `computeReward(beginCycle, endCycle, accountCapsule)` whenever any part of the requested cycle range predates `dynamicPropertiesStore.getNewRewardAlgorithmEffectiveCycle()`: [1](#0-0) 

For every vote, it computes `voteRate = (double) userVote / totalVote` and then does `reward += voteRate * totalReward;` where `reward` is declared as `long`. Because `reward` is a `long`, the compound assignment operator converts the right-hand `double` expression back to `long` on **every single iteration**, truncating any fractional value below `1.0`. If a voter's proportional share of a witness's per-cycle reward (`voteRate * totalReward`) is less than 1 (e.g. a small holder voting for a witness whose per-cycle reward pool is modest relative to total votes), that iteration contributes exactly `0`, permanently losing that voter's entitled fractional reward for that cycle/witness pair — mirroring the Derby report's `nominator/denominator == 0` truncation when the numerator (proportional share) is small relative to the denominator scale.

This is invoked from both `withdrawReward()` and `queryReward()`: [2](#0-1) 

Notably, java-tron's newer VI-based reward algorithm (used after the effective cycle) already avoids this exact class of bug by scaling the numerator with `DECIMAL_OF_VI_REWARD` before dividing: [3](#0-2) 

This is precisely the type of fix (scale-then-divide) recommended in the external report for `Vault.storePriceAndRewards()`. The legacy double-based path, however, was never hardened this way and remains reachable for any account whose reward-withdrawal cursor (`beginCycle`) predates the algorithm switch.

### Impact Explanation
Any voter whose per-witness per-cycle proportional reward share is below 1 TRX-unit (SUN) has that cycle's reward for that witness silently zeroed instead of accumulated fractionally. Across many cycles and many small-stake voters, this results in systematic, permanent underpayment of staking rewards — an accounting/state-correctness defect affecting unprivileged users' legitimate reward entitlement, not merely a rounding artifact of negligible size when compounded across long vote/withdraw histories.

### Likelihood Explanation
This path is deterministically triggered whenever `withdrawReward()` or `queryReward()` is called for an account whose un-withdrawn reward range starts before `newRewardAlgorithmEffectiveCycle`, which is common for any account that voted and did not withdraw prior to the algorithm switch (a normal historical usage pattern rather than an attacker-crafted scenario), making this consistently reachable via ordinary, unprivileged wallet operations (`withdrawReward`/`GetRewardApi`).

### Recommendation
For the legacy path, avoid the implicit truncating cast on every loop iteration by accumulating a scaled/fixed-point or `BigDecimal`/`BigInteger` intermediate value and only truncating once at the end (e.g., accumulate `userVote * totalReward` in `BigInteger`, divide by `totalVote` once per witness and sum, or accumulate in `double`/scaled long and cast to `long` only after the full loop). Because this legacy algorithm underpins historical consensus-relevant reward state, any change must be carefully evaluated for consensus compatibility (e.g., gated by a new hard-fork flag) rather than silently altered.

### Proof of Concept
Given `totalVote = 1_000_000_000`, `userVote = 1`, `totalReward = 999_999_999` (SUN) for a given witness/cycle:
- `voteRate = 1.0 / 1_000_000_000 = 1e-9`
- `voteRate * totalReward ≈ 0.999999999`
- `reward += 0.999999999` truncates to `reward += 0`

This repeats identically for every cycle the voter remains delegated to that witness before the algorithm switch, so the voter accrues `0` reward from that witness across the entire legacy window despite having a real, continuously-growing entitlement. [4](#0-3)

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L199-214)
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
