### Title
Block/transaction-fee rewards for a witness whose snapshotted vote count is zero become permanently unclaimable in the DPoS delegation reward accounting - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService.payReward` unconditionally credits a witness's per-cycle reward bucket in `DelegationStore` via `addReward` whenever a block or transaction-fee reward is paid, without checking whether the witness's snapshotted vote count for that cycle (`getWitnessVote`) is non-zero. The subsequent reward-claim logic (`computeReward` in both the old and new "Vi" reward algorithms) divides that bucket among voters proportionally to `userVote / totalVote`. If `totalVote` for that witness/cycle is `0`, the division/ratio path is skipped entirely, so the credited amount is never paid out to any account, and there is no rescue/reclaim function to recover or redistribute it. This mirrors the reported `Gauge`/`CLGauge`/`Bribes` pattern where `notifyRewardAmount` can credit rewards for a period whose "total supply" turns out to be zero, permanently stranding funds with no rescue mechanism.

### Finding Description
`Manager.payReward` (`framework/src/main/java/org/tron/core/db/Manager.java:1946-1985`) calls `mortgageService.payBlockReward(...)` and `mortgageService.payTransactionFeeReward(...)` for the block-producing witness every block, unconditionally: [1](#0-0) 

Both delegate to `payReward`, which credits the witness's reward for the *current* cycle in `DelegationStore` regardless of whether any votes are currently attributed to that witness for that cycle: [2](#0-1) 

`DelegationStore.addReward` simply accumulates the value into a per-cycle, per-witness counter with no relation to the recorded vote total: [3](#0-2) 

The witness's vote count for a cycle is only snapshotted at maintenance time, independent of the reward flow, via `MaintenanceManager.doMaintenance`: [4](#0-3) 

When a voter later tries to withdraw/query the reward, `MortgageService.computeReward` (old algorithm) explicitly skips any cycle/witness whose recorded `totalVote` is `0` (or `REMARK`), even though `totalReward` for that cycle/witness is greater than zero: [5](#0-4) 

The new "Vi" reward algorithm has the same structural gap: `DelegationStore.accumulateWitnessVi` (called every maintenance cycle from `MaintenanceManager.doMaintenance`) only increments the reward-per-vote accumulator (`Vi`) when `voteCount != 0`; if `voteCount == 0` for that cycle, the accumulated reward is simply dropped ("Just forward pre vi") even though `getReward(cycle, address)` for that witness is non-zero: [6](#0-5) 

In both algorithms, once a witness's cycle-level `totalVote`/`voteCount` is `0`, the reward amount credited via `addReward` for that (cycle, witness) pair becomes permanently unreachable: no voter's share calculation can ever attribute a nonzero fraction of it, and there is no admin/rescue function anywhere in `MortgageService` or `DelegationStore` to recover, redistribute, or burn it explicitly - it simply remains an orphaned counter in the `delegation` store forever, exactly analogous to the reported `Gauge`/`Bribes` bug where rewards notified for a zero-total-supply epoch become permanently stuck with no rescue path.

### Impact Explanation
Impact is an accounting/asset-corruption issue: TRX that is nominally earmarked as a witness's block/transaction-fee reward for a given cycle can become permanently non-distributable to any voter if that witness's snapshotted vote total for the cycle is zero. This does not directly enable a state-corruption exploit that harms other accounts' funds, but it does represent unclaimed protocol emissions with no accounted destination or reclaim path, which is the same underlying accounting flaw class as the reported issue (funds credited against a zero denominator with no rescue mechanism).

### Likelihood Explanation
Likelihood is low, mirroring the original report's "Low" likelihood rating. On a live mainnet, an actively block-producing (top-27) or standby (top-127) witness almost always has a nonzero recorded vote count, and `IncentiveManager.reward` / `MortgageService.payStandbyWitness` even explicitly guard against a fully zero `voteSum` before crediting standby rewards (`consensus/src/main/java/org/tron/consensus/dpos/IncentiveManager.java:31-33`, `chainbase/src/main/java/org/tron/core/service/MortgageService.java:57-59`). However, no equivalent guard exists for the per-witness `payBlockReward`/`payTransactionFeeReward` path, so the edge case (e.g., a witness whose votes are fully withdrawn mid-cycle but who remains active until the next maintenance re-election, or a newly created witness on a low-participation/private network) is plausible though rare.

### Recommendation
Add a check in `MortgageService.payReward` (and in the new-algorithm accumulation path in `DelegationStore.accumulateWitnessVi`/`RewardViCalService`) to detect when a witness's cycle vote total is zero at reward-credit time, and either (a) redirect/roll that reward into the next cycle's pool for the same witness so it isn't orphaned, or (b) provide an explicit governance-controlled rescue/reclaim function that can recover reward amounts that were credited against a zero-vote cycle and redirect them (e.g., back to the transaction fee pool or to protocol treasury) instead of leaving them permanently stranded in `DelegationStore`.

### Proof of Concept
1. A witness `W` is elected/active (top-27 or top-127) with vote count `V > 0` at the start of cycle `C`, recorded via `MaintenanceManager.doMaintenance` → `delegationStore.setWitnessVote(C+1, W, V)`.
2. Before the next maintenance cycle, all voters withdraw their votes from `W` such that `W`'s live `voteCount` becomes `0`; `W` still remains in the active/standby list until the next `doMaintenance()` call re-ranks witnesses.
3. During this window, `W` produces blocks, and `Manager.payReward` → `MortgageService.payBlockReward`/`payTransactionFeeReward` → `payReward` credits `delegationStore.addReward(C, W, value)` for nonzero `value`.
4. At the next maintenance, `delegationStore.setWitnessVote(C+1, W, 0)` is recorded for cycle `C` (or `accumulateWitnessVi` runs with `voteCount == 0`), and `getReward(C, W) > 0` but `getWitnessVote(C, W) == 0`.
5. Any voter calling `MortgageService.withdrawReward`/`queryReward` for cycle `C` against witness `W` hits `computeReward`'s `totalVote == 0` skip branch, receiving `0` for that portion, while `delegationStore.getReward(C, W)` remains permanently non-zero and unreachable, with no function anywhere in the codebase to reclaim it.

### Citations

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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L154-162)
```java
    if (dynamicPropertiesStore.allowChangeDelegation()) {
      long nextCycle = dynamicPropertiesStore.getCurrentCycleNumber() + 1;
      dynamicPropertiesStore.saveCurrentCycleNumber(nextCycle);
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.setBrokerage(nextCycle, witness.createDbKey(),
            delegationStore.getBrokerage(witness.createDbKey()));
        delegationStore.setWitnessVote(nextCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
```
