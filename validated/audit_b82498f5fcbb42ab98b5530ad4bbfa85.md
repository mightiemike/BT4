### Title
Unchecked `BigInteger.longValue()` truncation in vote-reward `Vi` accumulator can silently corrupt witness reward accounting - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
The DPoS vote-reward mechanism computes a cumulative "reward-per-vote" index (`Vi`, analogous to Sophon's `accPointsPerShare`) that is scaled by `DECIMAL_OF_VI_REWARD = 1e18` and accumulated additively, unboundedly, every maintenance cycle by dividing a witness's per-cycle reward by its per-cycle `voteCount`. When a witness's recorded vote count is abnormally small relative to the reward it earns, the accumulated `Vi` value grows very large. This value is later multiplied by a voter's `userVote` and truncated to a `long` via `BigInteger.longValue()`, which — unlike the `LongMath.checkedAdd`/`checkedMultiply` used elsewhere in this same reward pipeline — performs **silent, unchecked truncation** instead of throwing on overflow.

### Finding Description
Each maintenance cycle, `RewardViCalService.accumulateWitnessVi` / `DelegationStore.accumulateWitnessVi` compute: [1](#0-0) 

```
BigInteger deltaVi = BigInteger.valueOf(reward)
    .multiply(DECIMAL_OF_VI_REWARD)   // 1e18
    .divide(BigInteger.valueOf(voteCount));
setWitnessVi(cycle, address, preVi.add(deltaVi));
```

`Vi` is monotonically non-decreasing (`preVi.add(deltaVi)`, never re-based), exactly like `accPointsPerShare` in the referenced Sophon bug. Because `voteCount` is the divisor, any cycle where a witness's `getWitnessVote` value is very small relative to the reward it received (`delegationStore.getReward`) inflates `deltaVi` sharply, and that inflation is permanently baked into all subsequent `Vi` values.

When a voter later claims rewards, `MortgageService.computeReward` (and the duplicate logic in `VoteRewardUtil.computeReward` used by the TVM `RewardBalance`/`WithdrawReward` precompiles) does: [2](#0-1) 

```
BigInteger deltaVi = endVi.subtract(beginVi);
...
long userVote = vote.getValue();
reward += deltaVi.multiply(BigInteger.valueOf(userVote))
    .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
```

`deltaVi.multiply(userVote)` is computed with arbitrary-precision `BigInteger` (no overflow there), but the final `.longValue()` call **silently wraps around** if the result does not fit in a signed 64-bit `long` — it does not throw, unlike `LongMath.checkedAdd`/`checkedMultiply`, which this same codebase deliberately uses in `VoteWitnessProcessor` to guard against overflow: [3](#0-2) 

The presence of `checkedAdd`/`checkedMultiply` in the voting path but *not* in the reward-index path shows the overflow risk was addressed inconsistently — the reward math is the one place where a wrap can occur without any exception being raised, so it goes completely undetected and the resulting (possibly negative or wildly wrong) `reward` value is added to the user's `allowance`.

This mirrors the Sophon `accPointsPerShare` bug class precisely:
- A "supply" quantity (`voteCount`, analogous to `lpSupply`) can be driven very low relative to the "reward" numerator.
- The per-share accumulator (`Vi`, analogous to `accPointsPerShare`) is stored with 18-decimal (`1e18`) scaling and accumulates additively forever.
- The accumulator is later multiplied by a user-controlled quantity (`userVote`) before being scaled back down, and the multiplication step is where the overflow surfaces.

### Impact Explanation
If `Vi` inflation combined with a voter's `userVote` causes the `BigInteger` product to exceed `Long.MAX_VALUE`/`Long.MIN_VALUE` before `.longValue()` is applied, the truncated result silently becomes an arbitrary (potentially negative or drastically wrong) `long`. This value flows into:
- `MortgageService.withdrawReward` → `adjustAllowance`, which credits/debits the account's `allowance` (spendable TRX reward balance). A wrapped positive value would incorrectly mint reward into a user's balance; a wrapped negative value is silently dropped (`adjustAllowance` no-ops for `amount <= 0`), permanently and silently losing that user's legitimately earned reward, since `beginCycle`/`endCycle` are advanced unconditionally regardless of whether the computed reward was valid.
- The TVM precompiles `RewardBalance`/`WithdrawReward` (via `VoteRewardUtil`), which are reachable directly by any contract/account through the TVM, giving an attacker-controlled surface to trigger the miscalculation and observe/exploit the corrupted result on-chain.

This constitutes reward/accounting corruption (potential unauthorized value creation or permanent loss of legitimately earned rewards) reachable through normal voting/withdrawal transactions.

### Likelihood Explanation
Triggering requires a witness's recorded `voteCount` for a cycle to be abnormally small relative to the reward it receives in that cycle (e.g., a witness that briefly qualifies for block production/reward while most of its votes are withdrawn near a maintenance boundary), sustained/repeated so the additive `Vi` accumulates to a very large value before a large voter's `userVote` is applied against the delta. This is a non-privileged, protocol-level edge case (no special key/node access needed) but requires specific vote-timing manipulation across multiple maintenance cycles, making it a real but conditions-dependent (moderate likelihood) path, analogous to the "1 wei first depositor" precondition in the original Sophon finding.

### Recommendation
- Replace the unchecked `BigInteger.longValue()` calls in `MortgageService.computeReward`, `VoteRewardUtil.computeReward`, and `RewardViCalService.getNewRewardAlgorithmReward` with a checked conversion (e.g., `BigInteger.longValueExact()` or an explicit range check) so an out-of-range result throws/reverts instead of silently wrapping.
- Consider bounding/flooring `voteCount` (or rejecting reward accrual when `voteCount` is implausibly small relative to `reward`) to prevent the `Vi` accumulator from being inflated in the first place, mirroring the "set a floor for deposits" remediation applied in the referenced Sophon fix.

### Proof of Concept
Conceptual (long overflow via silent `BigInteger.longValue()` truncation):
```java
BigInteger deltaVi = BigInteger.valueOf(2).pow(70);   // artificially inflated Vi delta
                                                        // from many cycles of reward/voteCount≈tiny
long userVote = 10_000_000L;                           // a large legitimate voter
long reward = deltaVi.multiply(BigInteger.valueOf(userVote))
        .divide(DelegationStore.DECIMAL_OF_VI_REWARD)
        .longValue();
// reward silently wraps to an arbitrary (possibly negative) long instead of throwing,
// exactly as in MortgageService.computeReward / VoteRewardUtil.computeReward
```
This reproduces, in Java semantics, the same class of failure demonstrated in the Sophon `testOverflow()` PoC: an artificially deflated "supply" divisor inflates a persistently-accumulated per-share index, which later overflows when multiplied by a legitimate user's balance/vote amount during reward settlement.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L215-230)
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
    }
    return reward;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L81-95)
```java
          sum = LongMath.checkedAdd(sum, voteCount);
          // merge vote for same witness
          voteMap.put(vote.getVoteAddress(),
              LongMath.checkedAdd(voteMap.getOrDefault(vote.getVoteAddress(), 0L), voteCount));
        }
      }

      long tronPower;
      if (repo.getDynamicPropertiesStore().supportUnfreezeDelay()
          && repo.getDynamicPropertiesStore().supportAllowNewResourceModel()) {
        tronPower = accountCapsule.getAllTronPower();
      } else {
        tronPower = accountCapsule.getTronPower();
      }
      sum =  LongMath.checkedMultiply(sum, TRX_PRECISION);
```
