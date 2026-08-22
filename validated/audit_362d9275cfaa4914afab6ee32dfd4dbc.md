### Title
Unbounded `VotesStore` growth causes full store-iteration DoS in witness vote counting - ([File: consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java])

### Summary
`MaintenanceManager.doMaintenance()` and `Wallet.getPaginatedNowWitnessList()` both call a `countVote(votesStore)` helper that performs a full, unbatched iteration over every entry in `VotesStore` on every invocation. `VotesStore` contains one entry per distinct account that has ever cast a `VoteWitnessContract`, and any unprivileged account can add an entry cheaply by freezing a minimal amount of TRX (as little as 1 TRX = 1 vote) and voting. This is the same bug class as the reported admin-refund issue: a state-processing loop that is bounded only by an attacker-inflatable array/store, causing resource exhaustion for the function that must process it in one pass.

### Finding Description
`countVote()` walks the entire `VotesStore` with a raw DB iterator and, for every entry, iterates the `oldVotes`/`newVotes` lists inside each `VotesCapsule`: [1](#0-0) 

This method is invoked unconditionally at every maintenance cycle from `doMaintenance()`: [2](#0-1) 

An equivalent, differently-cleared copy of the same full-scan logic is also invoked directly from the read-only `Wallet.getPaginatedNowWitnessList()` API, which is reachable by any unauthenticated HTTP/gRPC client with no throttling on how many times it can be called: [3](#0-2) [4](#0-3) 

Every entry added to `VotesStore` comes from an ordinary, unprivileged `VoteWitnessContract` transaction executed by `VoteWitnessActuator.countVoteAccount()`, which writes one `VotesCapsule` per distinct voter address into `VotesStore` with no cap on the total number of distinct voters: [5](#0-4) 

Because voting requires only a small amount of frozen TRX Power (as low as 1 TRX yields 1 vote), an attacker can create many low-cost accounts, freeze a minimal balance in each, and submit a vote transaction from each account. Each such account produces a brand-new key in `VotesStore` that persists until the next maintenance cycle clears it — but by then the damage (a maintenance-cycle iteration over an arbitrarily large key space) has already occurred, and the same unbounded key space is also scanned synchronously on every `getPaginatedNowWitnessList` RPC/HTTP call before that cycle completes.

### Impact Explanation
This mirrors the reported bug class exactly: a loop that must fully traverse a state collection whose size is controlled by cheap, unprivileged user action.
- If `VotesStore` grows large enough, `doMaintenance()` — a mandatory per-cycle state transition executed by every full node/witness — takes proportionally longer or can be pushed toward resource exhaustion, risking missed block production / consensus stalls network-wide, not just for one admin.
- `Wallet.getPaginatedNowWitnessList()` performs the identical unbounded full-store scan synchronously inside a JSON-RPC/gRPC/HTTP request handler with no batching or limit on `VotesStore` size, making it a direct RPC-triggerable amplification/DoS vector: a single external client request cost scales with the total number of ever-distinct voters network-wide.

### Likelihood Explanation
Likelihood is high: creating many accounts and freezing a minimal stake to vote is a standard, cheap, permissionless operation (no special privileges, no reliance on leaked keys or malicious peers). The attack only requires enough TRX to pay ordinary account/freeze/vote transaction fees for many low-value accounts, which is inexpensive relative to the disruption caused to full-store iteration paths used by every node and by public API consumers.

### Recommendation
- Cap/batch the vote-counting scan: process `VotesStore` in bounded chunks per maintenance cycle (or across multiple cycles) instead of a single unbounded iteration.
- Add an economic or count-based limit on the number of distinct new voter entries considered per epoch, or aggregate multiple identical votes prior to storage to bound growth.
- For `Wallet.getPaginatedNowWitnessList()`, avoid re-computing the full `countVote()` scan per external request; cache/memoize the aggregated vote deltas for the current epoch and invalidate only when new votes arrive, or move the computation off the synchronous RPC/HTTP request path.

### Proof of Concept
1. Programmatically create N accounts (e.g., tens of thousands), each funded with a minimal TRX balance.
2. From each account, freeze the minimum stake (1 TRX) and broadcast a `VoteWitnessContract` transaction voting for any active witness — this is a normal, unprivileged transaction accepted by `VoteWitnessActuator.execute()`/`countVoteAccount()`.
3. Each such transaction creates a new key in `VotesStore` (one per distinct voter address) that is never deduplicated or capped.
4. Observe that: (a) at the next maintenance cycle, `MaintenanceManager.doMaintenance() → countVote(votesStore)` must iterate over all N new entries in a single pass; and (b) any repeated call to `getPaginatedNowWitnessList` via gRPC/HTTP before the next maintenance cycle re-executes the identical O(N) full-store scan synchronously inside the request handler, degrading service for all API consumers proportional to N.

### Citations

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L89-104)
```java
  public void doMaintenance() {
    VotesStore votesStore = consensusDelegate.getVotesStore();

    tryRemoveThePowerOfTheGr();

    DynamicPropertiesStore dynamicPropertiesStore = consensusDelegate.getDynamicPropertiesStore();
    DelegationStore delegationStore = consensusDelegate.getDelegationStore();
    if (dynamicPropertiesStore.useNewRewardAlgorithm()) {
      long curCycle = dynamicPropertiesStore.getCurrentCycleNumber();
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.accumulateWitnessVi(curCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }

    Map<ByteString, Long> countWitness = countVote(votesStore);
    if (!countWitness.isEmpty()) {
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L165-192)
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
```

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
