Confirmed: `VotesCapsule.addNewVotes` simply appends a new `Vote` entry to the `NewVotes` list every time it's called—it does not merge or deduplicate by witness address [1](#0-0) . `AccountCapsule.addVotes` (used by the same actuator) similarly appends per-call rather than merging by witness address. This exactly mirrors the Allora bug class: a list of user-supplied elements (`ForecastElements` / here `VotesList`) is consumed without collapsing duplicate keys before being persisted and used downstream for aggregation.

### Title
Duplicate witness entries in `VoteWitnessContract.VotesList` are not merged before persisting votes, causing vote-count/tally divergence - (File: `actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java`)

### Summary
`VoteWitnessActuator.countVoteAccount` iterates the raw `voteContract.getVotesList()` and, for every entry, calls `votesCapsule.addNewVotes(...)` and `accountCapsule.addVotes(...)` without deduplicating or merging votes that target the same witness address [2](#0-1) . In contrast, the newer TVM-native path, `VoteWitnessProcessor.execute`, explicitly merges duplicate witness votes into a `Map<ByteString, Long> voteMap` before writing to storage, with a code comment "merge vote for same witness" [3](#0-2) . The two code paths for the same underlying operation are inconsistent: the legacy actuator persists one `Vote` protobuf entry per input element (with duplicates), while the newer processor collapses duplicates into a single entry per witness.

### Finding Description
`VoteWitnessActuator.validate()` only checks that the total summed vote count (including duplicate entries for the same witness) does not exceed the voter's `tronPower`, and that each entry references a valid witness/account [4](#0-3) . There is no check that `voteContract.getVotesList()` entries have unique `voteAddress` values.

During execution, `countVoteAccount` then persists each vote entry as-is via `votesCapsule.addNewVotes` and `accountCapsule.addVotes`, both of which simply append a new `Vote` record rather than merging by address [2](#0-1) [1](#0-0) . This means the persisted `NewVotes`/`Votes` list can contain multiple entries pointing at the same witness address, splitting a voter's power across duplicate records instead of a single canonical entry.

Downstream, `MaintenanceManager.countVote` (via `Wallet.countVote`) iterates `votes.getNewVotes()`/`getOldVotes()` and sums vote counts per witness address into a map, so the total tallied vote count for a witness is technically still correct arithmetically (duplicates just get summed again) [5](#0-4) . However, other consumers of `AccountCapsule.getVotesList()` that assume one entry per witness (e.g., precompiled contract `VoteCount`, which sums matching entries in a loop) are not similarly protected by design and are only correct by coincidence of also summing over all matches [6](#0-5) . The core problem, mirroring the Allora bug class, is architectural: duplicate elements are never rejected or normalized at the point of input validation/persistence (`VoteWitnessActuator`), unlike the newer, hardened `VoteWitnessProcessor` path which explicitly merges duplicates.

### Impact Explanation
Because `VoteWitnessActuator`'s persisted `Votes`/`AccountCapsule` vote lists can contain duplicate witness entries, any state/tooling/API/precompile that assumes a `List<Vote>` has at most one entry per witness address will silently diverge from the actual intended semantics. This creates an accounting/state-representation inconsistency between the two vote-processing implementations (legacy actuator vs. TVM native contract) for functionally identical operations, and increases the size of stored vote lists without bound relative to `MAX_VOTE_NUMBER`, since duplicates each still count toward the `contract.getVotesCount() > maxVoteNumber` limit but represent redundant data [7](#0-6) .

### Likelihood Explanation
Any unprivileged account can submit a `VoteWitnessContract` transaction with a `VotesList` containing multiple entries for the same witness address, since there is no uniqueness validation in `VoteWitnessActuator.validate()` [4](#0-3) . This makes the trigger trivial and requires no special privilege.

### Recommendation
In `VoteWitnessActuator.countVoteAccount` (and correspondingly in `validate`), merge/deduplicate votes by `voteAddress` before persisting, analogous to the `voteMap` merging already implemented in `VoteWitnessProcessor.execute` [8](#0-7) .

### Proof of Concept
1. Construct a `VoteWitnessContract` with `VotesList = [{witnessA, 10}, {witnessA, 5}]` where the voter has `tronPower >= 15 * TRX_PRECISION`.
2. `validate()` passes because the sum (15) is checked against `tronPower`, and each entry references a valid witness [4](#0-3) .
3. `countVoteAccount` then calls `votesCapsule.addNewVotes(witnessA, 10)` and `votesCapsule.addNewVotes(witnessA, 5)` separately, resulting in two distinct `Vote` records for `witnessA` in the persisted `Votes.NewVotes` list, instead of one merged record of `15` [9](#0-8) [1](#0-0) .

Note: I was unable to fully verify how every downstream consumer (e.g. wallet display APIs, external indexers) handles a `Vote` list with duplicate witness addresses, since not all such consumers were located in the indexed codebase; the index may not include every relevant file. A Devin session with full repo access could confirm whether any consumer besides the tally logic assumes uniqueness and would produce incorrect results (rather than just redundant storage).

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/VotesCapsule.java (L81-85)
```java
  public void addNewVotes(ByteString voteAddress, long voteCount) {
    this.votes = this.votes.toBuilder()
        .addNewVotes(Vote.newBuilder().setVoteAddress(voteAddress).setVoteCount(voteCount).build())
        .build();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L93-97)
```java
    int maxVoteNumber = MAX_VOTE_NUMBER;
    if (contract.getVotesCount() > maxVoteNumber) {
      throw new ContractValidateException(
          "VoteNumber more than maxVoteNumber " + maxVoteNumber);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L98-121)
```java
    try {
      Iterator<Vote> iterator = contract.getVotesList().iterator();
      Long sum = 0L;
      while (iterator.hasNext()) {
        Vote vote = iterator.next();
        byte[] witnessCandidate = vote.getVoteAddress().toByteArray();
        if (!DecodeUtil.addressValid(witnessCandidate)) {
          throw new ContractValidateException("Invalid vote address!");
        }
        long voteCount = vote.getVoteCount();
        if (voteCount <= 0) {
          throw new ContractValidateException("vote count must be greater than 0");
        }
        String readableWitnessAddress = StringUtil.createReadableString(vote.getVoteAddress());
        if (!accountStore.has(witnessCandidate)) {
          throw new ContractValidateException(
              ACCOUNT_EXCEPTION_STR + readableWitnessAddress + NOT_EXIST_STR);
        }
        if (!witnessStore.has(witnessCandidate)) {
          throw new ContractValidateException(
              WITNESS_EXCEPTION_STR + readableWitnessAddress + NOT_EXIST_STR);
        }
        sum = LongMath.checkedAdd(sum, vote.getVoteCount());
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L181-190)
```java
    voteContract.getVotesList().forEach(vote -> {
      logger.debug("countVoteAccount, address[{}]",
          ByteArray.toHexString(vote.getVoteAddress().toByteArray()));

      votesCapsule.addNewVotes(vote.getVoteAddress(), vote.getVoteCount());
      accountCapsule.addVotes(vote.getVoteAddress(), vote.getVoteCount());
    });

    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
    votesStore.put(ownerAddress, votesCapsule);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L54-86)
```java
    Map<ByteString, Long> voteMap = new HashMap<>();
    Iterator<Protocol.Vote> iterator = param.getVotes().iterator();
    try {
      long sum = 0;
      while (iterator.hasNext()) {
        Protocol.Vote vote = iterator.next();

        byte[] witnessAddress = vote.getVoteAddress().toByteArray();
        /*
          Already covered while doing maintenance in MaintenanceManager.java, for tvm performance,
          we remove the account check
         */
//        if (repo.getAccount(witnessAddress) == null) {
//          throw new ContractValidateException(
//              ACCOUNT_EXCEPTION_STR + StringUtil.encode58Check(witnessAddress) + NOT_EXIST_STR);
//        }
        if (repo.getWitness(witnessAddress) == null) {
          throw new ContractExeException(
              WITNESS_EXCEPTION_STR + StringUtil.encode58Check(witnessAddress) + NOT_EXIST_STR);
        }

        long voteCount = vote.getVoteCount();
        if (voteCount < 0) {
          throw new ContractExeException("Vote count must not be less than 0");
        } else if (voteCount == 0) {
          iterator.remove();
        } else {
          sum = LongMath.checkedAdd(sum, voteCount);
          // merge vote for same witness
          voteMap.put(vote.getVoteAddress(),
              LongMath.checkedAdd(voteMap.getOrDefault(vote.getVoteAddress(), 0L), voteCount));
        }
      }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L864-869)
```java
      votes.getNewVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        countWitness.put(voteAddress,
            countWitness.getOrDefault(voteAddress, 0L) + voteCount);
      });
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1883-1891)
```java
      long voteCount = 0;
      if (accountCapsule != null && !accountCapsule.getVotesList().isEmpty()) {
        ByteString witness = ByteString.copyFrom(words[1].toTronAddress());
        for (Protocol.Vote vote : accountCapsule.getVotesList()) {
          if (witness.equals(vote.getVoteAddress())) {
            voteCount += vote.getVoteCount();
          }
        }
      }
```
