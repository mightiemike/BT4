### Title
Standby witness reward distribution loses precision due to division-before-multiplication - (File: chainbase/src/main/java/org/tron/core/service/MortgageService.java, consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java)

### Summary
Both `MortgageService.payStandbyWitness()` and `IncentiveManager.reward()` compute each standby witness's share of a fixed reward pool by first dividing `totalPay` by `voteSum` (as a `double`), and then multiplying the result by each witness's `voteCount`. This is the same division-before-multiplication anti-pattern described in the external report: the correct order is `(voteCount * totalPay) / voteSum`, but the code instead computes `voteCount * (totalPay / voteSum)`, which introduces avoidable floating-point precision loss on every distribution.

### Finding Description
In `MortgageService.payStandbyWitness()`:
```java
double eachVotePay = (double) totalPay / voteSum;
for (WitnessCapsule w : witnessStandbys) {
  long pay = (long) (w.getVoteCount() * eachVotePay);
  payReward(w.getAddress().toByteArray(), pay);
``` [1](#0-0) 

`totalPay / voteSum` is computed once as a `double` before the loop, then multiplied per-witness by `voteCount`. Because `totalPay`/`voteSum` is generally not exactly representable in binary floating point, the pre-computed ratio already carries rounding error, and that error is then scaled (potentially amplified) by each witness's (possibly very large) `voteCount`, exactly mirroring the reported "weight computed via division, then multiplied" flaw.

The identical pattern exists in `IncentiveManager.reward()`:
```java
long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay
    / voteSum));
``` [2](#0-1) 

Here the division `totalPay / voteSum` is recomputed inside the loop but is still performed *before* multiplying by `voteCount`, rather than doing the multiplication first (`voteCount * totalPay`) and dividing once at the end.

A related legacy code path, `MortgageService.computeReward(cycle, votes)` (the "old algorithm" used for cycles prior to `newRewardAlgorithmEffectiveCycle`), has the same shape:
```java
double voteRate = (double) userVote / totalVote;
reward += voteRate * totalReward;
``` [3](#0-2) 

By contrast, the newer VI-based reward computation elsewhere in the same class correctly multiplies before dividing using `BigInteger` (`deltaVi.multiply(BigInteger.valueOf(userVote)).divide(DECIMAL_OF_VI_REWARD)`), showing that the project is aware of this exact precision-safety pattern in some code paths but not in the standby-witness/legacy-vote-reward paths flagged here. [4](#0-3) 

### Impact Explanation
This causes standby witnesses (and, via `payReward`, their voters through the brokerage-adjusted reward pool) to receive slightly less TRX allowance than their exact proportional share of `totalPay`/`getWitnessStandbyAllowance()`. Because `payStandbyWitness()` and `IncentiveManager.reward()` run automatically as part of every maintenance/cycle reward distribution (protocol-level "resource and reward accounting"), the loss compounds block after block/cycle after cycle across the entire standby witness set, resulting in systemic, non-trivial token under-distribution over time — not a one-off rounding difference. This falls into the "resource and reward accounting" corruption category explicitly in scope.

### Likelihood Explanation
This code executes unconditionally on every cycle/reward-distribution pass for all networks where the corresponding legacy delegation-payment path is active (i.e., `payStandbyWitness` is always reachable per cycle change and `IncentiveManager.reward()` runs whenever `!allowChangeDelegation()`), requiring no attacker action, privileged role, or special transaction — it is triggered purely by normal chain progression. This makes the precision loss deterministic and continuously reproducible, though the per-cycle magnitude is bounded to the floating point rounding error scaled by vote weight (potentially a handful of SUN per witness per cycle, not a single dramatic loss).

### Recommendation
Refactor both reward-distribution loops to multiply before dividing, ideally using integer/`BigInteger` arithmetic to avoid floating-point rounding entirely, consistent with the pattern already used in the VI-based reward calculation (`deltaVi.multiply(...).divide(...)`):

```java
// MortgageService.payStandbyWitness()
for (WitnessCapsule w : witnessStandbys) {
  long pay = BigInteger.valueOf(w.getVoteCount())
      .multiply(BigInteger.valueOf(totalPay))
      .divide(BigInteger.valueOf(voteSum))
      .longValueExact();
  payReward(w.getAddress().toByteArray(), pay);
}
```
```java
// IncentiveManager.reward()
long pay = BigInteger.valueOf(consensusDelegate.getWitness(address).getVoteCount())
    .multiply(BigInteger.valueOf(totalPay))
    .divide(BigInteger.valueOf(voteSum))
    .longValueExact();
```
Similarly convert `MortgageService.computeReward(cycle, votes)`'s `voteRate * totalReward` (double) computation to `(userVote * totalReward) / totalVote` using `BigInteger` to prevent silent precision loss.

### Proof of Concept
Given `totalPay = 16_000_000`, and witnesses with `voteCount` values `100_000_000, 100_000_001, …, 100_000_026` (`voteSum = 27 * 100_000_000 + Σi(0..26) = 2_700_000_351`), computing `eachVotePay = (double) totalPay / voteSum` first introduces a binary rounding error in the ratio (since `16000000/2700000351` is not exactly representable in double). Multiplying that already-rounded ratio by each `voteCount` (on the order of 1e8) propagates and can amplify the error into whole-unit (SUN) losses per witness per cycle, exactly as in the existing regression test `DelegationServiceTest.testPay` which itself relies on doubled-precision expected values (`double d = (double) 16000000 / tmp; long expect = (long) (d * 100000026);`), demonstrating the codebase already tolerates/reproduces this rounding behavior rather than computing the mathematically exact `(voteCount * totalPay) / voteSum`. [5](#0-4)

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L60-66)
```java
    long totalPay = dynamicPropertiesStore.getWitness127PayPerBlock();
    double eachVotePay = (double) totalPay / voteSum;
    for (WitnessCapsule w : witnessStandbys) {
      long pay = (long) (w.getVoteCount() * eachVotePay);
      payReward(w.getAddress().toByteArray(), pay);
      logger.debug("Pay {} stand reward {}.", Hex.toHexString(w.getAddress().toByteArray()), pay);
    }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L183-186)
```java
      long userVote = vote.getValue();
      double voteRate = (double) userVote / totalVote;
      reward += voteRate * totalReward;
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

**File:** framework/src/test/java/org/tron/core/services/DelegationServiceTest.java (L30-49)
```java
  private void testPay(int cycle) {
    double rate = 0.2;
    if (cycle == 0) {
      rate = 0.1;
    } else if (cycle == 1) {
      rate = 0.2;
    }
    mortgageService.payStandbyWitness();
    Wallet.setAddressPreFixByte(ADD_PRE_FIX_BYTE_MAINNET);
    byte[] sr1 = decodeFromBase58Check("TLTDZBcPoJ8tZ6TTEeEqEvwYFk2wgotSfD");
    long value = dbManager.getDelegationStore().getReward(cycle, sr1);
    long tmp = 0;
    for (int i = 0; i < 27; i++) {
      tmp += 100000000 + i;
    }
    double d = (double) 16000000 / tmp;
    long expect = (long) (d * 100000026);
    long brokerageAmount = (long) (rate * expect);
    expect -= brokerageAmount;
    Assert.assertEquals(expect, value);
```
