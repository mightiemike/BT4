### Title
Reward-per-vote accumulator (`Vi`) can be inflated by low-vote witnesses, causing silent `long` overflow/truncation in delegation reward accounting - ([File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java])

### Summary
The Sophon bug class (an accumulator computed as `rewardScaled / smallDenominator` that is added, never reset, to a running per-share value, and later multiplied by a user-controlled amount) has a structural analog in java-tron's DPoS reward-accounting system: the per-witness reward-per-vote accumulator `Vi`.

### Finding Description
Every maintenance cycle, `MaintenanceManager.doMaintenance()` calls `DelegationStore.accumulateWitnessVi(cycle, witnessAddress, witness.getVoteCount())` for every witness: [1](#0-0) 

`accumulateWitnessVi` computes the incremental "value-per-vote" for the cycle by dividing the witness's total cycle reward (scaled by `DECIMAL_OF_VI_REWARD = 1e18`) by the witness's `voteCount`, then **adds** it to the previous accumulated `Vi`: [2](#0-1) 

This is the same shape as Sophon's `accPointsPerShare = pointReward/lpSupply + pool.accPointsPerShare`: a monotonically-growing accumulator whose per-cycle increment is inversely proportional to a denominator (`voteCount`) that can be very small. `VoteWitnessActuator` only requires `voteCount > 0` (minimum 1 vote = 1 TRX), so a witness can have `voteCount = 1` for one or more cycles: [3](#0-2) 

If reward is paid to a witness with `voteCount = 1` (e.g., via `payBlockReward`/`payTransactionFeeReward`, which credit a fixed amount per produced block irrespective of vote count) in `MortgageService.payReward`, the per-cycle `deltaVi = reward * 1e18 / 1` is added into `Vi` and persists forever (unlike Solidity's fixed-width `uint256`, `Vi` is stored as an arbitrary-precision `BigInteger`, so the accumulator itself cannot overflow — but this only defers the bug downstream).

Later, when any voter's reward is computed, `Vi` is consumed and forced back into a fixed-width `long`: [4](#0-3) 
and identically in the TVM-facing path: [5](#0-4) 

`deltaVi.multiply(BigInteger.valueOf(userVote)).divide(DECIMAL_OF_VI_REWARD).longValue()` — `BigInteger.longValue()` **silently truncates/wraps** (no exception) if the magnitude exceeds `Long.MAX_VALUE`. Note that elsewhere in the very same actuator family (`VoteWitnessActuator.validate()`), the code deliberately uses `LongMath.checkedAdd`/`checkedMultiply` to guard against overflow: [6](#0-5) 
but the equivalent reward-consumption code path in `MortgageService`/`VoteRewardUtil` has no such guard, so an inflated `Vi` (driven by one or more low-vote cycles) combined with a large `userVote` in the final multiplication can silently produce an incorrect (wrapped/negative) `long` reward — the accounting analog of the Sherlock overflow.

### Impact Explanation
A silent `long` overflow in `computeReward`/`_pendingPoints`-equivalent code corrupts the delegation reward/allowance accounting: `adjustAllowance` would apply an incorrect (potentially negative or drastically wrong) value to `account.allowance`, which is later used for TRX withdrawal via `withdrawReward`/`WithdrawBalanceActuator`. This is a state/accounting-divergence class bug — it can lead to voters receiving grossly incorrect reward amounts, without any transaction reverting (Java integer overflow does not throw), unlike the Solidity case which reverts. Silent-wrong-value is arguably more severe operationally, since there is no automatic detection.

### Likelihood Explanation
This requires: (1) at least one witness that is actively earning block/transaction-fee rewards while having an extremely small `voteCount` for one or more cycles, and (2) a subsequent voter with a comparatively large `userVote` querying/withdrawing across the affected cycle range so the multiplication in `computeReward` overflows `long`. Condition (1) is realistic mainly on private/consortium chains, freshly bootstrapped networks, or brief windows where an SR's votes have been withdrawn to near-zero while it is still an active block producer for that cycle — it is not achievable at will by an ordinary user on a mature, competitive network like TRON mainnet, since becoming a top-27 active witness with `voteCount = 1` is not realistic there. This lowers likelihood relative to the original Sophon report (where any user could self-inflate the share denominator via a first deposit), but the underlying code defect (unmatched fixed-width truncation absent overflow-checked arithmetic, present in the exact same file family that otherwise uses `LongMath.checkedX`) is real and unguarded.

### Recommendation
- Use `Math.multiplyExact`/`Math.addExact` (or `LongMath.checkedMultiply`/`checkedAdd`, consistent with `VoteWitnessActuator`) instead of raw `BigInteger.longValue()` truncation when converting the final reward computation back to `long` in `MortgageService.computeReward` and `VoteRewardUtil.computeReward`.
- Consider bounding/floors on `voteCount` used as a divisor in `accumulateWitnessVi` (`DelegationStore.java`, `RewardViCalService.java`) to prevent per-cycle `Vi` increments from becoming disproportionately large relative to typical vote sizes.
- Add explicit overflow assertions/tests around `computeReward` with adversarial low-vote-count witness inputs.

### Proof of Concept
Conceptual reproduction (cannot be executed without live chain state):
1. Deploy/operate a network where a witness `W` can become an active block-producing SR with `voteCount = 1` for cycle `C` (e.g., a private/consortium chain with few witnesses).
2. Let `W` produce blocks during cycle `C`, accruing `payBlockReward`/`payTransactionFeeReward` into `delegationStore.addReward(C, W, value)`.
3. At `doMaintenance()` for cycle `C`, `accumulateWitnessVi(C, W, 1)` computes `deltaVi = value * 1e18 / 1`, added into `Vi[C][W]`, an arbitrarily large `BigInteger` persisted without truncation.
4. A separate voter with a large `userVote` (e.g., `1e13` sun-equivalent votes) later calls `MortgageService.computeReward`/`withdrawReward` spanning cycle `C`; the line `deltaVi.multiply(BigInteger.valueOf(userVote)).divide(DECIMAL_OF_VI_REWARD).longValue()` can exceed `Long.MAX_VALUE`, silently truncating to an incorrect (possibly negative) `long`, corrupting `reward`/`allowance` state without any exception being raised. [4](#0-3)

### Citations

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L96-100)
```java
    if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
      long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
      });
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L133-146)
```java
  public void accumulateWitnessVi(long cycle, byte[] address, long voteCount) {
    BigInteger preVi = getWitnessVi(cycle - 1, address);
    long reward = getReward(cycle, address);
    if (reward == 0 || voteCount == 0) { // Just forward pre vi
      if (!BigInteger.ZERO.equals(preVi)) { // Zero vi will not be record
        setWitnessVi(cycle, address, preVi);
      }
    } else { // Accumulate delta vi
      BigInteger deltaVi = BigInteger.valueOf(reward)
          .multiply(DECIMAL_OF_VI_REWARD)
          .divide(BigInteger.valueOf(voteCount));
      setWitnessVi(cycle, address, preVi.add(deltaVi));
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L107-110)
```java
        long voteCount = vote.getVoteCount();
        if (voteCount <= 0) {
          throw new ContractValidateException("vote count must be greater than 0");
        }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L120-138)
```java
        sum = LongMath.checkedAdd(sum, vote.getVoteCount());
      }

      AccountCapsule accountCapsule = accountStore.get(ownerAddress);
      if (accountCapsule == null) {
        throw new ContractValidateException(
            ACCOUNT_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
      }

      long tronPower;
      DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
      if (dynamicStore.supportAllowNewResourceModel()) {
        tronPower = accountCapsule.getAllTronPower();
      } else {
        tronPower = accountCapsule.getTronPower();
      }

      sum = LongMath
          .checkedMultiply(sum, TRX_PRECISION); //trx -> drop. The vote count is based on TRX
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

**File:** actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java (L97-108)
```java
    for (Protocol.Vote vote : accountCapsule.getVotesList()) {
      byte[] srAddress = vote.getVoteAddress().toByteArray();
      BigInteger beginVi = repository.getDelegationStore().getWitnessVi(beginCycle - 1, srAddress);
      BigInteger endVi = repository.getDelegationStore().getWitnessVi(endCycle - 1, srAddress);
      BigInteger deltaVi = endVi.subtract(beginVi);
      if (deltaVi.signum() <= 0) {
        continue;
      }
      long userVote = vote.getVoteCount();
      reward += deltaVi.multiply(BigInteger.valueOf(userVote))
          .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
    }
```
