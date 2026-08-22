### Title
DoS via Unbounded `VotesStore` Scan in Publicly-Exposed `getPaginatedNowWitnessList` RPC/HTTP API - (File: `framework/src/main/java/org/tron/core/Wallet.java`)

### Summary
`Wallet.getPaginatedNowWitnessList()` and its private helper `countVote(VotesStore)` iterate over the **entire** `VotesStore` — i.e., every voter that has cast a vote during the current maintenance epoch — on every single invocation of this unauthenticated, externally reachable API. This mirrors the `Revolver._checkSolePlayer()` bug class: an externally triggerable code path whose cost scales linearly with the total number of network participants (voters) rather than being bounded/paginated, creating an unbounded compute/DoS surface as voter counts grow.

### Finding Description
`getPaginatedNowWitnessList(offset, limit)` is designed to be a *paginated* API (it accepts `offset`/`limit` and clamps `limit` to `WITNESS_COUNT_LIMIT_MAX`), but before any pagination/limiting is applied, it must first compute up-to-date vote deltas by calling `countVote(votesStore)`: [1](#0-0) 

`countVote` walks the whole `VotesStore` iterator — one entry per voting account for the current epoch — and for each entry iterates that account's `getOldVotes()`/`getNewVotes()` lists to build an in-memory `Map<ByteString, Long>` of vote deltas: [2](#0-1) 

This is functionally identical in shape to `_checkSolePlayer()` in the external report: a full scan over "all active players" (all currently-voting accounts) triggered on every call to a supposedly bounded/paginated entry point, rather than the pagination applying to the underlying scan itself. The same `countVote` pattern (full `VotesStore` iteration every epoch) also underlies `MaintenanceManager.doMaintenance()`: [3](#0-2) 

but `doMaintenance()` runs once per maintenance cycle as part of consensus, so its cost, while O(voters), is amortized and not attacker-repeatable at will. In contrast, `getPaginatedNowWitnessList` is exposed via gRPC/HTTP and can be called by any anonymous client repeatedly, at any time within an epoch, re-executing the full O(total voters in epoch) scan on every call — this is the closer functional analog of the `randomNumberCallback` → `_checkSolePlayer()` pattern (an externally reachable entry point paying for an unbounded, participant-count-scaled scan).

### Impact Explanation
As the number of unique voting accounts in an epoch grows (this scales with overall network adoption, not with any per-caller limit), each call to this API becomes progressively more expensive in CPU and memory (building a `HashMap` sized to all voters, and iterating all `oldVotes`/`newVotes` lists). Because the endpoint is unauthenticated and can be invoked without rate limiting from any client, an attacker can repeatedly hit it during high-voter epochs to consume node CPU/memory disproportionately to the cost of the request, degrading node responsiveness for legitimate RPC/HTTP/gRPC consumers (a "DoS via RPC-API" class issue analogous to the reported OOG/stuck-state bug, though here the failure mode is service degradation/resource exhaustion of the serving node rather than an on-chain stuck state, since this is a read-only query path, not part of block/transaction execution consensus).

### Likelihood Explanation
Likelihood is moderate: the endpoint is unauthenticated and always reachable, but the severity of impact depends on the size of `VotesStore` for the current epoch, which is time-bounded (cleared every maintenance cycle) and requires substantial real voter activity to become large enough to cause meaningful resource exhaustion. On networks/epochs with many thousands of active voters, repeated calls could meaningfully burden a node; on chains with modest voter counts the effect is negligible.

### Recommendation
1. Cache the `countVote` result per maintenance epoch (invalidate on `doMaintenance()` or when `VotesStore` changes) instead of recomputing the full scan on every API call.
2. Avoid scanning the entire `VotesStore` for a paginated read: maintain an incrementally-updated vote-delta index (updated at vote-cast time) so `getPaginatedNowWitnessList` can read from a bounded structure.
3. Apply request-level rate limiting / cost accounting to this and similar read APIs whose backing computation scales with total account/voter count.
4. Consider capping the maximum number of `VotesStore` entries processed per call, returning a "retry"/"in progress" response similar to the existing maintenance-period guard already present in this method.

### Proof of Concept
Not applicable as concrete exploit code — this is a resource-exhaustion analog surfaced by code review, not a directly reproducible transaction-level exploit like the original Solidity report. To demonstrate impact, a background agent with node access could: (1) spin up a testnet, (2) have a large number (e.g., 50k+) of unique accounts each cast a `VoteWitnessContract` in the same epoch, (3) repeatedly call `getPaginatedNowWitnessList` via gRPC/HTTP and measure latency/CPU growth relative to voter count, confirming the O(n) full-store-scan cost per call.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L792-822)
```java
    // It contains the final vote count at the end of the last epoch.
    List<WitnessCapsule> witnessCapsuleList = chainBaseManager.getWitnessStore().getAllWitnesses();
    if (offset >= witnessCapsuleList.size()) {
      return null;
    }

    VotesStore votesStore = chainBaseManager.getVotesStore();
    // Count the vote changes for each witness in the current epoch, it is maybe negative.
    Map<ByteString, Long> countWitness = countVote(votesStore);

    // Iterate through the witness list to apply vote changes and calculate the real-time vote count
    witnessCapsuleList.forEach(witnessCapsule -> {
      long voteCount = countWitness.getOrDefault(witnessCapsule.getAddress(), 0L);
      witnessCapsule.setVoteCount(witnessCapsule.getVoteCount() + voteCount);
    });

    // Use the same sorting logic as in the Maintenance period
    WitnessStore.sortWitnesses(witnessCapsuleList,
        chainBaseManager.getDynamicPropertiesStore().allowWitnessSortOptimization());

    List<WitnessCapsule> sortedWitnessList = witnessCapsuleList.stream()
        .skip(offset)
        .limit(limit)
        .collect(Collectors.toList());

    WitnessList.Builder builder = WitnessList.newBuilder();
    sortedWitnessList.forEach(witnessCapsule ->
        builder.addWitnesses(witnessCapsule.getInstance()));

    return builder.build();
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L842-872)
```java
  private Map<ByteString, Long> countVote(VotesStore votesStore) {
    // Initialize a result map to store vote changes for each witness
    Map<ByteString, Long> countWitness = Maps.newHashMap();

    // VotesStore is a key-value store, where the key is the address of the voter
    Iterator<Entry<byte[], VotesCapsule>> dbIterator = votesStore.iterator();

    while (dbIterator.hasNext()) {
      Entry<byte[], VotesCapsule> next = dbIterator.next();
      VotesCapsule votes = next.getValue();

      /**
       * VotesCapsule contains two lists:
       * - Old votes: Last votes from the previous epoch, updated in maintenance period
       * - New votes: Latest votes in current epoch, updated after each vote transaction
       */
      votes.getOldVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        countWitness.put(voteAddress,
            countWitness.getOrDefault(voteAddress, 0L) - voteCount);
      });
      votes.getNewVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        countWitness.put(voteAddress,
            countWitness.getOrDefault(voteAddress, 0L) + voteCount);
      });
    }
    return countWitness;
  }
```

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
