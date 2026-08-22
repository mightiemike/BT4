## Title
Unnecessary precision loss (div-before-mul) in legacy witness/voter reward computation - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`, `consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java`)

### Summary
Both `MortgageService.computeReward(long cycle, List<Pair<byte[], Long>> votes)` (legacy pre-new-algorithm reward path) and `IncentiveManager.reward(List<ByteString> witnesses)` (standby witness pay) compute a proportional share by dividing first and multiplying second, using `double` arithmetic, exactly matching the reported bug class ("div before mul" causing avoidable precision loss).

### Finding Description
In `MortgageService.java`:
```java
double voteRate = (double) userVote / totalVote;
reward += voteRate * totalReward;
``` [1](#0-0) 

and in `IncentiveManager.java`:
```java
long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay
    / voteSum));
``` [2](#0-1) 

Both compute `userValue / total` as an intermediate ratio (a division) before multiplying by the reward pool amount, instead of multiplying first (`userValue * total_reward`) and dividing once at the end. This is the identical anti-pattern flagged in the referenced report: dividing before multiplying accumulates avoidable rounding/precision error. Notably, other parts of the same codebase (`ResourceProcessor.calculateGlobalLimitV1/V2`, `VoteRewardUtil.computeReward`, `RewardViCalService`, `MortgageService.computeReward(long,long,AccountCapsule)` new algorithm) were hardened to use `BigInteger` multiply-then-divide precisely to avoid this class of bug, but the legacy `voteRate`/`IncentiveManager.reward` paths were not similarly hardened. [3](#0-2) 

### Impact Explanation
This affects TRX reward accounting distributed to voters/standby witnesses — an accounting-correctness issue causing users to receive systematically truncated/incorrect rewards versus the mathematically precise mul-then-div formula. It is a real "unnecessary precision loss" defect in reward/resource accounting, matching the report's bug class, though the magnitude is small per computation.

### Likelihood Explanation
`MortgageService.computeReward(cycle, votes)` only executes for cycles prior to `getNewRewardAlgorithmEffectiveCycle()`, i.e., only for historical reward periods already finalized on-chain before the network's algorithm switch; `IncentiveManager.reward` runs automatically each maintenance cycle for the top standby witnesses and is not attacker-triggerable — both paths are deterministic and execute identically on all full nodes (no consensus divergence), so exploitability is low and impact is limited to a minor precision loss already baked into historical/ongoing reward distribution rather than an actively exploitable vulnerability.

### Recommendation
Change `voteRate * totalReward` and the `IncentiveManager` pay formula to multiply first then divide once (ideally via `BigInteger`/`Math.multiplyHigh` style patterns already used elsewhere in the codebase, e.g. `deltaVi.multiply(BigInteger.valueOf(userVote)).divide(...)`), consistent with the hardened `ResourceProcessor`/`VoteRewardUtil` implementations.

### Proof of Concept
For `MortgageService.computeReward`: with `userVote = 1`, `totalVote = 3`, `totalReward = 10`, `voteRate = 1.0/3.0 = 0.333...`, `reward += 0.333... * 10 = 3.333...` truncated to `3`. Computing `userVote * totalReward / totalVote = 10 / 3 = 3` (integer) yields the same in this trivial case, but for larger reward pools and vote counts the two orderings diverge due to double rounding at the division step, systematically under/over-crediting accounts across many cycles.

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L183-185)
```java
      long userVote = vote.getValue();
      double voteRate = (double) userVote / totalVote;
      reward += voteRate * totalReward;
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java (L37-38)
```java
      long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay
          / voteSum));
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L350-357)
```java
  protected long calculateGlobalLimitV1(long frozeBalance,
      long totalLimit, long totalWeight) {
    long weight = frozeBalance / TRX_PRECISION;
    return BigInteger.valueOf(weight)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(totalWeight))
        .longValueExact();
  }
```
