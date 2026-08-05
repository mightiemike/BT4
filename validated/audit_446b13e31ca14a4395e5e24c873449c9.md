### Title
Truncated Standby Witness Reward Rate Causes Permanent Loss of Unpaid TRX Reward Dust - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService.payStandbyWitness()` computes a per-vote reward rate by dividing a fixed reward budget by the total standby vote count, then truncates each witness's payout to a `long`. Every truncation discards a fractional remainder that is never paid to anyone and never carried forward, mirroring the `_upsertIncentive` integer-truncation bug in the referenced report.

### Finding Description
`payStandbyWitness()` derives a floating-point rate from a fixed budget divided by total votes, and each witness's share is then truncated to a `long`: [1](#0-0) 

Specifically:
```
long totalPay = dynamicPropertiesStore.getWitness127PayPerBlock();
double eachVotePay = (double) totalPay / voteSum;
for (WitnessCapsule w : witnessStandbys) {
  long pay = (long) (w.getVoteCount() * eachVotePay);
  payReward(w.getAddress().toByteArray(), pay);
}
```
This is structurally identical to the bmx `_upsertIncentive` bug: a fixed total (`totalPay`, analogous to `amount`) is divided by a denominator (`voteSum`, analogous to `WEEK`) to derive a per-unit rate, and each recipient's payout is `unitCount * rate` truncated down. The sum of all truncated `pay` values is strictly less than or equal to `totalPay`; the difference (`totalPay - Σpay`) is the "dust" that is silently dropped every time this function runs, with no accumulator or carry-forward mechanism to recover it, exactly as the report's `total % WEEK` remainder is discarded in `_updatePool`.

This function is invoked once per block from `Manager.java` during block completion, so the truncation happens continuously rather than once per incentive top-up. Because `voteSum` (total standby witness votes) can be a very large number relative to `totalPay`, `eachVotePay` can be a small fractional value, and the per-witness truncation loss (`w.getVoteCount() * eachVotePay` fractional part, up to just under 1 SUN per witness per block) recurs on every block indefinitely.

### Impact Explanation
The truncated remainder represents TRX that should have been distributed to standby witnesses as block rewards but is instead permanently unaccounted for — it is neither paid to a witness nor tracked anywhere for later distribution. This is a genuine accounting/state divergence: the intended reward budget (`getWitness127PayPerBlock()` times number of standby witnesses) is never fully realized in practice, and the shortfall compounds with every block indefinitely, matching the "permanently locks unstreamed rewards" impact class from the analog report, applied here as "permanently drops truncated reward dust every block."

### Likelihood Explanation
This code path executes automatically on every block as part of normal witness reward accounting — no attacker action or privileged role is required, and it is not a mocked/internal-only code path. The dust loss is deterministic and occurs on essentially every block where `voteSum` does not evenly divide `totalPay`, which is the common case given real-world vote counts.

### Recommendation
Avoid floating-point division and per-recipient independent truncation. Instead, track a running remainder (`carry`) across payouts within `payStandbyWitness()` (or persist it across blocks), e.g. compute exact integer shares with `BigInteger`/scaled-integer arithmetic and add the leftover remainder to a small number of witnesses or roll it into the next block's payable pool, ensuring `Σpay == totalPay` (or the residual is carried forward rather than dropped).

### Proof of Concept
Given `totalPay = 127`, and three standby witnesses with vote counts `{1000, 2000, 3000}` (`voteSum = 6000`):
- `eachVotePay = 127.0 / 6000 = 0.021166...`
- Witness 1: `(long)(1000 * 0.021166...) = 21`
- Witness 2: `(long)(2000 * 0.021166...) = 42`
- Witness 3: `(long)(3000 * 0.021166...) = 63`
- Total paid = `21 + 42 + 63 = 126`, versus intended `totalPay = 127`.

The `1` SUN difference is dropped on this single block and is never recovered; over a large number of blocks, the cumulative unpaid dust grows without bound and is never claimable by any account, consistent with the report's "dust permanently locked" pattern. [2](#0-1)

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
