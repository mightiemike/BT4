### Title
Floating-point precision loss in legacy witness-reward distribution irrecoverably strands SR-cycle reward dust - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService.computeReward(long cycle, List<Pair<byte[], Long>> votes)` — the pre-VI ("old algorithm") voter reward computation used for cycles before `newRewardAlgorithmEffectiveCycle` — allocates a fixed, already-escrowed per-SR reward bucket (`delegationStore.getReward(cycle, srAddress)`) to individual voters using floating-point vote-share math with an implicit narrowing (truncating) cast to `long`. This is structurally the same bug class as the reported MultiRewards `_notifyRewardAmount()` issue: a full reward amount is credited/escrowed once, but the per-recipient distribution formula truncates fractional shares, leaving a residual that nobody ever claims and that the contract never tracks or recovers.

### Finding Description
The reward pot for a given cycle/SR is created once and stored verbatim via `DelegationStore.addReward()`/`getReward()`: [1](#0-0) 

That pot is treated as fully "escrowed" for the SR's voters exactly like the ERC20 tokens pulled in by `safeTransferFrom` in the original MultiRewards report. When a voter withdraws, their share is computed with double-precision division and multiplication, then implicitly truncated back to `long`: [2](#0-1) 

Because `voteRate = (double) userVote / totalVote` is a fraction less than 1 for every voter but one, and `reward += voteRate * totalReward` truncates on each addition, the sum of everything actually paid out to all voters of that SR for that cycle is, in general, strictly less than `totalReward`. Unlike the newer VI-based algorithm, which scales the numerator by `DECIMAL_OF_VI_REWARD = 10^18` before dividing to keep the loss at sub-unit (dust) level, this path performs no scaling and truncates raw TRX-denominated `long` amounts, so the lost fraction per voter can be non-trivial and is never accounted for anywhere: [3](#0-2) 

The `getReward(cycle, srAddress)` value is never decremented as voters withdraw and there is no mechanism to detect or redistribute the unclaimed remainder — the difference between `totalReward` and the sum of truncated per-voter payouts is permanently unpaid to anyone and unrecoverable, exactly mirroring the `reward % rewardsDuration` dust described in the MultiRewards report, and it recurs on every cycle where this path executes for any SR with more than one voter.

The same double-truncation pattern (share = `count * (total/sum)` cast to `long`) also exists in `MortgageService.payStandbyWitness()`: [4](#0-3) 
and in `IncentiveManager.reward()`: [5](#0-4) 
but in those two cases `totalPay`/`getWitnessStandbyAllowance()` represent a per-block emission cap rather than a previously escrowed balance, so any truncation there simply reduces total inflation for that block rather than stranding already-committed funds — a weaker analog than the SR reward-bucket case.

### Impact Explanation
Per-cycle, per-SR reward dust from truncated floating-point vote-share division becomes permanently unclaimable — no voter, the SR, or the protocol can ever recover it, matching the "Low" severity classification of the original report (individual amounts are small, bounded by `totalVote` precision).

### Likelihood Explanation
This code path executes for every SR/voter combination in every cycle prior to `newRewardAlgorithmEffectiveCycle` (i.e., any deployment/network that has not yet crossed that cycle, or historical accounting for cycles before the switch), so the loss recurs with high frequency, matching the "High" likelihood in the original report. It is a legacy path but remains present and reachable in the current codebase as `computeReward(long, List)` / `getOldReward` call site inside `computeReward(long, long, AccountCapsule)`.

### Recommendation
- For the legacy path, either retire it entirely (force all live networks past `newRewardAlgorithmEffectiveCycle`) or replace the double-based `voteRate * totalReward` computation with integer/BigInteger scaled arithmetic (as already done for the VI-based algorithm), and explicitly track/carry forward any truncation remainder within the same reward bucket so it is not permanently lost.
- Audit `MortgageService.payStandbyWitness()` and `IncentiveManager.reward()` for the same truncation pattern and, if the intent is to fully emit `totalPay`/`getWitnessStandbyAllowance()`, accumulate and redistribute (or carry over) the truncation remainder rather than silently dropping it.

### Proof of Concept
Given an SR with `totalVote = 3` split across three voters each with `userVote = 1`, and `totalReward = 10` (integer TRX units) for a given cycle:
- `voteRate = 1/3 = 0.3333...` for each voter.
- `reward += 0.3333... * 10 = 3.333...` truncated to `3` for each voter.
- Total actually paid out across the three voters = `3 + 3 + 3 = 9`, while `totalReward = 10`.
- `1` unit is permanently unaccounted for: `delegationStore.getReward(cycle, srAddress)` still logically represents `10`, but no code path ever pays out or reclaims the missing unit — it is stranded exactly like the `reward % rewardsDuration` remainder in the original MultiRewards report, and this repeats for every cycle/SR pair processed via `computeReward(long, List<Pair<byte[], Long>>)`. [6](#0-5)

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L35-53)
```java
  public void addReward(long cycle, byte[] address, long value) {
    byte[] key = buildRewardKey(cycle, address);
    BytesCapsule bytesCapsule = get(key);
    if (bytesCapsule == null) {
      put(key, new BytesCapsule(ByteArray.fromLong(value)));
    } else {
      put(key, new BytesCapsule(ByteArray
          .fromLong(ByteArray.toLong(bytesCapsule.getData()) + value)));
    }
  }

  public long getReward(long cycle, byte[] address) {
    BytesCapsule bytesCapsule = get(buildRewardKey(cycle, address));
    if (bytesCapsule == null) {
      return 0L;
    } else {
      return ByteArray.toLong(bytesCapsule.getData());
    }
  }
```

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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L171-188)
```java
  private long computeReward(long cycle, List<Pair<byte[], Long>> votes) {
    long reward = 0;
    for (Pair<byte[], Long> vote : votes) {
      byte[] srAddress = vote.getKey();
      long totalReward = delegationStore.getReward(cycle, srAddress);
      if (totalReward <= 0) {
        continue;
      }
      long totalVote = delegationStore.getWitnessVote(cycle, srAddress);
      if (totalVote == DelegationStore.REMARK || totalVote == 0) {
        continue;
      }
      long userVote = vote.getValue();
      double voteRate = (double) userVote / totalVote;
      reward += voteRate * totalReward;
    }
    return reward;
  }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L199-230)
```java
  private long computeReward(long beginCycle, long endCycle, AccountCapsule accountCapsule) {
    if (beginCycle >= endCycle) {
      return 0;
    }

    long reward = 0;
    long newAlgorithmCycle = dynamicPropertiesStore.getNewRewardAlgorithmEffectiveCycle();
    List<Pair<byte[], Long>> srAddresses = accountCapsule.getVotesList().stream()
        .map(vote -> new Pair<>(vote.getVoteAddress().toByteArray(), vote.getVoteCount()))
        .collect(Collectors.toList());
    if (beginCycle < newAlgorithmCycle) {
      long oldEndCycle = min(endCycle, newAlgorithmCycle,
          dynamicPropertiesStore.disableJavaLangMath());
      reward = getOldReward(beginCycle, oldEndCycle, srAddresses);
      beginCycle = oldEndCycle;
    }
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
