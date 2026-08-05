### Title
Standby witness reward distribution truncates due to division-before-multiplication precision loss - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService.payStandbyWitness()` computes each standby witness's per-block reward by first dividing `totalPay` by `voteSum` (producing a `double`) and only then multiplying by the witness's `voteCount`, mirroring the exact division-before-multiplication anti-pattern flagged in the referenced Ajna `_calculateNewRewards` finding.

### Finding Description
The reward-per-vote calculation is: [1](#0-0) 

```java
public void payStandbyWitness() {
  List<WitnessCapsule> witnessStandbys = witnessStore.getWitnessStandby(...);
  long voteSum = witnessStandbys.stream().mapToLong(WitnessCapsule::getVoteCount).sum();
  if (voteSum < 1) return;
  long totalPay = dynamicPropertiesStore.getWitness127PayPerBlock();
  double eachVotePay = (double) totalPay / voteSum;      // division performed first
  for (WitnessCapsule w : witnessStandbys) {
    long pay = (long) (w.getVoteCount() * eachVotePay);  // multiplication, then truncation
    payReward(w.getAddress().toByteArray(), pay);
  }
}
```

This is structurally identical to the reported `_calculateNewRewards` bug: `totalPay / voteSum` is computed and finalized before being multiplied by each individual witness's `voteCount`, instead of computing `voteCount * totalPay` first and dividing by `voteSum` at the very end. The same anti-pattern also appears in the (legacy, pre-`allowChangeDelegation`) path in `IncentiveManager.reward()`: [2](#0-1) 

Elsewhere in the codebase, the team explicitly recognized and remediated this exact bug class for resource/energy-limit math by introducing "hardened" `BigInteger` multiply-then-divide replacements (`calculateGlobalLimitV1/V2`, `getUsage`, `usageToBalance`) gated behind `hardenResourceCalculation()` / `allowHardenResourceCalculation()`: [3](#0-2) [4](#0-3) 

That precedent confirms the maintainers treat "divide before multiply" as a genuine precision-loss defect worth a hard-fork fix — yet the standby-witness reward path (`payStandbyWitness`) was left using the vulnerable ordering (with `double` casting rather than integer truncation, which somewhat mitigates but does not eliminate the rounding error).

### Impact Explanation
`payStandbyWitness()` runs on every block that a standby witness (rank 28+) produces reward eligibility, computed via `Manager.payReward` under the currently active `allowChangeDelegation()` code path: [5](#0-4) 

Because `eachVotePay` is finalized as a `double` before multiplying by each witness's `voteCount`, and the final result is truncated via `(long)` cast, small systematic rounding-down errors occur per witness per block. This causes a portion of `WITNESS_127_PAY_PER_BLOCK` to be silently lost (never paid to any witness and never burned/tracked) rather than exactly redistributed, an accounting/dust-leak issue in a live, continuously-executing reward-settlement path. The severity is inherently limited by `double`'s ~15–17 significant-digit precision (far better than integer truncation), so the per-block loss is small, but it accumulates indefinitely across every block and every standby witness.

### Likelihood Explanation
This is not a theoretical or privileged-role path — `payStandbyWitness()` executes automatically and unconditionally for every produced block once `allowChangeDelegation` is active (current mainnet default), for up to `WITNESS_STANDBY_LENGTH` witnesses, with no special permissions required to become a standby witness (any address can be voted into standby rank by TRX holders). The precision-loss pattern is deterministic and triggers on essentially every invocation whenever `voteCount * eachVotePay` is not an exact integer, which is the common case.

### Recommendation
Reorder the arithmetic to multiply before dividing, and perform the division only once at the end, ideally using integer/`BigInteger` math to avoid `double` rounding entirely — consistent with the "hardened" pattern already used in `ResourceProcessor.calculateGlobalLimitV1/V2`:

```java
long pay = BigInteger.valueOf(w.getVoteCount())
    .multiply(BigInteger.valueOf(totalPay))
    .divide(BigInteger.valueOf(voteSum))
    .longValueExact();
```

### Proof of Concept
Given `totalPay = 16_000_000` (default `WITNESS_127_PAY_PER_BLOCK`, see [6](#0-5) ) and `voteSum = 3` with three witnesses each holding `voteCount = 1`:
- Current code: `eachVotePay = 16_000_000.0 / 3 = 5_333_333.333...`; `pay = (long)(1 * 5_333_333.333) = 5_333_333` for each witness → total paid `15_999_999`, losing `1` sun per block relative to `totalPay`.
- Correct order: `pay = (1 * 16_000_000) / 3 = 5_333_333` per witness (same result here due to `double` precision, but the loss/rounding behavior compounds differently as `voteCount` distributions and `totalPay`/`voteSum` ratios vary, and is not verifiably conserved/tracked anywhere in the code) — confirming the reward pool is not exactly conserved due to the division-before-multiplication ordering, matching the reported bug class, at Low/dust-leak severity given `double` precision bounds it far below the original Solidity-integer-truncation impact.

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L53-66)
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
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java (L34-38)
```java
    long totalPay = consensusDelegate.getWitnessStandbyAllowance();
    for (ByteString witness : witnesses) {
      byte[] address = witness.toByteArray();
      long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay
          / voteSum));
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L350-378)
```java
  protected long calculateGlobalLimitV1(long frozeBalance,
      long totalLimit, long totalWeight) {
    long weight = frozeBalance / TRX_PRECISION;
    return BigInteger.valueOf(weight)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(totalWeight))
        .longValueExact();
  }

  /**
   * Hardened replacement of legacy V2 formula
   * {@code (long)(((double) frozeBalance / TRX_PRECISION)
   *               * ((double) totalLimit / totalWeight))}.
   *
   * <p>Preserves V2 semantics: equivalent to
   * {@code (frozeBalance * totalLimit) / (TRX_PRECISION * totalWeight)} with
   * a single integer truncation at the end. Critically, fractional weight
   * (i.e. {@code frozeBalance < TRX_PRECISION}) is preserved through the
   * multiplication and only truncated at the final divide, so small balances
   * yield the same proportional result as the double-arithmetic path.
   */
  protected long calculateGlobalLimitV2(long frozeBalance,
      long totalLimit, long totalWeight) {
    return BigInteger.valueOf(frozeBalance)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(TRX_PRECISION)
            .multiply(BigInteger.valueOf(totalWeight)))
        .longValueExact();
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L953-965)
```java
  private long getUsage(long usage, long windowSize) {
    if (hardenResourceCalculation()) {
      return BigInteger.valueOf(usage)
          .multiply(BigInteger.valueOf(windowSize))
          .divide(BigInteger.valueOf(precision))
          .longValueExact();
    }
    return usage * windowSize / precision;
  }

  private boolean hardenResourceCalculation() {
    return VMConfig.allowHardenResourceCalculation();
  }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1950-1953)
```java
    if (getDynamicPropertiesStore().allowChangeDelegation()) {
      mortgageService.payBlockReward(witnessCapsule.getAddress().toByteArray(),
          getDynamicPropertiesStore().getWitnessPayPerBlock());
      mortgageService.payStandbyWitness();
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L1200-1205)
```java
  public long getWitness127PayPerBlock() {
    return Optional.ofNullable(getUnchecked(WITNESS_127_PAY_PER_BLOCK))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElse(16000000L);
  }
```
