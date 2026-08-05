### Title
Standby witness reward truncation permanently leaks TRX issuance every block - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService.payStandbyWitness()` distributes a fixed per-block TRX allowance (`getWitness127PayPerBlock()`) among up to 127 standby witnesses proportional to their vote count. The per-witness share is computed using floating point and then truncated to a `long`, so the sum of amounts actually credited is strictly ≤ the intended `totalPay`. Unlike a leftover balance that simply sits in a recoverable account, the truncated remainder here is never minted, tracked, or carried forward — it disappears entirely, every single block.

### Finding Description
`payStandbyWitness()` computes: [1](#0-0) 

```
long totalPay = dynamicPropertiesStore.getWitness127PayPerBlock();
double eachVotePay = (double) totalPay / voteSum;
for (WitnessCapsule w : witnessStandbys) {
  long pay = (long) (w.getVoteCount() * eachVotePay);
  payReward(w.getAddress().toByteArray(), pay);
}
```

For each of the (up to `WITNESS_STANDBY_LENGTH`) standby witnesses, `pay` is floor-truncated from a `double` computation. Summing `pay` over all witnesses yields a value strictly less than `totalPay` whenever `totalPay` doesn't divide evenly across weighted vote shares — which is virtually always the case given `voteSum` is an arbitrary large integer. This function is invoked on every block via `Manager.payReward()`: [2](#0-1) 

The legacy code path (`allowChangeDelegation()` disabled) has the same defect in `IncentiveManager.reward()`, run once per maintenance cycle for the same set of standby witnesses: [3](#0-2) 

This is the direct structural analog of the Lido `NodeOperatorRegistry.distributeRewards()` bug: a fixed reward pool is divided among many recipients proportional to a weight (votes vs. active validators), using per-recipient integer/float truncation, with no mechanism to retain, redistribute, or account for the truncated remainder. The Lido version at least leaves the residual in the `NodeOperatorRegistry`'s recoverable stETH balance; the java-tron version is strictly worse — the untransferred `totalPay - Σpay` is not moved anywhere (it is never subtracted from a tracked pool nor credited to any account), so it is simply never issued, forever.

### Impact Explanation
This causes a persistent, silent divergence between the protocol's intended per-block issuance to standby witnesses (`getWitness127PayPerBlock()`) and what actually gets credited via `adjustAllowance`/`delegationStore.addReward`. Because this runs every block (~every 3 seconds) rather than daily as in the Lido case, and applies across up to 127 recipients each time, the aggregate leaked value compounds continuously over the chain's lifetime. This is an accounting/incentive-accuracy defect affecting unprivileged standby witnesses (ranked outside the top 27 active witnesses) who are systematically underpaid relative to the documented reward formula, with no recovery path for the shortfall.

### Likelihood Explanation
This triggers deterministically on essentially every block where standby witnesses have received votes (`voteSum >= 1`), which is the normal steady-state of the network. No attacker action is required — it is a systematic protocol-level truncation bug, guaranteed to occur under normal, expected conditions.

### Recommendation
Track the truncation remainder explicitly rather than discarding it: either (a) carry the undistributed remainder forward into the next block's `totalPay` for standby rewards, (b) compute a single integer "pay-per-vote" (analogous to the Lido fix's recommended `_totalReward.div(effectiveStakeTotal)` before multiplying by each witness's vote count so each witness suffers the same bounded truncation and the aggregate divergence is minimized/known, or (c) credit any leftover (`totalPay - Σpay`) to a treasury/burn-tracked account so the shortfall is at least accounted for rather than silently vanishing.

### Proof of Concept
Given `totalPay = 16_000_000` (sun) and `voteSum` such that `eachVotePay` is a non-terminating fraction (e.g., `voteSum = 3`), for any witness with `voteCount` not a multiple of `voteSum`, `(long)(voteCount * eachVotePay)` truncates a fractional sun. With 127 standby witnesses each losing up to just under 1 sun per block, and blocks produced roughly every 3 seconds, the aggregate unminted TRX accumulates without bound and without any corresponding decrement elsewhere in the system (compare to `payTransactionFeeReward`'s pool at `Manager.java:1956-1964`, which does properly decrement `TransactionFeePool` by the exact amount paid out).

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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1950-1953)
```java
    if (getDynamicPropertiesStore().allowChangeDelegation()) {
      mortgageService.payBlockReward(witnessCapsule.getAddress().toByteArray(),
          getDynamicPropertiesStore().getWitnessPayPerBlock());
      mortgageService.payStandbyWitness();
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
