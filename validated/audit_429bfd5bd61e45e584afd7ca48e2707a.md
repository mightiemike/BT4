## Analog Vulnerability Found

### Title
Unbounded iteration over `VotesStore` in DPoS maintenance can cause consensus-critical processing delays / DoS - ([File: consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java])

### Summary
The reported issue describes an unbounded loop over an attacker-growable array (`challengers.addresses`) inside `_calculateChallengerEligibility`, which is triggered as part of normal contract logic and can consume unbounded gas, risking an out-of-gas revert. The closest analog in java-tron is `MaintenanceManager.countVote`, which iterates over the *entire* `VotesStore` every maintenance cycle. Because `VotesStore` accumulates one entry per unique voting account and is never bounded, an attacker can grow this store arbitrarily cheaply, causing the per-cycle iteration cost to grow without limit. Unlike the Solidity case, this loop is not gas-metered by a paying caller — it executes deterministically inside block processing on every full node.

### Finding Description
`MaintenanceManager.doMaintenance()` invokes `countVote(votesStore)`, which iterates every entry in `VotesStore` via a `DBIterator`, and for each entry iterates the account's `oldVotes` and `newVotes` lists, then deletes the entry: [1](#0-0) 

Every account that casts a `VoteWitnessContract` transaction creates/updates one `VotesCapsule` entry keyed by its own address in `VotesStore`, and this entry persists until the next maintenance cycle consumes and deletes it: [2](#0-1) 

There is no cap on the number of distinct voter accounts (and thus `VotesStore` entries) that can exist at maintenance time — the only bound (`MAX_VOTE_NUMBER`) limits the number of *witnesses* a single vote transaction can target, not the number of *voting accounts*: [3](#0-2) 

`doMaintenance()` is invoked synchronously from `applyBlock`, which is called by `DposService.applyBlock` on every node for every block once the maintenance time threshold is reached: [4](#0-3) 

This mirrors the reported bug class exactly: an unbounded, user-growable collection (`challengers.addresses` vs. `VotesStore` entries) is iterated in full inside a critical accounting/state-transition function (`_calculateChallengerEligibility` vs. `countVote`), and the cost of the operation scales linearly with attacker-controlled input with no upper bound enforced anywhere in the call path.

### Impact Explanation
Unlike EVM gas metering (which reverts a single transaction on OOG), `countVote`/`doMaintenance` runs as part of deterministic full-node block/state processing outside of any transaction-level gas budget. If the `VotesStore` becomes very large (many distinct voting accounts), maintenance processing time increases proportionally, which occurs on every node at the same synchronized point (`nextMaintenanceTime`) inside block application. This can:
- Slow down or stall block production/validation across the network at maintenance boundaries (invalid-state/halt-adjacent risk), since `applyBlock` is on the hot path for every block once the maintenance flag is set.
- Cause divergence between nodes with different hardware/performance profiles processing the same maintenance-triggering block within the expected slot time, risking missed slots or consensus disruption.

This is an "unbounded public work" issue in a chain-critical, unprivileged-user-reachable code path (any account can vote and inflate `VotesStore`), analogous to the underpriced/unbounded-work concern in the original finding.

### Likelihood Explanation
Casting a vote only requires an account with nonzero `TronPower` (frozen balance) — even a very small freeze amount suffices, since `sum` just needs to be ≤ `tronPower` and vote count must be > 0. An attacker can therefore create a large number of low-cost accounts, freeze minimal TRX in each, and cast one vote from each account before a maintenance cycle, cheaply inflating `VotesStore`. This is comparable in cost/effort to the original report's acknowledgment that "triggering an out-of-gas error would be costly ... the attacker would need to create many accounts" — the same attacker profile applies here, but the java-tron impact is a deterministic, unmetered, per-block-validator cost rather than a single reverting transaction, making the "medium difficulty, real-but-costly" classification transferable.

### Recommendation
- Bound the number of live `VotesStore` entries considered per maintenance cycle (e.g., pagination/rate-limiting of the number of new voter accounts processed per epoch), or amortize the reward/vote reconciliation logic to avoid a single unbounded full-store scan in `MaintenanceManager.countVote`.
- Consider tracking aggregate vote deltas incrementally at vote-cast time (in `VoteWitnessProcessor.execute`) rather than deferring the full recomputation to a single unbounded loop in `doMaintenance`, similar to the report's long-term recommendation of computing pro-rata shares at entry time instead of at settlement time.
- Add an upper bound / cost analysis on `VotesStore` size growth per maintenance interval and monitor/alert if the store size trends toward levels that make `doMaintenance` processing time material relative to the block interval.

### Proof of Concept
1. Attacker creates `N` accounts, each funded with the minimal TRX needed to freeze a nonzero amount of bandwidth/energy (to obtain nonzero `TronPower`).
2. Each account submits a `VoteWitnessContract` transaction with 1 vote for any valid witness, which is accepted per `VoteWitnessProcessor.execute` validation (only checks `sum <= tronPower`, no cap on number of distinct voting accounts) — [5](#0-4) .
3. Each such vote creates a persistent entry in `VotesStore` keyed by the voter's address.
4. Repeating this across a large `N` (e.g., hundreds of thousands of accounts, scriptable and inexpensive at TRX's per-account minimum freeze) causes `VotesStore` to grow to size `N`.
5. At the next `nextMaintenanceTime`, every full node's `applyBlock` → `doMaintenance` → `countVote` call iterates all `N` entries synchronously as part of block application — [6](#0-5) , increasing processing time for that block on every node in proportion to `N`, with no cap to prevent this scaling.

### Citations

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L165-195)
```java
  private Map<ByteString, Long> countVote(VotesStore votesStore) {
    final Map<ByteString, Long> countWitness = Maps.newHashMap();
    Iterator<Entry<byte[], VotesCapsule>> dbIterator = votesStore.iterator();
    long sizeCount = 0;
    while (dbIterator.hasNext()) {
      Entry<byte[], VotesCapsule> next = dbIterator.next();
      VotesCapsule votes = next.getValue();
      votes.getOldVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        if (countWitness.containsKey(voteAddress)) {
          countWitness.put(voteAddress, countWitness.get(voteAddress) - voteCount);
        } else {
          countWitness.put(voteAddress, -voteCount);
        }
      });
      votes.getNewVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        if (countWitness.containsKey(voteAddress)) {
          countWitness.put(voteAddress, countWitness.get(voteAddress) + voteCount);
        } else {
          countWitness.put(voteAddress, voteCount);
        }
      });
      sizeCount++;
      votesStore.delete(next.getKey());
    }
    logger.info("There is {} new votes in this epoch", sizeCount);
    return countWitness;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L28-37)
```java
  public void validate(VoteWitnessParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    if (param.getVotes().size() > MAX_VOTE_NUMBER) {
      throw new ContractValidateException(
          "VoteNumber more than maxVoteNumber " + MAX_VOTE_NUMBER);
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L39-60)
```java
  public void execute(VoteWitnessParam param, Repository repo) throws ContractExeException {
    byte[] ownerAddress = param.getVoterAddress();
    VoteRewardUtil.withdrawReward(ownerAddress, repo);

    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);

    VotesCapsule votesCapsule = repo.getVotes(ownerAddress);
    if (votesCapsule == null) {
      votesCapsule = new VotesCapsule(ByteString.copyFrom(ownerAddress),
          accountCapsule.getVotesList());
    }

    accountCapsule.clearVotes();
    votesCapsule.clearNewVotes();

    Map<ByteString, Long> voteMap = new HashMap<>();
    Iterator<Protocol.Vote> iterator = param.getVotes().iterator();
    try {
      long sum = 0;
      while (iterator.hasNext()) {
        Protocol.Vote vote = iterator.next();

```

**File:** consensus/src/main/java/org/tron/consensus/dpos/DposService.java (L151-157)
```java
  @Override
  public boolean applyBlock(BlockCapsule blockCapsule) {
    statisticManager.applyBlock(blockCapsule);
    maintenanceManager.applyBlock(blockCapsule);
    updateSolidBlock();
    return true;
  }
```
