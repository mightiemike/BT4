### Title
Unbounded per-cycle reward loop in `MortgageService.getOldReward` can stall block processing when withdrawing rewards for long-dormant voter accounts - (File: chainbase/src/main/java/org/tron/core/service/MortgageService.java)

### Summary
`WithdrawBalanceActuator.execute()` calls `MortgageService.withdrawReward()`, which calls `computeReward(beginCycle, endCycle, accountCapsule)`. For the portion of the cycle range that precedes `newRewardAlgorithmEffectiveCycle`, this delegates to `getOldReward()`, which iterates cycle-by-cycle (`for (long cycle = begin; cycle < end; cycle++)`) and, for each cycle, loops over the account's full vote list doing DB reads via `delegationStore.getReward`/`getWitnessVote`. This mirrors the `LiquidityFarming.sol` `getUpdatedAccTokenPerShare()` pattern: a loop whose length is proportional to how long a user has been inactive/dormant, executed synchronously inside a state-transition triggered by an ordinary broadcast transaction.

### Finding Description [1](#0-0) 
`WithdrawBalanceActuator.execute()` unconditionally invokes `mortgageService.withdrawReward(...)` for any broadcast `WithdrawBalanceContract`. [2](#0-1) 
`withdrawReward()` calls `computeReward(beginCycle, endCycle, accountCapsule)` where `beginCycle` is the account's `DelegationStore.getBeginCycle(address)` — i.e., the last cycle the account's rewards were processed. If an account voted once and never withdrew/re-voted since, `beginCycle` can remain stuck at a very old cycle number while `endCycle` advances to `currentCycle` on every subsequent call. [3](#0-2) 
`computeReward(beginCycle, endCycle, accountCapsule)` splits the range: for `beginCycle < newRewardAlgorithmCycle` it calls `getOldReward(beginCycle, oldEndCycle, srAddresses)`; only for cycles at/after `newRewardAlgorithmEffectiveCycle` does it use the O(1) cumulative "Vi" delta technique. [4](#0-3) 
`getOldReward()` — when `allowOldRewardOpt()` is not enabled — runs `for (long cycle = begin; cycle < end; cycle++) { reward += computeReward(cycle, votes); }`, and each iteration's `computeReward(cycle, votes)` loops over every voted SR (up to the max allowed votes) performing two store lookups per vote (`getReward`, `getWitnessVote`).

Unlike the `LiquidityFarming.sol` bug, where `rewardRateLog` grows without bound forever, here the "old algorithm" window is capped by the fixed, historical `newRewardAlgorithmEffectiveCycle` — so the maximum loop length is bounded but can still be very large (thousands of cycles × up to dozens of votes × multiple DB reads each) for accounts that have been dormant since before that hard-fork cycle and only now call `withdrawReward`/`WithdrawBalanceContract`.

### Impact Explanation
Because native TRON actuators like `WithdrawBalanceContract` charge a flat bandwidth-based fee rather than metering execution cost the way TVM/EVM gas does, there is no cost cap proportional to the amount of work performed inside `getOldReward()`. A single ordinary broadcast transaction from a long-dormant account can force every full node to perform a large number of sequential DB reads/BigInteger computations while processing that one transaction during block validation, which can slow block processing (a DoS-adjacent availability concern) without the attacker paying proportional fees. This matches the reported bug class ("unbounded loop growth causing failure/expense out of proportion during withdraw") but manifests as processing-time cost to the network rather than reverted transactions.

### Likelihood Explanation
This requires: (1) an account that voted and accumulated a stale `beginCycle` from well before `newRewardAlgorithmEffectiveCycle` and never triggered `withdrawReward`/re-vote since, and (2) `allowOldRewardOpt()` not being enabled on the target network (mainnet has this behind a committee-controlled proposal switch, so the exact current mainnet state could not be confirmed from the code alone). Given the difficulty of guaranteeing indexer coverage of this configuration state, likelihood is assessed as Low-to-Medium: it depends on a network-level feature flag whose current value could not be conclusively verified from this analysis, and it needs a genuinely dormant, pre-hard-fork voter account, which naturally diminishes over time as more accounts get moved to the new algorithm's O(1) path (via any `withdrawReward` call).

### Recommendation
- Cap the maximum span processed by `getOldReward()` per call (e.g., process at most N cycles per `withdrawReward` invocation and advance `beginCycle` incrementally on subsequent calls), so no single transaction can force unbounded work.
- Alternatively/also, ensure `allowOldRewardOpt()` (which redirects to the O(1) `RewardViCalService.getNewRewardAlgorithmReward`) is enabled network-wide and that the legacy per-cycle loop path is fully deprecated, eliminating this code path from being reachable by new transactions.
- Add a fee model for native actuators whose runtime cost is proportional to on-chain iteration count, similar to gas metering for TVM operations, so the transaction fee scales with the amount of history being processed.

### Proof of Concept
1. Identify/construct an account that voted for SRs in a cycle well before `newRewardAlgorithmEffectiveCycle` and never called `WithdrawBalanceContract` or re-voted since (so `DelegationStore.getBeginCycle(address)` remains at that old cycle).
2. Ensure `allowOldRewardOpt()` is not enabled (or simulate a network configuration where it isn't).
3. Broadcast a `WithdrawBalanceContract` transaction for that address, invoking `WithdrawBalanceActuator.execute()` → `MortgageService.withdrawReward()` → `computeReward()` → `getOldReward()`.
4. Observe that `getOldReward()` executes a `for` loop over the full historical cycle range up to `newRewardAlgorithmEffectiveCycle`, with an inner loop over the account's voted SR list and two `DelegationStore` reads per vote per cycle — all within the single transaction's execution, for a flat fee unrelated to the number of iterations performed.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L54-55)
```java
    mortgageService.withdrawReward(withdrawBalanceContract.getOwnerAddress()
        .toByteArray());
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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L260-269)
```java
  private long getOldReward(long begin, long end, List<Pair<byte[], Long>> votes) {
    if (dynamicPropertiesStore.allowOldRewardOpt()) {
      return rewardViCalService.getNewRewardAlgorithmReward(begin, end, votes);
    }
    long reward = 0;
    for (long cycle = begin; cycle < end; cycle++) {
      reward += computeReward(cycle, votes);
    }
    return reward;
  }
```
