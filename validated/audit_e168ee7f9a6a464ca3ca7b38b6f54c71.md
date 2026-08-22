### Title
Double-rounding in vote-reward accumulation (Vi) causes permanently lost/unclaimed reward dust — ([File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java])

### Summary
The TRON "TVM vote" reward-distribution mechanism converts a witness's per-cycle TRX reward into a per-share value index (`Vi`), then later converts a voter's shares (`voteCount`, analogous to wlsETH's shares) back into a TRX amount. Both conversions use integer division, so exact share-to-value round-tripping is impossible, and the truncated remainder ("dust") is neither credited to the voter nor returned to the witness/reward pool — it is silently lost, matching the WlsETH root cause: `_value` and shares cannot be exactly converted in both directions, and fractional dust accumulates and disappears.

### Finding Description
`DelegationStore.accumulateWitnessVi` computes a delta value-index per cycle by dividing the witness's per-cycle reward by the total vote count for that cycle: [1](#0-0) 

`deltaVi = reward * DECIMAL_OF_VI_REWARD / voteCount` truncates any remainder of `reward * DECIMAL_OF_VI_REWARD` that isn't evenly divisible by `voteCount`. This is the "shares → underlying value" analog: `Vi` is the per-share (per-vote) value index, and `voteCount` is the total shares.

Later, when a specific voter withdraws, `VoteRewardUtil.computeReward` converts the accumulated `deltaVi` back to an actual reward amount using the voter's own vote count: [2](#0-1) 

`reward += deltaVi.multiply(BigInteger.valueOf(userVote)).divide(DECIMAL_OF_VI_REWARD).longValue()` truncates a second time. Because `DECIMAL_OF_VI_REWARD` is a fixed scale (`10^18`) rather than an exact fractional representation, both divisions lose the fractional remainder: [3](#0-2) 

The identical pattern exists in the non-VM path used for the standard `WithdrawBalanceContract`/vote flow: [4](#0-3) 

There is no mechanism anywhere in `DelegationStore` or `MortgageService`/`VoteRewardUtil` that tracks or redistributes the truncated remainder back into the reward pool for the next cycle or to any account — it is simply discarded on every `deltaVi` accumulation and on every voter's `computeReward` call. Over many cycles and many voters, this rounds down the total sum actually paid out below the sum of rewards recorded via `addReward`, exactly analogous to the WlsETH report's `v ≠ b·x·S/B` — the "share" (vote) representation cannot always be converted back to an exact value, and the difference accumulates as unrecoverable dust.

### Impact Explanation
The impact is a systemic under-payment of validator/voter rewards: real TRX value that was recorded as owed (`delegationStore.addReward`) is never fully paid out to any account because of double integer-division truncation. This is a resource/reward accounting integrity issue — value is created (minted as block reward) but a fraction of it becomes permanently unaccounted and unclaimable, which is a real but low-magnitude protocol-level accounting corruption. It does not enable theft, unauthorized account operations, or consensus divergence (all nodes compute this deterministically the same way), so it does not itself cause a chain fork; the impact is limited to a slow "leak" of reward value.

### Likelihood Explanation
Reachability requires no privileged access: any account can vote via `VoteWitnessContract` (or via the TVM `vote`/`withdrawReward` precompile in `Program.withdrawReward`, `WithdrawRewardProcessor`), and the truncation occurs automatically every cycle for every witness/voter combination. Likelihood of the rounding occurring is effectively 100% whenever `reward * 10^18` is not evenly divisible by `voteCount`, and again whenever `deltaVi * userVote` is not evenly divisible by `10^18` — this is the common case, not an edge case, so the dust accumulates continuously across the network.

### Recommendation
Track the truncation remainder per witness/cycle (e.g., carry-forward a remainder term alongside `Vi`, or use a rational/fixed-point scheme with an explicit remainder-carry) so leftover fractions are added into the next cycle's `Vi` computation instead of being discarded, ensuring the sum of amounts a witness can pay out converges to the recorded total reward rather than being permanently short by rounding dust.

### Proof of Concept
1. Configure a witness with a per-cycle reward `R` and total vote count `V` such that `R * 10^18 % V != 0` (trivial with almost any real-world numbers, e.g., `R=7`, `V=3`).
2. Call `accumulateWitnessVi` — `deltaVi` loses `(R*10^18) % V` of value silently.
3. Distribute votes among several voters whose individual `userVote` sums to `V`; call `computeReward`/`withdrawReward` for each — each `deltaVi*userVote/10^18` truncates further.
4. Sum all voters' paid rewards and compare to `R` recorded via `delegationStore.getReward(cycle, witness)`; the sum is strictly less than `R`, and the shortfall is never credited anywhere, demonstrating permanently lost reward dust, matching the same class of value-loss described in the WlsETH report (share/value conversion rounding).

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L20-22)
```java
  public static final long REMARK = -1L;
  public static final int DEFAULT_BROKERAGE = 20;
  public static final BigInteger DECIMAL_OF_VI_REWARD = BigInteger.valueOf(10).pow(18);
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

**File:** actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java (L90-109)
```java
  private static long computeReward(long beginCycle, long endCycle,
                                    AccountCapsule accountCapsule, Repository repository) {
    if (beginCycle >= endCycle) {
      return 0;
    }

    long reward = 0;
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
    return reward;
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L89-134)
```java
  public void withdrawReward(byte[] address) {
    if (!dynamicPropertiesStore.allowChangeDelegation()) {
      return;
    }
    AccountCapsule accountCapsule = accountStore.get(address);
    long beginCycle = delegationStore.getBeginCycle(address);
    long endCycle = delegationStore.getEndCycle(address);
    long currentCycle = dynamicPropertiesStore.getCurrentCycleNumber();
    long reward = 0;
    if (beginCycle > currentCycle || accountCapsule == null) {
      return;
    }
    if (beginCycle == currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        return;
      }
    }
    //withdraw the latest cycle reward
    if (beginCycle + 1 == endCycle && beginCycle < currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        reward = computeReward(beginCycle, endCycle, account);
        adjustAllowance(address, reward);
        reward = 0;
        logger.info("Latest cycle reward {}, {}.", beginCycle, account.getVotesList());
      }
      beginCycle += 1;
    }
    //
    endCycle = currentCycle;
    if (CollectionUtils.isEmpty(accountCapsule.getVotesList())) {
      delegationStore.setBeginCycle(address, endCycle + 1);
      return;
    }
    if (beginCycle < endCycle) {
      reward += computeReward(beginCycle, endCycle, accountCapsule);
      adjustAllowance(address, reward);
    }
    delegationStore.setBeginCycle(address, endCycle);
    delegationStore.setEndCycle(address, endCycle + 1);
    delegationStore.setAccountVote(endCycle, address, accountCapsule);
    logger.info("Adjust {} allowance {}, now currentCycle {}, beginCycle {}, endCycle {}, "
            + "account vote {}.", Hex.toHexString(address), reward, currentCycle,
        beginCycle, endCycle, accountCapsule.getVotesList());
  }
```
