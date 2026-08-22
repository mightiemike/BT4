I have sufficient evidence to build the analog. This maps cleanly to the reported bug class: a rounding/dust discrepancy in per-user share-of-reward accounting that leaves the sum of amounts distributed to individual holders not matching the total reward pool tracked for the group.

### Title
Reward-per-vote (Vi) accounting uses double integer-division truncation, causing systematic dust that isn't reflected in claimable balances - ([File: chainbase/src/main/java/org/tron/core/store/DelegationStore.java])

### Summary
java-tron's new voting-reward algorithm distributes each witness's per-cycle reward to voters using a per-vote reward index ("Vi"), accumulated with `BigInteger` integer division and then re-divided per user when rewards are claimed. Both steps truncate (floor) fractional remainders, so — exactly like the StWSX `oracleClaimRewards()` finding — the total amount attributable to individual voters when summed will not equal the total reward recorded for that witness/cycle. This is the same bug class: rounding in share-based accounting produces an ever-growing, unattributed "dust" balance.

### Finding Description
`DelegationStore.accumulateWitnessVi` (and the duplicate logic in `RewardViCalService.accumulateWitnessVi`) computes the delta reward-index for a cycle as:
```
BigInteger deltaVi = BigInteger.valueOf(reward)
    .multiply(DECIMAL_OF_VI_REWARD)
    .divide(BigInteger.valueOf(voteCount));
``` [1](#0-0) 

This is a first floor-division: `reward * 1e18 / voteCount` discards the remainder `(reward * 1e18) % voteCount`, so a portion of the witness's total reward for that cycle is not represented in `Vi` at all.

When a voter later claims, `MortgageService.computeReward` (used from `WithdrawBalanceContract`/`Wallet`) and its TVM equivalent `VoteRewardUtil.computeReward` (used from `withdrawreward()`/`rewardBalance()` opcodes reachable by any contract) both perform a second floor-division per user:
```
reward += deltaVi.multiply(BigInteger.valueOf(userVote))
    .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
``` [2](#0-1) [3](#0-2) 

Even if `sum(userVote) == totalVoteCount`, summing the individually-floored `deltaVi * userVote / 1e18` terms across all voters is not guaranteed to equal `deltaVi * totalVoteCount / 1e18`, let alone the original `reward` value before the first truncation. The net effect: `sum(claimable rewards across all voters for a witness/cycle) <= reward` recorded via `DelegationStore.addReward`/`delegationStore.getReward`, with the shortfall being un-attributed "dust" baked permanently into `RewardViStore`'s Vi values — mirroring the StWSX `totalSupply` vs. `sum(balances)` drift described in the report. `DECIMAL_OF_VI_REWARD` is fixed at `10^18` [4](#0-3) , so precision loss is bounded per operation but accumulates every maintenance cycle across every witness and every voter, and is reachable purely by normal voting/reward flow (`MaintenanceManager.doMaintenance` calling `accumulateWitnessVi` every cycle) and by any smart contract calling `withdrawreward()`/`rewardBalance()` TVM opcodes — no privileged actor required.

### Impact Explanation
This causes a small, permanent, and continuously growing discrepancy between the total SR reward pool recorded per cycle and the sum of rewards actually claimable by voters. Analogous to the reported issue, this is not directly exploitable to steal funds beyond legitimate proportional share, but it is a genuine on-chain accounting inconsistency (reward "dust" is neither claimable by any single account nor conserved), and the drift compounds over many cycles/witnesses/voters as network usage grows — matching the report's stated concern that "the difference... has the potential to increase as deposit balances and rewards increase."

### Likelihood Explanation
High likelihood of occurrence (it happens on essentially every maintenance cycle for every witness with votes, since exact division is the exception rather than the rule with real-world vote counts), but low-to-medium severity since the accumulated error is bounded by the number of division operations, not by value scale, and no path lets a user claim more than their proportional share of already-recorded reward.

### Recommendation
Use full-precision remainder tracking (carry forward the truncated remainder into the next cycle's `Vi` accumulation rather than discarding it), or track a global "reward dust" ledger to reconcile drift, similar to the "dust rebase" approach the LiquiStake team stated it plans to implement for StWSX.

### Proof of Concept
Given a witness with `voteCount = 3` and `reward = 10` (in SUN) for a cycle:
- `deltaVi = 10 * 1e18 / 3 = 3333333333333333333` (floor; true value has repeating remainder).
- Voter A holds `1` vote: claim `= deltaVi * 1 / 1e18 = 3`.
- Voter B holds `1` vote: claim `= 3`.
- Voter C holds `1` vote: claim `= 3`.
- Sum of claims = `9`, while `reward` recorded for that cycle was `10` — `1` SUN of dust is permanently unattributed and unrecoverable by any account, repeating every cycle for every witness/voter combination that doesn't divide evenly. [1](#0-0) [5](#0-4)

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L20-22)
```java
  public static final long REMARK = -1L;
  public static final int DEFAULT_BROKERAGE = 20;
  public static final BigInteger DECIMAL_OF_VI_REWARD = BigInteger.valueOf(10).pow(18);
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L133-145)
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

**File:** actuator/src/main/java/org/tron/core/vm/utils/VoteRewardUtil.java (L90-110)
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
  }
```
