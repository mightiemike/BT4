### Title
Proportional vote re-scaling in `UnfreezeBalanceV2Actuator.updateVote` under-allocates the account's real TRON Power due to per-entry rounding down - (File: `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java`)

### Summary
When an account unfreezes part of its stake via `UnfreezeBalanceV2Contract`, `UnfreezeBalanceV2Actuator.updateVote()` (and its TVM twin `UnfreezeBalanceV2Processor.updateVote()`) proportionally shrinks every existing SR vote entry to fit the account's reduced TRON Power. Each entry is individually scaled with `vote.getVoteCount() / totalVote * ownedTronPower`, truncated to a `long`. Because the divisions are done independently per vote-entry rather than once against a running remainder, the sum of the newly written votes can be strictly less than the account's actual available TRON Power, exactly the "weights[i] * total / totalWeight" rounding-down pattern described in the referenced report.

### Finding Description
`updateVote` computes, for every existing vote of an account whose frozen balance just decreased: [1](#0-0) 

```java
List<Vote> addVotes = new ArrayList<>();
for (Vote vote : accountCapsule.getVotesList()) {
  long newVoteCount = (long)
      ((double) vote.getVoteCount() / totalVote * ownedTronPower / TRX_PRECISION);
  if (newVoteCount > 0) {
    ...
    addVotes.add(newVote);
  }
}
```

This is mathematically identical to Alchemix's `_poolWeight = (_weights[i] * totalPower) / _totalVoteWeight` loop: each entry's share of a shrunk total (`ownedTronPower`) is computed with an independent division/truncation, and the code never verifies that `sum(newVoteCount) == ownedTronPower / TRX_PRECISION`. Any remainder created by truncating each entry separately (rather than tracking it and assigning the leftover to the last/one entry) is silently discarded — the account permanently loses that fraction of its usable TRON Power for the vote it just cast, instead of it being redistributed to the account's SR choices.

The identical logic (and the identical bug) exists in the native-contract path used by TVM `freezeBalanceV2`/`unfreezeBalanceV2` precompiles: [2](#0-1) 

Both call sites are reachable directly from `execute()`, which runs on every `UnfreezeBalanceV2Contract` transaction sent by an ordinary account: [3](#0-2) 

The resulting `Vote` list feeds directly into the DPoS witness-vote tally performed at every maintenance cycle: [4](#0-3) 

so any rounding loss here becomes a permanent, systemic under-count of real voting power feeding Super Representative elections and, through `MortgageService.computeReward`, into `queryReward`/`withdrawReward` reward accounting.

### Impact Explanation
The bug causes a genuine state/accounting divergence: after a partial unfreeze that shrinks `ownedTronPower` below the total previously-cast vote count, the account's re-scaled votes sum to strictly less than the TRON Power it actually still owns. That "lost" power is neither retained by the account, redistributed to its other chosen witnesses, nor accounted anywhere else — it simply vanishes from the consensus tally. Because the deficit is a function only of the number and ratio of a user's vote entries (users who split votes across many SRs lose proportionally more than users who vote for a single SR with the same total power, mirroring the exact unfairness the original report calls out), it produces skewed Super Representative election results and correspondingly skewed vote-based reward accrual via `DelegationStore`/`MortgageService`, without giving anyone else the "missing" votes. This is an accounting/consensus-input divergence bug reachable by any unprivileged token holder through a routine `UnfreezeBalanceV2` transaction — it does not require any privileged role.

### Likelihood Explanation
High likelihood of triggering: any account that (a) has cast votes for multiple witnesses and (b) later partially unfreezes stake such that `ownedTronPower < totalVote * TRX_PRECISION` will hit this code path automatically, with no special conditions or attacker cooperation required. The more SRs a voter has split votes across, the larger the cumulative rounding loss, so the effect is deterministic and reproducible on ordinary user activity (freeze → vote for several SRs → partially unfreeze).

### Recommendation
Do not truncate each vote entry's share independently. Instead, either:
- Track the running remainder/allocated sum across the loop and assign the last entry `ownedTronPower/TRX_PRECISION - sumOfPreviouslyAssigned` (subtraction instead of division for the final entry), or
- Use a single pass that accumulates the truncation error and carries it forward (largest-remainder method) so the sum of `newVoteCount` values always equals `ownedTronPower / TRX_PRECISION` exactly.

Apply the fix in both `UnfreezeBalanceV2Actuator.updateVote` and the equivalent `UnfreezeBalanceV2Processor.updateVote` in the TVM native-contract path to keep both code paths consistent.

### Proof of Concept
1. Create an account, freeze balance for `TRON_POWER`, and cast votes across three witnesses with counts e.g. `10`, `50`, `75` (`totalVote = 135`) — reachable via the standard `VoteWitness` flow.
2. Partially unfreeze stake such that the account's `ownedTronPower` (after `/TRX_PRECISION`) becomes `500` (i.e., less than `totalVote`), triggering `UnfreezeBalanceV2Actuator.updateVote`.
3. Compute expected re-scaled votes:
   - `10/135*500 = 37`
   - `50/135*500 = 185`
   - `75/135*500 = 277`
   - Sum = `499` instead of `500`.
4. Assert (as in the original PoC) that the sum of the account's new `Vote` entries stored via `votesStore`/`accountCapsule.getVotesList()` is strictly less than `ownedTronPower / TRX_PRECISION`, demonstrating the permanently lost TRON Power that never enters the SR vote tally in `MaintenanceManager.countVote`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L88-89)
```java
    this.updateTotalResourceWeight(accountCapsule, unfreezeBalanceV2Contract, unfreezeBalance);
    this.updateVote(accountCapsule, unfreezeBalanceV2Contract, ownerAddress);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L369-381)
```java
    // Update Owner Voting
    List<Vote> addVotes = new ArrayList<>();
    for (Vote vote : accountCapsule.getVotesList()) {
      long newVoteCount = (long)
          ((double) vote.getVoteCount() / totalVote * ownedTronPower / TRX_PRECISION);
      if (newVoteCount > 0) {
        Vote newVote = Vote.newBuilder()
            .setVoteAddress(vote.getVoteAddress())
            .setVoteCount(newVoteCount)
            .build();
        addVotes.add(newVote);
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L266-278)
```java
    // Update Owner Voting
    List<Protocol.Vote> votesToAdd = new ArrayList<>();
    for (Protocol.Vote vote : accountCapsule.getVotesList()) {
      long newVoteCount =
          (long) ((double) vote.getVoteCount() / totalVote * ownedTronPower / TRX_PRECISION);
      if (newVoteCount > 0) {
        votesToAdd.add(
            Protocol.Vote.newBuilder()
                .setVoteAddress(vote.getVoteAddress())
                .setVoteCount(newVoteCount)
                .build());
      }
    }
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L103-126)
```java
    Map<ByteString, Long> countWitness = countVote(votesStore);
    if (!countWitness.isEmpty()) {
      List<ByteString> currentWits = consensusDelegate.getActiveWitnesses();

      List<ByteString> newWitnessAddressList = new ArrayList<>();
      consensusDelegate.getAllWitnesses()
          .forEach(witnessCapsule -> newWitnessAddressList.add(witnessCapsule.getAddress()));

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
