### Title
Precision loss in witness reward/brokerage split silently discards TRX every block - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService.payReward()` and `MortgageService.payStandbyWitness()` compute reward/brokerage splits using `double` arithmetic and then truncate the result to a `long` via a `(long)` cast. The fractional remainder that is truncated away is never added back to either side of the split (witness reward vs. brokerage, or per-witness standby pay vs. total pool), so it is permanently discarded instead of being tracked as a residual, exactly matching the reported bug class: "precision loss in reward calculation leads to incorrect accounting for residual amounts."

### Finding Description
In `payReward()`:
```
double brokerageRate = (double) brokerage / 100;
long brokerageAmount = (long) (brokerageRate * value);
value -= brokerageAmount;
delegationStore.addReward(cycle, witnessAddress, value);
adjustAllowance(witnessAddress, brokerageAmount);
``` [1](#0-0) 
`brokerageRate * value` is computed as a floating point number and then floored by the `(long)` cast. Unlike the MultiRewards.sol fix pattern (storing `reward % rewardsDuration` as `rewardResidual` for future use), no fractional/truncated amount here is ever captured or reintroduced into `value` (delegated reward pool) or `brokerageAmount` (witness self-pay). It is simply lost from total token accounting for that reward payment.

The same pattern recurs in `payStandbyWitness()`:
```
double eachVotePay = (double) totalPay / voteSum;
for (WitnessCapsule w : witnessStandbys) {
  long pay = (long) (w.getVoteCount() * eachVotePay);
  payReward(w.getAddress().toByteArray(), pay);
``` [2](#0-1) 
Here, the sum of all per-witness `pay` values (each independently floored) will generally be strictly less than `totalPay`, and the shortfall is never redistributed or tracked — it vanishes from the reward pool.

Both `payReward()` (via `payBlockReward()` and `payTransactionFeeReward()`) and `payStandbyWitness()` are invoked unconditionally from `Manager.payReward(BlockCapsule)`, which executes on every single block during normal block processing/consensus finalization:
```
mortgageService.payBlockReward(witnessCapsule.getAddress().toByteArray(),
    getDynamicPropertiesStore().getWitnessPayPerBlock());
mortgageService.payStandbyWitness();
...
mortgageService.payTransactionFeeReward(witnessCapsule.getAddress().toByteArray(),
    transactionFeeReward);
``` [3](#0-2) 
This is not a privileged-actor path — it is triggered automatically as part of normal blockchain operation (every produced block), so the loss accumulates continuously and indefinitely across the entire network's lifetime.

### Impact Explanation
Low per-invocation: the amount lost per block is at most a few units of TRX (sun) from truncation of `brokerageRate * value` and, separately, the sum of per-witness rounding losses in `payStandbyWitness()`, which is bounded by roughly `voteSum - 1` sun per call in the worst case, but typically small.

### Likelihood Explanation
High: `payReward()` executes on every block (for block reward and, when enabled, transaction fee reward), and `payStandbyWitness()` executes on every block as well, so the irrecoverable loss accumulates continuously with no way to recover it — mirroring the "high likelihood" characterization in the original report, since it is invoked on essentially every block indefinitely.

### Recommendation
Track the truncated fractional remainder from `brokerageRate * value` (and from the per-witness division in `payStandbyWitness()`) as a residual to be carried into subsequent reward calculations, analogous to the `rewardResidual` fix pattern, instead of relying on `(long)` truncation of a `double` product/quotient that silently discards value. Consider computing the split using integer arithmetic (e.g., `value * brokerage / 100`) and explicitly accumulating the remainder for future distribution.

### Proof of Concept
Not applicable in the strict sense of an exploit script; the loss is deterministic arithmetic truncation reachable on every normal block:
1. On block N, `payReward()` is called with some `value` and non-zero `brokerage` such that `brokerageRate * value` has a non-integer result (e.g., `brokerage = 33`, `value = 100`): `brokerageRate = 0.33`, `brokerageAmount = (long)(33.0) = 33` exactly here, but for `value = 10`, `brokerageRate*value = 3.3 -> 3`, discarding `0.3` sun-equivalent fraction of TRX permanently.
2. Similarly in `payStandbyWitness()`, with `totalPay = 100`, `voteSum = 3` witnesses each with `voteCount = 1`: `eachVotePay = 33.333...`; each witness gets `pay = 33`, total distributed `= 99`, `1` sun is unaccounted for and lost every time this runs.
3. Because both methods run on every block indefinitely, this dust loss accumulates over the life of the chain with no mechanism to recover or reallocate it. [4](#0-3)

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L53-87)
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

  public void payBlockReward(byte[] witnessAddress, long value) {
    logger.debug("Pay {} block reward {}.", Hex.toHexString(witnessAddress), value);
    payReward(witnessAddress, value);
  }

  public void payTransactionFeeReward(byte[] witnessAddress, long value) {
    logger.debug("Pay {} transaction fee reward {}.", Hex.toHexString(witnessAddress), value);
    payReward(witnessAddress, value);
  }

  private void payReward(byte[] witnessAddress, long value) {
    long cycle = dynamicPropertiesStore.getCurrentCycleNumber();
    int brokerage = delegationStore.getBrokerage(cycle, witnessAddress);
    double brokerageRate = (double) brokerage / 100;
    long brokerageAmount = (long) (brokerageRate * value);
    value -= brokerageAmount;
    delegationStore.addReward(cycle, witnessAddress, value);
    adjustAllowance(witnessAddress, brokerageAmount);
  }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1950-1965)
```java
    if (getDynamicPropertiesStore().allowChangeDelegation()) {
      mortgageService.payBlockReward(witnessCapsule.getAddress().toByteArray(),
          getDynamicPropertiesStore().getWitnessPayPerBlock());
      mortgageService.payStandbyWitness();

      if (chainBaseManager.getDynamicPropertiesStore().supportTransactionFeePool()) {
        long transactionFeeReward = floorDiv(
            chainBaseManager.getDynamicPropertiesStore().getTransactionFeePool(),
                Constant.TRANSACTION_FEE_POOL_PERIOD,
            chainBaseManager.getDynamicPropertiesStore().disableJavaLangMath());
        mortgageService.payTransactionFeeReward(witnessCapsule.getAddress().toByteArray(),
            transactionFeeReward);
        chainBaseManager.getDynamicPropertiesStore().saveTransactionFeePool(
            chainBaseManager.getDynamicPropertiesStore().getTransactionFeePool()
                - transactionFeeReward);
      }
```
