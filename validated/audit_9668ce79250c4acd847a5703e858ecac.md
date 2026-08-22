### Title
Standby-witness reward distribution rounds rewards to zero via double division-before-multiplication - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService.payStandbyWitness()` computes each witness's share of the standby-witness reward pool by first dividing `totalPay` by `voteSum` (producing a fractional `double`), then multiplying that quotient by each witness's `voteCount`. Just like the Ajna `RewardsManager._calculateNewRewards` bug (division performed before the final multiplication), this ordering causes unnecessary precision loss and, for witnesses whose vote share is small relative to the pool, produces a per-block reward of exactly zero even though a nonzero reward should mathematically be owed.

### Finding Description
`payStandbyWitness()` is: [1](#0-0) 

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
  }
}
```

The correct order (matching the fix pattern recommended in the referenced Ajna report) is to multiply `totalPay * w.getVoteCount()` first and divide by `voteSum` last, ideally in integer/`BigInteger` arithmetic to avoid `double` rounding entirely. Instead, `eachVotePay = totalPay / voteSum` is computed as an early, independent division, discarding fractional precision before it is ever multiplied by each witness's vote count. This is the same root-cause pattern flagged in the external report: "division before multiplication" precision loss.

The identical anti-pattern also exists in the legacy DPoS incentive path used when the new delegation mechanism is disabled: [2](#0-1) 

```java
long totalPay = consensusDelegate.getWitnessStandbyAllowance();
for (ByteString witness : witnesses) {
  byte[] address = witness.toByteArray();
  long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay
      / voteSum));
  ...
}
```

Here the division `totalPay / voteSum` is computed first (as a double), and only then multiplied by `voteCount`. Both call sites are invoked automatically every block, from `Manager.payReward(BlockCapsule)`: [3](#0-2) 

```java
if (getDynamicPropertiesStore().allowChangeDelegation()) {
  mortgageService.payBlockReward(witnessCapsule.getAddress().toByteArray(),
      getDynamicPropertiesStore().getWitnessPayPerBlock());
  mortgageService.payStandbyWitness();
```

which runs on every produced block as part of normal, unprivileged consensus/protocol processing — not requiring any privileged actor.

### Impact Explanation
Because `eachVotePay` is a per-block quotient (`totalPay / voteSum`) computed independently of any specific witness's vote weight, witnesses whose `voteCount` is small relative to the aggregate `voteSum` of up to 127 standby witnesses can receive `pay == 0` on any given block, even though the mathematically correct share (`totalPay * voteCount / voteSum` computed with multiplication-first, division-last) would be nonzero. Since this computation happens on every single block, over time this results in systematic loss of rightful reward allocation for the lower-voted subset of standby witnesses — an accounting/economic correctness defect in the protocol's block reward distribution, consistent in class with the Ajna finding (rewards silently lost to zero due to premature division).

### Likelihood Explanation
This code executes unconditionally on every block as part of the standard reward payment flow (`payReward(BlockCapsule)` → `payStandbyWitness()`), requiring no attacker action, special permissions, or malicious input — it is a deterministic consequence of the vote distribution among the standby witness set, which is a normal, expected on-chain condition (small-vote witnesses are a common and intended scenario in DPoS).

### Recommendation
Reorder the arithmetic so multiplication happens before division, and avoid `double` for reward-critical calculations by using integer/`BigInteger` math with a single truncation at the end, mirroring the hardened pattern already used elsewhere in the codebase (e.g. `ResourceProcessor.calculateGlobalLimitV2`): [4](#0-3) 

Specifically, change `payStandbyWitness()` to compute `pay = BigInteger.valueOf(totalPay).multiply(BigInteger.valueOf(w.getVoteCount())).divide(BigInteger.valueOf(voteSum)).longValueExact()` (multiplication first, division last), and apply the analogous fix to `IncentiveManager.reward()`.

### Proof of Concept
1. Configure a standby-witness set where `voteSum` is large (e.g., sum of 127 witnesses' votes ≈ 10^9) and `totalPay` (from `getWitness127PayPerBlock()`) is a small integer value.
2. For a witness `w` with a comparatively small `voteCount` (e.g., such that `w.getVoteCount() * totalPay < voteSum`), `eachVotePay = (double) totalPay / voteSum` yields a very small double, and `(long) (w.getVoteCount() * eachVotePay)` truncates to `0`.
3. Compare against the multiplication-first calculation `BigInteger.valueOf(totalPay).multiply(BigInteger.valueOf(w.getVoteCount())).divide(BigInteger.valueOf(voteSum))`, which for suitable inputs is nonzero, demonstrating the reward loss caused by the division-before-multiplication ordering.

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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1950-1953)
```java
    if (getDynamicPropertiesStore().allowChangeDelegation()) {
      mortgageService.payBlockReward(witnessCapsule.getAddress().toByteArray(),
          getDynamicPropertiesStore().getWitnessPayPerBlock());
      mortgageService.payStandbyWitness();
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L371-378)
```java
  protected long calculateGlobalLimitV2(long frozeBalance,
      long totalLimit, long totalWeight) {
    return BigInteger.valueOf(frozeBalance)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(TRX_PRECISION)
            .multiply(BigInteger.valueOf(totalWeight)))
        .longValueExact();
  }
```
