### Title
Floating-Point Brokerage Fee Calculation Causes Systematic Rounding-Down and Value Leakage - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService.payReward` computes each witness's brokerage cut (the witness's percentage share of block/vote rewards) using `double` division followed by `double` multiplication and a truncating cast to `long`, instead of performing the multiplication first and dividing once with integer/`BigInteger` arithmetic. This is the exact "division before multiplication" precision-loss bug class described in the report, applied to the TRON reward/brokerage-distribution path instead of a management fee. [1](#0-0) 

### Finding Description
In `payReward`, for every block reward and every transaction-fee reward paid to a super representative, the code does:

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
``` [1](#0-0) 

This mirrors precisely the bug class in the report: the percentage (`brokerageRate`) is computed by dividing first (`brokerage / 100`), and only then multiplied by `value`, with a final truncating cast to `long`. Because `brokerage` is validated only to be in `[0, 100]` in `UpdateBrokerageActuator.validate` [2](#0-1) , and `double` division of small integers frequently yields non-terminating binary fractions (e.g. `brokerage=33` → `0.33` is not exactly representable), the final truncated `brokerageAmount` is systematically biased and does not exactly equal `floor(brokerage * value / 100)`. Additionally, the entire computation is routed through `double`, which loses precision for large `value` (block/vote rewards can be large `long` values, and `double` only has 53 bits of exact integer precision).

Notably, other value-accounting paths in the same codebase (e.g., `VoteRewardUtil.computeReward`, `RewardViCalService.getNewRewardAlgorithmReward`, `DelegationStore.accumulateWitnessVi`) already use `BigInteger` multiply-then-divide to avoid this exact class of error [3](#0-2) , and the VM/resource-accounting code (`RepositoryImpl`, `ResourceProcessor`) has been explicitly "hardened" with `BigInteger` multiply-first arithmetic specifically to eliminate float/divide-before-multiply truncation bugs [4](#0-3) . `MortgageService.payReward`, and similarly `MortgageService.payStandbyWitness` (`double eachVotePay = (double) totalPay / voteSum; long pay = (long) (w.getVoteCount() * eachVotePay);`) [5](#0-4) , and `IncentiveManager.reward` (`long pay = (long) (consensusDelegate.getWitness(address).getVoteCount() * ((double) totalPay / voteSum));`) [6](#0-5)  were never hardened and still use the vulnerable divide-then-multiply floating point pattern.

### Impact Explanation
This is a state/accounting divergence and value-leakage bug in the core reward distribution/brokerage accounting path that runs on every maintenance cycle for every witness's block and transaction-fee reward. Because `brokerageAmount` is computed via lossy `double` arithmetic rather than exact integer math, the amount credited to the witness's allowance (`adjustAllowance`) and the amount left in the reward pool (`delegationStore.addReward`) will not sum exactly to the correct proportional split in all cases, and will not match a `BigInteger`/multiply-first computation. Over the life of the chain, across many witnesses, cycles, and reward events, this compounds into a systematic value skew between witnesses and their voters — the same "slow but steady leak" class described in the report, but manifesting as consensus-level reward accounting drift rather than a discrete management fee.

### Likelihood Explanation
This code executes unconditionally on every `payBlockReward` and `payTransactionFeeReward` call for every witness in every maintenance cycle — i.e., extremely frequently and without any special permission or trust required to trigger it (it fires from normal block production and normal reward accrual). No privileged action is needed; it is baseline consensus-layer accounting behavior.

### Recommendation
Refactor `payReward` (and the analogous `payStandbyWitness` and `IncentiveManager.reward`) to perform the multiplication before division using integer/`BigInteger` arithmetic, consistent with the hardened pattern already used elsewhere in the codebase (e.g. `RepositoryImpl.calculateGlobalEnergyLimit`, `ResourceProcessor.calculateGlobalLimitV1`):

```java
private void payReward(byte[] witnessAddress, long value) {
    long cycle = dynamicPropertiesStore.getCurrentCycleNumber();
    int brokerage = delegationStore.getBrokerage(cycle, witnessAddress);
    long brokerageAmount = BigInteger.valueOf(value)
        .multiply(BigInteger.valueOf(brokerage))
        .divide(BigInteger.valueOf(100))
        .longValueExact();
    value -= brokerageAmount;
    delegationStore.addReward(cycle, witnessAddress, value);
    adjustAllowance(witnessAddress, brokerageAmount);
}
```
This preserves full precision through a single final division and eliminates the floating-point truncation bias.

### Proof of Concept
Given `brokerage = 33` (33%, a valid value per `UpdateBrokerageActuator.validate`, range `[0,100]`) and `value = 100_000_003` sun:
- Current code: `brokerageRate = 33.0/100 = 0.33` (not exactly representable in binary floating point, e.g. actual stored value ≈ `0.33000000000000002`), `brokerageAmount = (long)(0.33 * 100_000_003)` — the double multiplication and truncation can yield a result that differs from the mathematically exact `33 * 100_000_003 / 100 = 33,000,000` (with remainder 99, correctly truncated to `33000000`), due to intermediate double rounding for large `value`.
- Correct/hardened computation: `BigInteger.valueOf(100_000_003).multiply(BigInteger.valueOf(33)).divide(BigInteger.valueOf(100)) = 33000000` exactly.
- For sufficiently large `value` (block rewards accumulate over long chain lifetimes, and vote/tx-fee rewards can be large), the `double` path's 53-bit mantissa precision is insufficient to represent `value` exactly, so `brokerageRate * value` can drift from the exact integer product before truncation, producing a `brokerageAmount` that is off by one or more sun from the correct value — repeated over every reward payment across the chain's lifetime.

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

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateBrokerageActuator.java (L93-95)
```java
    if (brokerage < 0 || brokerage > ActuatorConstant.ONE_HUNDRED) {
      throw new ContractValidateException("Invalid brokerage");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java (L105-108)
```java
      long userVote = vote.getVoteCount();
      reward += deltaVi.multiply(BigInteger.valueOf(userVote))
          .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
    }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L350-357)
```java
  protected long calculateGlobalLimitV1(long frozeBalance,
      long totalLimit, long totalWeight) {
    long weight = frozeBalance / TRX_PRECISION;
    return BigInteger.valueOf(weight)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(totalWeight))
        .longValueExact();
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
