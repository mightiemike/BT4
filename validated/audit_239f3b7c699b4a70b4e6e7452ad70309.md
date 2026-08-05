### Title
DPoS voting reward (Vi) mechanism lets a voter front-run the maintenance-cycle reward recalculation to capture a full cycle's reward with only momentary exposure - ([File: chainbase/src/main/java/org/tron/core/service/MortgageService.java])

### Summary
The witness-vote reward system computes a per-cycle "value index" (`Vi`), analogous to the Vault's `exchangeRate`, only once per maintenance cycle. A user can cast (or change) a vote right before the maintenance boundary and have their `beginCycle` set to the *current* cycle, so that on the next reward computation they are credited the entire current cycle's `Vi` delta for a vote that was only held for a moment before the recalculation, exactly mirroring the "deposit right before rebalance, cash the pre-existing profit" pattern from the referenced report.

### Finding Description
Reward accounting is driven by `MortgageService.withdrawReward()` [1](#0-0) , which is invoked on every vote-changing action (`VoteWitnessActuator.countVoteAccount` calls `mortgageService.withdrawReward(ownerAddress)` *before* the new votes are applied) [2](#0-1) .

When this is called during cycle `N` (before that cycle's maintenance has executed), it sets:
```
endCycle = currentCycle;              // = N
...
delegationStore.setBeginCycle(address, endCycle);   // beginCycle = N
delegationStore.setEndCycle(address, endCycle + 1);
delegationStore.setAccountVote(endCycle, address, accountCapsule); // snapshot of OLD votes
``` [3](#0-2) 

The per-witness reward index `Vi` is only advanced once per maintenance cycle, in `MaintenanceManager.doMaintenance()`:
```
if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
  long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
  consensusDelegate.getAllWitnesses().forEach(witness ->
      delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount()));
}
...
witnessCapsule.setVoteCount(witnessCapsule.getVoteCount() + voteCount); // votes cast THIS cycle added AFTER Vi(curCycle) is computed
...
delegationStore.setWitnessVote(nextCycle, witness.createDbKey(), witness.getVoteCount()); // divisor for Vi(nextCycle)
``` [4](#0-3) [5](#0-4) [6](#0-5) 

`accumulateWitnessVi` computes `deltaVi = reward * DECIMAL / voteCount` using the vote total that existed at the *start* of cycle `N` (i.e. NOT including votes cast during cycle `N`, since those are only folded into `witness.getVoteCount()` later in the same `doMaintenance()` call and only become the divisor for `Vi(N+1)` via `setWitnessVote(nextCycle, ...)`) [7](#0-6) .

Reward is subsequently paid via `computeReward(beginCycle, endCycle, accountCapsule)`, which sums `deltaVi * userVote` for every cycle in `[beginCycle, endCycle)` using the account's **live** vote list [8](#0-7) .

Putting this together: if a user casts a vote for witness `W` late in cycle `N` (after `W`'s block-production performance for cycle `N` is already known/observable, since block scheduling in DPoS is deterministic and rewards accrue in real time), `beginCycle` becomes `N`. `W`'s new vote is not counted in the divisor used to compute `Vi(N)` (so it does not dilute other voters' share of cycle `N`), yet on the next reward pull the user's window `[N, endCycle)` includes the `Vi(N) − Vi(N−1)` delta multiplied by their **full** vote weight — crediting them the whole cycle `N` reward-per-vote for a vote that was economically exposed for only a fraction of that cycle. This is functionally identical to depositing into the Vault right before `exchangeRate` is recalculated upward and immediately capturing the already-realized profit: the numerator (cycle-N eligibility window) is granted retroactively while the corresponding risk/holding period was never actually taken on.

### Impact Explanation
This lets any account extract disproportionate reward from the shared witness reward pool with negligible duration of exposure, at the expense of long-term voters whose share of `Vi` is diluted once the attacker's stake enters the divisor in the following cycle while the attacker still collects a full cycle's reward retroactively. Because the attacker only needs available TRX Power (already-frozen or otherwise) and a single `VoteWitnessContract`/`voteWitness` TVM call timed near a maintenance boundary, this is a repeatable, unprivileged accounting-drain vector on the live reward/allowance balances tracked in `AccountCapsule.allowance`.

### Likelihood Explanation
Maintenance cycle timing (`getNextMaintenanceTime`) and witness block-production performance are both public/deterministic, so the "right before rebalance" timing condition from the original report is trivially satisfiable on java-tron — an attacker merely needs to submit a vote transaction near the end of a maintenance cycle. No privileged role or race beyond ordinary block-inclusion timing is required, making this readily exploitable by any unprivileged account holding TronPower.

### Recommendation
Set `beginCycle` to `currentCycle + 1` (not `currentCycle`) whenever `withdrawReward`/vote-change occurs before the current cycle's maintenance has run, so a newly cast vote only starts accruing `Vi` reward from the cycle following the one in which it was cast — consistent with the fact that the vote is excluded from that cycle's `Vi` divisor. Alternatively, snapshot and lock in `Vi(N)` eligibility strictly to accounts whose vote was already reflected in the divisor used to compute it, mirroring the report's recommendation to tie the accounting to the *next* period's rate rather than the currently-forming one.

### Proof of Concept
1. Monitor upcoming maintenance time (`ChainParameters`/`getNextMaintenanceTime`) and observe witness `W`'s accrued block/vote reward for the current cycle `N` (publicly queryable via `delegationStore.getReward(N, W)` equivalents / `queryReward`).
2. Just before the maintenance boundary, submit `VoteWitnessContract` (or TVM `voteWitness`) voting available TronPower for `W`. This triggers `VoteWitnessActuator.countVoteAccount` → `mortgageService.withdrawReward(owner)`, which sets `beginCycle = N` for the attacker [3](#0-2) .
3. Maintenance runs: `accumulateWitnessVi(N, W, oldVoteCount)` finalizes `Vi(N)` without the attacker's new vote in the divisor [9](#0-8) ; `witness.getVoteCount()` is then updated to include the attacker's vote for future cycles [10](#0-9) .
4. In cycle `N+1`, call `withdrawReward` (or any vote-changing action). `computeReward(beginCycle=N, endCycle=N+1, accountCapsule)` credits `deltaVi(N) * attackerVoteCount` into `allowance`, granting a full cycle's reward for a vote held only momentarily before the boundary [8](#0-7) .
5. Attacker withdraws the balance via `WithdrawBalanceContract` and unfreezes/unvotes, having captured cycle `N`'s reward at no real cost.

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L89-97)
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
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L118-130)
```java
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
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L199-228)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L152-167)
```java
  private void countVoteAccount(VoteWitnessContract voteContract) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    VotesStore votesStore = chainBaseManager.getVotesStore();
    MortgageService mortgageService = chainBaseManager.getMortgageService();
    byte[] ownerAddress = voteContract.getOwnerAddress().toByteArray();

    VotesCapsule votesCapsule;

    //
    mortgageService.withdrawReward(ownerAddress);

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);

    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    if (dynamicStore.supportAllowNewResourceModel()
        && accountCapsule.oldTronPowerIsNotInitialized()) {
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L96-101)
```java
    if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
      long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L111-126)
```java
      countWitness.forEach((address, voteCount) -> {
        byte[] witnessAddress = address.toByteArray();
        WitnessCapsule witnessCapsule = consensusDelegate.getWitness(witnessAddress);
        if (witnessCapsule == null) {
          logger.warn("Witness capsule is null. address is {}", Hex.toHexString(witnessAddress));
          return;
        }
        AccountCapsule account = consensusDelegate.getAccount(witnessAddress);
        if (account == null) {
          logger.warn("Witness account is null. address is {}", Hex.toHexString(witnessAddress));
          return;
        }
        witnessCapsule.setVoteCount(witnessCapsule.getVoteCount() + voteCount);
        consensusDelegate.saveWitness(witnessCapsule);
        logger.info("address is {} , countVote is {}", witnessCapsule.createReadableString(),
            witnessCapsule.getVoteCount());
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
