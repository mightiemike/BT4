### Title
Reward truncation in stake-proportional witness payout causes systematic underpayment and zero-reward for low-vote witnesses - (File: chainbase/src/main/java/org/tron/core/service/MortgageService.java, consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java)

### Summary
Both `MortgageService.payStandbyWitness()` and `IncentiveManager.reward()` distribute a fixed reward pool (`totalPay`/`WitnessStandbyAllowance`) across a set of standby witnesses proportional to their vote count, using `double` arithmetic followed by a truncating cast to `long`. This is the same class of bug described in the RaptorCast report: a proportional-allocation algorithm that rounds down per-recipient shares, systematically starving low-weight participants (here, low-vote witnesses) and silently discarding the rounding remainder instead of redistributing it.

### Finding Description
In `MortgageService.payStandbyWitness()`:
```java
double eachVotePay = (double) totalPay / voteSum;
for (WitnessCapsule w : witnessStandbys) {
  long pay = (long) (w.getVoteCount() * eachVotePay);
  payReward(w.getAddress().toByteArray(), pay);
}
``` [1](#0-0) 

and identically in `IncentiveManager.reward()`:
```java
long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay / voteSum));
``` [2](#0-1) 

Each witness's share is calculated as `voteCount * (totalPay / voteSum)` and then truncated toward zero via `(long)` cast. Exactly like the RaptorCast case where `num_packets` allocated per validator was `floor(m * stake_i / total_stake)`, here `pay` is `floor(voteCount_i * totalPay / voteSum)`. Any witness whose `voteCount` is small relative to `voteSum` (analogous to a validator with small stake relative to total stake and few packets/redundancy factor) can receive `pay == 0` even though `totalPay > 0` and the witness produced a strictly positive vote weight. The sum of all distributed `pay` values across witnesses is `<= totalPay`, and the "lost" remainder from rounding is never redistributed or accounted for — it simply is not paid out to anyone, similar to how RaptorCast's rounding-down loses chunks instead of reallocating them.

This runs on every block for standby witnesses (`payStandbyWitness`, called from `Manager.payReward`) [3](#0-2)  and, in the legacy path, in `IncentiveManager.reward()`.

### Impact Explanation
Low-vote standby witnesses can be systematically denied reward payouts for many cycles/blocks whenever their proportional share truncates to zero, even though the allowance pool (`totalPay`) is nonzero. This directly affects reward/resource accounting correctness on-chain: value that should accrue to a witness's allowance is permanently lost rather than paid or redistributed, causing a persistent, protocol-level accounting discrepancy. Because this executes deterministically every block as part of core consensus reward accounting, the effect is systemic and compounds over time.

### Likelihood Explanation
This is guaranteed to occur, not just theoretically possible: any witness with `voteCount * totalPay < voteSum` receives `pay == 0` for that cycle. With `WITNESS_STANDBY_LENGTH` witnesses splitting a fixed `totalPay` (default `WITNESS_127_PAY_PER_BLOCK = 16000000`) [4](#0-3) , witnesses near the bottom of the standby ranking (lowest vote counts, which is the normal/expected steady-state distribution) are the ones most likely to be affected, occurring on essentially every block where the reward pool isn't large enough for the vote distribution.

### Recommendation
Use integer arithmetic (e.g., `BigInteger` or fixed-point) to compute `pay = totalPay * voteCount / voteSum` without an intermediate lossy `double`, and track/redistribute the rounding remainder (e.g., give the leftover from `totalPay - sum(pay_i)` to the last witness processed, or accumulate remainders across cycles) so reward funds are never silently discarded. This mirrors the "add extra unit to counter rounding-down" fix recommended for the RaptorCast packet allocation.

### Proof of Concept
Given `witnessStandbys` = 27 witnesses with `voteSum = 2,700,000,000` and `totalPay = 16,000,000`, a witness with `voteCount = 100` would compute `pay = (long)(100 * 16,000,000.0 / 2,700,000,000) = (long)(0.59...) = 0`. That witness receives zero reward for the cycle despite having a positive, nonzero vote weight and a nonzero pool to draw from, and the fractional `0.59` unit of payout is lost rather than redistributed to any other witness.

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

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L1200-1205)
```java
  public long getWitness127PayPerBlock() {
    return Optional.ofNullable(getUnchecked(WITNESS_127_PAY_PER_BLOCK))
        .map(BytesCapsule::getData)
        .map(ByteArray::toLong)
        .orElse(16000000L);
  }
```
