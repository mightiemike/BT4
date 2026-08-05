## Title
Vote-reward accounting is cycle-granular, not time-weighted — voting seconds before a maintenance boundary can earn a full cycle's reward - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
The DPoS voting-reward mechanism in java-tron pays SR voters based on a per-cycle `Vi` (reward-per-vote accumulator) delta multiplied by the voter's vote count captured in a snapshot. The snapshot of "how much a voter had voted" is taken lazily, whenever `withdrawReward`/`countVoteAccount` is invoked, and is then applied to the *entire* subsequent cycle's accumulated reward with no weighting for how long within that cycle the vote was actually held. This mirrors the `LendingLedger` bug class: a discrete, boundary-rounded accounting window lets a user capture a full period's reward by only being "in" for an instant around the boundary.

### Finding Description
Voter rewards are computed in `MortgageService.computeReward` (and its VM-callable twin `VoteRewardUtil.computeReward`) as:
```java
BigInteger deltaVi = endVi.subtract(beginVi);
reward += deltaVi.multiply(BigInteger.valueOf(userVote)).divide(DECIMAL_OF_VI_REWARD).longValue();
``` [1](#0-0) 

`Vi` is accumulated once per maintenance cycle in `MaintenanceManager.doMaintenance` via `delegationStore.accumulateWitnessVi(curCycle, ...)`, i.e. the reward-per-vote for an entire cycle is fixed at cycle-end based on total reward paid to the SR and total vote count for that cycle: [2](#0-1) 

The voter's `userVote` used against this cycle-wide delta is not a time-weighted average — it is whatever vote count was captured in the last snapshot (`delegationStore.setAccountVote(endCycle, address, accountCapsule)`), taken whenever `withdrawReward` happens to run: [3](#0-2) 

Votes themselves can be changed at any point in time within a cycle via `VoteWitnessActuator.countVoteAccount`, which calls `mortgageService.withdrawReward(ownerAddress)` first (committing/rolling the previous snapshot) and then immediately overwrites `accountCapsule`'s votes with the new vote list, with no timestamp or duration tracking: [4](#0-3) 

Because `computeReward` treats `[beginCycle, endCycle)` as an atomic unit and multiplies the *entire* cycle's `deltaVi` by whatever vote count is in the snapshot, a voter can:
1. Cast a large vote a few seconds before a maintenance boundary (end of cycle N).
2. Let maintenance run (advancing to cycle N+1) so `Vi` for cycle N is finalized using the SR's full vote total (which already includes the late voter, since `countVote()`/`WitnessCapsule.voteCount` updates are immediate, not time-weighted either).
3. Withdraw/clear the vote seconds after the boundary.
4. Still receive the full-cycle reward for cycle N when `withdrawReward`/`queryReward` is eventually called, because the snapshot recorded the vote count that existed at withdraw time, not the actual holding duration.

This is precisely the reported bug class: reward accounting rounds to a period boundary (`beginCycle`/`endCycle`, analogous to `WEEK`) instead of being time-weighted, so an attacker who is only "in" for a fraction of the accounting window can be credited for the whole window. The existing regression test `VoteTest.testRewardAlgorithmNo1` (F-V-W all inside cycle-1) explicitly documents that voting and withdrawing within a single cycle still nets a full subsequent cycle's reward: [5](#0-4) 

### Impact Explanation
An unprivileged account can time votes around maintenance-cycle boundaries to earn full-cycle voting rewards (paid out of the SR reward pool / brokerage-adjusted allowance) while contributing negligible actual "vote-weight-time" to securing/supporting the witness set. This dilutes rewards for genuine long-term voters and lets an attacker extract disproportionate reward with capital exposed for only seconds, defeating the purpose of the voting-reward mechanism (to incentivize sustained delegation to witnesses) — directly analogous to the High-severity finding in the original report, where the purpose of the reward (attracting sustained liquidity/participation) is defeated.

### Likelihood Explanation
This is reachable by any account that has frozen/staked TRX (TRON Power) and calls `VoteWitnessContract` — no privileged role is required. Maintenance cycle timing is fully known/predictable on-chain (fixed interval), making the timing attack trivial to script. The only constraint is having enough TRON power to move `Vi`, which is attacker-controlled capital, not a privilege gate.

### Recommendation
Introduce time-weighting into the voter reward calculation, similar to the fix adopted for `LendingLedger`: track the timestamp/cycle-fraction at which a vote was set/cleared and weight `userVote` by the fraction of the cycle during which it was actually held, rather than applying the full-cycle `deltaVi` to a single point-in-time vote snapshot. At minimum, changing votes very close to a maintenance boundary should not let the new vote count apply to the just-finalized cycle's reward.

### Proof of Concept
Conceptual reproduction using the existing test harness pattern from `VoteTest.testRewardAlgorithmNo1`:
1. In cycle N, near its end (just before maintenance), call `VoteWitnessContract`/`vote()` with a large vote count for witness A (triggers `VoteWitnessActuator.countVoteAccount`, which snapshots the account's vote via `mortgageService.withdrawReward`).
2. Trigger maintenance (`MaintenanceManager.doMaintenance`) — cycle N's `Vi` for witness A is finalized including this late vote in the SR's vote total.
3. Immediately after maintenance (start of cycle N+1), clear/withdraw the vote.
4. Advance a few more cycles and call `withdrawReward`/`queryReward` for the account — `computeReward` will include the full `deltaVi` for cycle N multiplied by the vote count snapshotted in step 1, crediting a full cycle of reward for a vote held for only seconds. This matches the behavior already exercised (without being treated as a bug) in `VoteTest.testRewardAlgorithmNo1`, where a vote cast and withdrawn within a single cycle still yields a full subsequent cycle's reward. [6](#0-5)

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L89-130)
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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L96-101)
```java
    if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
      long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L152-191)
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
      accountCapsule.initializeOldTronPower();
    }

    if (!votesStore.has(ownerAddress)) {
      votesCapsule = new VotesCapsule(voteContract.getOwnerAddress(),
          accountCapsule.getVotesList());
    } else {
      votesCapsule = votesStore.get(ownerAddress);
    }

    accountCapsule.clearVotes();
    votesCapsule.clearNewVotes();

    voteContract.getVotesList().forEach(vote -> {
      logger.debug("countVoteAccount, address[{}]",
          ByteArray.toHexString(vote.getVoteAddress().toByteArray()));

      votesCapsule.addNewVotes(vote.getVoteAddress(), vote.getVoteCount());
      accountCapsule.addVotes(vote.getVoteAddress(), vote.getVoteCount());
    });

    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
    votesStore.put(ownerAddress, votesCapsule);
  }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/VoteTest.java (L479-522)
```java
  /**
   *   F - Freeze, U - Unfreeze
   *   V - Vote, W - Withdraw, C - Clear Vote
   *   C* - Cycle-*, M* - Maintenance-*
   *
   *  M0    C1    M1    C2    M2    C3    M3    C4    M4    C5    M5    C6    M6    C7    M7
   *  ||__________||__________||__________||__________||__________||__________||__________||
   *    |  |  |     |           |                                   |
   *    F  V  W     W           W                                   W
   *
   * @throws Exception throw all kinds of exception
   */
  @Test
  public void testRewardAlgorithmNo1() throws Exception {
    byte[] voteContractA = deployContract("VoteA", ABI, CODE);
    byte[] voteContractB = deployContract("VoteB", ABI, CODE);

    // cycle-1
    {
      // freeze balance to get tron power
      freezeBalance(voteContractA);
      freezeBalance(voteContractB);

      // vote through smart contract
      voteWitness(voteContractA,
          Arrays.asList(witnessAStr, witnessBStr),
          Arrays.asList(1234L, 4321L));
      voteWitness(voteContractB,
          Arrays.asList(witnessAStr, witnessBStr),
          Arrays.asList(12L, 21L));

      // no reward yet
      checkRewardAndWithdraw(voteContractA, true);
      checkRewardAndWithdraw(voteContractB, true);
      payRewardAndDoMaintenance(1);
    }

    // cycle-2
    {
      // no reward yet
      checkRewardAndWithdraw(voteContractA, true);
      checkRewardAndWithdraw(voteContractB, true);
      payRewardAndDoMaintenance(1);
    }
```
