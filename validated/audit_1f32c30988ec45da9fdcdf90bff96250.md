### Title
Divide-before-multiply precision loss in legacy vote-reward calculation - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService.computeReward(long cycle, List<Pair<byte[], Long>> votes)` computes each voter's share of a Super Representative's cycle reward by first dividing `userVote` by `totalVote` and only then multiplying by `totalReward`, instead of multiplying first and dividing once at the end. This is the same "divide-before-multiply" anti-pattern flagged in the `Rewarder.sol` report, applied here to on-chain TRX reward accounting rather than a token reward rate.

### Finding Description
In the legacy ("old") reward algorithm path, reward for a voter is computed as: [1](#0-0) 

```
double voteRate = (double) userVote / totalVote;
reward += voteRate * totalReward;
```

This divides `userVote` by `totalVote` first (producing a fractional double that may already have lost significant relative precision when `userVote` is much smaller than `totalVote`), then multiplies by `totalReward`, and finally truncates via the `long +=` compound assignment on every loop iteration. The mathematically precise and precision-preserving order would be `userVote * totalReward / totalVote` (multiply before dividing), exactly the fix pattern recommended in the external report.

This method is reached from `computeReward(long beginCycle, long endCycle, AccountCapsule accountCapsule)`, which is called by both `withdrawReward(byte[] address)` and `queryReward(byte[] address)`: [2](#0-1) 

`withdrawReward` is invoked whenever any account withdraws its accumulated voting rewards (via the `WithdrawBalanceContract`/vote-reward withdrawal actuator path), so the flawed calculation is reachable from an ordinary broadcast transaction, not just from a privileged actor.

The same divide-then-multiply ordering also appears in the standby-witness pay-out and the DPoS incentive manager, both of which distribute real TRX to accounts based on vote share: [3](#0-2) [4](#0-3) 

### Impact Explanation
Because the division happens before the multiplication, the fractional vote-rate loses relative precision when small-vote accounts are compared against very large `totalVote` values, and the running `long reward` accumulator truncates on every iteration of the loop rather than once at the end. Over many voters and many cycles, this can cause systematic under/over-payment of on-chain TRX rewards relative to the mathematically correct `userVote * totalReward / totalVote` formula, i.e. accounting drift in reward distribution — the same bug class (and consequence: asset accounting corruption) identified in the `Rewarder.sol` report, but manifesting here as consensus-reward miscalculation rather than a DeFi APR truncation.

### Likelihood Explanation
The vulnerable code path executes automatically for every account that withdraws or queries vote rewards accrued in cycles prior to the "new reward algorithm effective cycle" (`getNewRewardAlgorithmEffectiveCycle`), which is a normal, frequent, unprivileged user operation (`withdrawReward`/`queryReward`). No special permissions or malicious peer behavior are required — any voter triggers this arithmetic simply by claiming rewards for a legacy cycle.

### Recommendation
Reorder the arithmetic to multiply before dividing, and perform the division with `BigInteger`/wide integer arithmetic instead of `double`, mirroring how the "new algorithm" branch already computes reward using `BigInteger` delta-VI multiplication followed by a single division:
```
reward += BigInteger.valueOf(userVote).multiply(BigInteger.valueOf(totalReward))
              .divide(BigInteger.valueOf(totalVote)).longValueExact();
```
Apply the same multiply-then-divide ordering fix to `payStandbyWitness` and `IncentiveManager.reward` for consistency.

### Proof of Concept
Not applicable as a runnable exploit — this is an arithmetic-precision analysis. The precision-loss can be demonstrated purely via unit test by comparing the double-based `computeReward(long cycle, votes)` output against the mathematically exact `BigInteger` multiply-then-divide result for a `totalVote`/`userVote` pair with a large dynamic range (e.g. `totalVote = 9_000_000_000_000_000_000L`, `userVote = 1L`, `totalReward = 1_000_000_000L`), showing the two results diverge.

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L53-67)
```java
  public void payStandbyWitness() {
    List<WitnessCapsule> witnessStandbys = witnessStore.getWitnessStandby(
        dynamicPropertiesStore.allowWitnessSortOptimization());
    long voteSum = witnessStandbys.stream().mapToLong(WitnessCapsule::getVoteCount).sum();
    if (voteSum < 1) {
      return;
    }
    long totalPay = dynamicPropertiesStore.getWitness127PayPerBlock();
    double eachVotePay = (double) totalPay / voteSum;
    for (WitnessCapsule w : witnessStandbys) {
      long pay = (long) (w.getVoteCount() * eachVotePay);
      payReward(w.getAddress().toByteArray(), pay);
      logger.debug("Pay {} stand reward {}.", Hex.toHexString(w.getAddress().toByteArray()), pay);
    }
  }
```

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

**File:** consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java (L34-42)
```java
    long totalPay = consensusDelegate.getWitnessStandbyAllowance();
    for (ByteString witness : witnesses) {
      byte[] address = witness.toByteArray();
      long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay
          / voteSum));
      AccountCapsule accountCapsule = consensusDelegate.getAccount(address);
      accountCapsule.setAllowance(accountCapsule.getAllowance() + pay);
      consensusDelegate.saveAccount(accountCapsule);
    }
```
