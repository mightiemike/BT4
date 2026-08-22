### Title
Standby witness reward rounding causes systemic reward-token loss every block - ([File: chainbase/src/main/java/org/tron/core/service/MortgageService.java])

### Summary
`MortgageService.payStandbyWitness()` divides a fixed total reward pool by the sum of standby-witness votes and then multiplies back per-witness with a `double`, truncating to `long`. Any remainder from this division is silently dropped rather than being paid out or carried forward, permanently destroying a portion of the reward pool on every block that pays standby witnesses. This mirrors the reported C4 finding where dividing an amount by a fixed rate and rounding down loses the remainder tokens.

### Finding Description
In `payStandbyWitness()`, `totalPay` (from `dynamicPropertiesStore.getWitness127PayPerBlock()`) is split proportionally across the 127 standby witnesses using `double eachVotePay = (double) totalPay / voteSum;` and then `long pay = (long) (w.getVoteCount() * eachVotePay);` for each witness. [1](#0-0) 

Because `pay` is truncated per witness (integer cast of a double), the sum of all `pay` values distributed across witnesses is generally less than `totalPay`. The difference is never credited anywhere (no remainder handling, no accumulator, no "dust" pool) — it is simply lost from the reward accounting for that block. The same rounding pattern (vote-share of a fixed total pay, truncated with `(long)`) also exists in the identical standby-reward logic in `IncentiveManager.reward()`. [2](#0-1) 

This is invoked unconditionally on every block via `Manager.payReward()`, which calls `mortgageService.payStandbyWitness()` right after paying the block-producing witness. [3](#0-2) 

The paid amount is further passed into `payReward()` where a `brokerageRate` (also a `double`) is applied and truncated again (`long brokerageAmount = (long) (brokerageRate * value);`), compounding the rounding loss. [4](#0-3) 

This is a protocol/consensus-layer accounting path (not a privileged-actor-only issue): it executes automatically for every produced block as part of `processBlock`/`payReward`, driven purely by witness vote counts recorded on-chain, with no owner/admin gating.

### Impact Explanation
Each block that distributes standby-witness rewards permanently loses up to `voteSum - 1` "units" of rounding dust (bounded by the number of witnesses relative to `eachVotePay` precision), analogous to the C4 finding where up to `denominator - 1` tokens are lost per operation. Since this executes on every single block indefinitely (as opposed to a one-off admin deposit in the original DelegatedStaking report), the aggregate token loss accumulates continuously across the chain's lifetime, silently reducing the amount of TRX actually paid out to voters/witnesses relative to what dynamic parameters intend, and creating a persistent discrepancy between the nominal reward budget and rewards actually distributed. This is a value-leakage / accounting-corruption issue rather than a fund-theft issue.

### Likelihood Explanation
Likelihood is high: the rounding occurs deterministically on every block via `Manager.payReward()` → `MortgageService.payStandbyWitness()` whenever `dynamicPropertiesStore.allowChangeDelegation()` is enabled, requiring no attacker action, malicious input, or privileged access — it is a systemic, always-triggered computation.

### Recommendation
- Use integer/`BigInteger` arithmetic (e.g., `Maths`/`floorDiv` with a running remainder or largest-remainder distribution) instead of `double` for `eachVotePay`/`pay` in `MortgageService.payStandbyWitness()` and `IncentiveManager.reward()`, and carry forward the leftover ("dust") to be added to the next cycle's pool or the last witness's payment, so the sum of `pay` equals `totalPay` exactly.
- Apply the same fix to `brokerageAmount` computation in `payReward()`, replacing the `double`-based multiplication/truncation with rational arithmetic that accounts for the remainder rather than dropping it.

### Proof of Concept
Given `voteSum = 7` and `totalPay = 100`, `eachVotePay = 100.0/7 ≈ 14.2857`. For witnesses with vote counts `[1,1,1,1,1,1,1]`, each gets `(long)(1 * 14.2857) = 14`, totaling `98` paid out of `100` — 2 units are lost every block this distribution occurs, with no code path crediting the missing 2 units anywhere: [5](#0-4)

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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L79-87)
```java
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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1946-1965)
```java
  private void payReward(BlockCapsule block) {
    WitnessCapsule witnessCapsule =
        chainBaseManager.getWitnessStore().getUnchecked(block.getInstance().getBlockHeader()
            .getRawData().getWitnessAddress().toByteArray());
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
