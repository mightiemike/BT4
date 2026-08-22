### Title
Reward splitting via floating-point double arithmetic causes cumulative over-issuance of standby witness rewards, breaking the fixed reward budget invariant - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`, `consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java`)

### Summary
The reported bug class is: a fixed pool of value is split proportionally between multiple recipients using floating-point (`double`) multiplication/division per recipient, without enforcing that the sum of the distributed parts equals the original pool. Independent per-recipient rounding (via `(long)` truncation of a `double`) can make the sum diverge from the intended total, silently breaking the accounting invariant and causing the protocol to distribute more (or less) value than it actually holds/budgets for.

The same pattern exists in java-tron's witness reward-distribution logic, which splits a fixed total reward pool (`WITNESS_127_PAY_PER_BLOCK` / `WITNESS_STANDBY_ALLOWANCE`) across all standby witnesses using per-witness `double` arithmetic, with no leftover/remainder reconciliation.

### Finding Description
`MortgageService.payStandbyWitness()` computes a fixed `totalPay` for the block (`WITNESS_127_PAY_PER_BLOCK`) and an `eachVotePay = (double) totalPay / voteSum` ratio, then for every standby witness computes: [1](#0-0) 

```java
long totalPay = dynamicPropertiesStore.getWitness127PayPerBlock();
double eachVotePay = (double) totalPay / voteSum;
for (WitnessCapsule w : witnessStandbys) {
  long pay = (long) (w.getVoteCount() * eachVotePay);
  payReward(w.getAddress().toByteArray(), pay);
}
```

Each `pay` value is computed and truncated independently for every witness, with no accumulator tracking how much of `totalPay` has already been allocated and no final "leftover" correction step (unlike, e.g., `_handleWethRewardDistribution` in the external report, which at least computes and reallocates a leftover — though even there the invariant broke down elsewhere). Because `double` multiplication of `voteCount * eachVotePay` is not exact, and because `(long)` truncation is applied per-term rather than to a running remainder, the sum `Σ pay` over all standby witnesses is not guaranteed to equal `totalPay`; it can drift above or below it depending on vote distribution.

The exact same anti-pattern appears in `IncentiveManager.reward()`, which is the consensus-layer counterpart used when `allowChangeDelegation` is not yet enabled: [2](#0-1) 

```java
long totalPay = consensusDelegate.getWitnessStandbyAllowance();
for (ByteString witness : witnesses) {
  long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay / voteSum));
  accountCapsule.setAllowance(accountCapsule.getAllowance() + pay);
}
```

Both functions are invoked unconditionally on every block during `Manager.payReward()` / `MaintenanceManager.doMaintenance()`, i.e. from ordinary block production — no privileged actor or special input is required; any set of active witnesses with vote counts that produce non-terminating fractions when divided will trigger the drift.

By contrast, other parts of the codebase that perform structurally identical proportional splits (e.g. `EnergyProcessor.calculateGlobalEnergyLimitV2`, `ResourceProcessor.calculateGlobalLimitV2`, `RepositoryImpl.getUsage`) have been explicitly "hardened" to use exact `BigInteger` arithmetic instead of `double`, guarded by an `allowHardenResourceCalculation`/`disableJavaLangMath` flag, precisely because the legacy `double`-based formulas were found to introduce precision loss: [3](#0-2) 

This shows the project is aware of, and has already remediated, this exact class of rounding bug in resource accounting — but the reward-distribution code paths in `MortgageService.payStandbyWitness` and `IncentiveManager.reward` were left using the legacy `double` formula with no analogous hardening or leftover-reconciliation logic.

### Impact Explanation
Because `pay` is credited directly as new account `allowance` (effectively minted TRX, since standby-witness pay is not debited from any existing account balance), cumulative rounding drift causes the actual amount of TRX issued per block/maintenance cycle for standby-witness rewards to differ from the configured `WITNESS_127_PAY_PER_BLOCK` / `WITNESS_STANDBY_ALLOWANCE` budget. This breaks the accounting invariant that the protocol's reward issuance should exactly match its configured emission schedule — a form of resource/reward accounting corruption. Over many blocks and maintenance cycles across the live network, this can accumulate into a persistent, unaccounted-for excess (or deficit) in total TRX supply relative to the documented reward parameters.

### Likelihood Explanation
This code executes unconditionally on every block (`payStandbyWitness`) and every maintenance cycle (`IncentiveManager.reward`), with no attacker action needed — it is a deterministic consequence of the `double` division/multiplication whenever `voteSum` does not evenly divide `totalPay` for the witnesses' vote-count distribution, which is essentially guaranteed in production given real-world vote counts. However, the per-block/per-cycle drift is bounded by floating-point epsilon and vote-count magnitude, so a single occurrence is small; the accounting deviation is a slow, silent, systemic drift rather than an immediately exploitable large-scale fund drain, which is a materially weaker impact profile than the original report's insolvency-causing insufficient-balance scenario.

### Recommendation
Replace the `double`-based per-witness pay computation in `MortgageService.payStandbyWitness()` and `IncentiveManager.reward()` with exact integer arithmetic (e.g., `BigInteger` multiply/divide as already done in `ResourceProcessor.calculateGlobalLimitV2`), and explicitly track/allocate any remainder (e.g., add leftover to the last witness or to the network's designated overflow/pool account) so that `Σ pay == totalPay` is enforced as an invariant, consistent with the hardening already applied elsewhere in the codebase for resource-limit calculations.

### Proof of Concept
Not directly demonstrable as an on-chain PoC without a live network, since the effect is a slow accounting drift rather than a single-transaction revert. The drift can be shown analytically/in a unit test: construct a set of standby witnesses with vote counts summing to `voteSum` such that `totalPay / voteSum` is a repeating binary fraction (e.g., `voteSum` not a power of two), then assert `Σ (long)(voteCount_i * (double)totalPay/voteSum) != totalPay` for at least one such distribution — mirroring the existing hardening regression tests in `framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java` that already demonstrate `double`-vs-`BigInteger` divergence for the analogous resource-limit formulas.

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

**File:** consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java (L20-43)
```java
  public void reward(List<ByteString> witnesses) {
    if (consensusDelegate.allowChangeDelegation()) {
      return;
    }
    if (witnesses.size() > WITNESS_STANDBY_LENGTH) {
      witnesses = witnesses.subList(0, WITNESS_STANDBY_LENGTH);
    }
    long voteSum = 0;
    for (ByteString witness : witnesses) {
      voteSum += consensusDelegate.getWitness(witness.toByteArray()).getVoteCount();
    }
    if (voteSum <= 0) {
      return;
    }
    long totalPay = consensusDelegate.getWitnessStandbyAllowance();
    for (ByteString witness : witnesses) {
      byte[] address = witness.toByteArray();
      long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay
          / voteSum));
      AccountCapsule accountCapsule = consensusDelegate.getAccount(address);
      accountCapsule.setAllowance(accountCapsule.getAllowance() + pay);
      consensusDelegate.saveAccount(accountCapsule);
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L359-378)
```java
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
