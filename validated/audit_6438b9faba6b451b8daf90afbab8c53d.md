### Title
Missing duplicate/zero-address validation of `Vote` list in `VoteWitnessActuator` - (File: actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java)

### Summary
`VoteWitnessActuator.validate()`, the validator for the `VoteWitnessContract` reachable by any broadcast transaction, checks each `Vote.getVoteAddress()` for validity, existence as an account/witness, and a positive vote count, and enforces that the summed vote count does not exceed the voter's TRON Power. However, it never checks that the list of vote addresses is a unique set, unlike the sibling `AccountPermissionUpdateActuator::checkPermission()`, which explicitly deduplicates key addresses with `.distinct()` and rejects the transaction if duplicates are found. [1](#0-0) [2](#0-1) 

### Finding Description
The `VoteWitnessContract.votes` field is a repeated `Vote{address, count}` list. `VoteWitnessActuator.validate()` iterates the list and only validates each vote in isolation (address validity, positive count, account/witness existence) and sums vote counts to compare against total TRON Power — it performs no uniqueness check across `vote.getVoteAddress()` entries. [1](#0-0) 

In `execute()`, `countVoteAccount()` then iterates the raw, unmerged `voteContract.getVotesList()` and calls `votesCapsule.addNewVotes(vote.getVoteAddress(), vote.getVoteCount())` and `accountCapsule.addVotes(vote.getVoteAddress(), vote.getVoteCount())` once per list entry, without any deduplication/merging step. [3](#0-2) 

This is directly contrasted by the TVM-native equivalent, `VoteWitnessProcessor` (used for the `voteWitness` native precompiled contract call), which explicitly builds a `Map<ByteString, Long> voteMap` and merges vote entries for the same witness address before writing them to state, with the comment `// merge vote for same witness`. [4](#0-3) 

The two code paths (regular `VoteWitnessContract` transaction actuator vs. TVM native `voteWitness` call) that both mutate the same account/witness vote state therefore behave inconsistently when a caller supplies duplicate vote addresses: one silently merges them, the other stores them as-is inside a repeated protobuf field on `AccountCapsule`/`VotesCapsule`. This mirrors the reported bug class exactly ("array is not validated to be a unique set"), just in a different array (`Vote.address` list) than the report's `topHolders`.

Because I could not retrieve the exact bodies of `AccountCapsule.addVotes()`/`VotesCapsule.addNewVotes()` (index limits — these files were not returned in full by search), I cannot fully confirm whether these setters merge by address internally or simply append/overwrite each call. This uncertainty should be resolved by reading those two files directly.

### Impact Explanation
If `addVotes`/`addNewVotes` append raw entries rather than merging by address (which is the behavior the TVM path explicitly works around), a `VoteWitnessContract` with duplicate witness addresses could persist duplicate `Vote` entries in the account's votes list and/or the `VotesCapsule` new-votes list. Depending on how witness vote tallying at maintenance time (`Manager`/`WitnessController` vote counting) consumes this list — e.g. if it iterates and sums per-entry rather than per-unique-address — this could cause vote-count accounting drift between the account-level view and witness-level aggregated vote totals, or inconsistent behavior compared to votes cast via the TVM native contract path for the same effective stake. This falls in scope as a resource/reward (voting) accounting-corruption concern, reachable from any broadcast `VoteWitnessContract` transaction with no privileged access required.

### Likelihood Explanation
Likelihood of triggering the divergent code path is high: constructing a `VoteWitnessContract` with the same `vote_address` repeated multiple times (with `vote_count` values summing to at most the voter's TRON Power) requires no special permissions and can be submitted by any account via ordinary transaction broadcast. The actual severity of the downstream consequence depends on unverified internals of `AccountCapsule.addVotes`/`VotesCapsule.addNewVotes`, which is why this is flagged as an analog needing confirmation rather than a proven high-severity bug.

### Recommendation
Add the same uniqueness validation pattern used in `AccountPermissionUpdateActuator.checkPermission()` to `VoteWitnessActuator.validate()`: deduplicate `contract.getVotesList()` by `vote_address` (e.g., via `.stream().map(Vote::getVoteAddress).distinct().collect(...)` compared to list size) and reject the transaction with a `ContractValidateException` if duplicates are found, mirroring the merge behavior already implemented in `VoteWitnessProcessor` for the TVM path so both code paths behave consistently. [5](#0-4) 

### Proof of Concept
1. Craft a `VoteWitnessContract` for `ownerAddress` with `votes = [{vote_address: WITNESS_A, vote_count: 100}, {vote_address: WITNESS_A, vote_count: 100}]`, where `ownerAddress` has TRON Power ≥ 200.
2. Broadcast the transaction. `VoteWitnessActuator.validate()` passes because `sum(200) <= tronPower`, and no duplicate check exists. [1](#0-0) 
3. In `execute()`, `countVoteAccount()` calls `accountCapsule.addVotes(WITNESS_A, 100)` and `votesCapsule.addNewVotes(WITNESS_A, 100)` twice for the same address rather than once with the merged value 200. [6](#0-5) 
4. Compare this against submitting the equivalent vote through the TVM native `voteWitness` call, where `VoteWitnessProcessor` merges the two entries into a single `{WITNESS_A: 200}` map entry before persisting. [7](#0-6) 
5. Inspect the resulting `AccountCapsule`/`VotesCapsule` state (via `AccountCapsule.addVotes`/`VotesCapsule.addNewVotes`, not fully retrievable from the index) to confirm whether duplicate entries are stored distinctly and whether downstream witness vote-tally logic double counts or otherwise diverges from the TVM path's merged result.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L61-97)
```java
  @Override
  public boolean validate() throws ContractValidateException {
    if (this.any == null) {
      throw new ContractValidateException(ActuatorConstant.CONTRACT_NOT_EXIST);
    }
    if (chainBaseManager == null) {
      throw new ContractValidateException(ActuatorConstant.STORE_NOT_EXIST);
    }
    AccountStore accountStore = chainBaseManager.getAccountStore();
    WitnessStore witnessStore = chainBaseManager.getWitnessStore();
    if (!this.any.is(VoteWitnessContract.class)) {
      throw new ContractValidateException(
          "contract type error, expected type [VoteWitnessContract], real type[" + any
              .getClass() + "]");
    }
    final VoteWitnessContract contract;
    try {
      contract = this.any.unpack(VoteWitnessContract.class);
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }
    if (!DecodeUtil.addressValid(contract.getOwnerAddress().toByteArray())) {
      throw new ContractValidateException("Invalid address");
    }
    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);

    if (contract.getVotesCount() == 0) {
      throw new ContractValidateException(
          "VoteNumber must more than 0");
    }
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

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L95-104)
```java
    long weightSum = 0;
    List<ByteString> addressList = permission.getKeysList()
        .stream()
        .map(x -> x.getAddress())
        .distinct()
        .collect(toList());
    if (addressList.size() != permission.getKeysList().size()) {
      throw new ContractValidateException(
          "address should be distinct in permission " + permission.getType());
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L54-110)
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

      long tronPower;
      if (repo.getDynamicPropertiesStore().supportUnfreezeDelay()
          && repo.getDynamicPropertiesStore().supportAllowNewResourceModel()) {
        tronPower = accountCapsule.getAllTronPower();
      } else {
        tronPower = accountCapsule.getTronPower();
      }
      sum =  LongMath.checkedMultiply(sum, TRX_PRECISION);
      if (sum > tronPower) {
        throw new ContractExeException(
            "The total number of votes[" + sum + "] is greater than the tronPower[" + tronPower
                + "]");
      }
    } catch (ArithmeticException e) {
      throw new ContractExeException(e.getMessage());
    }

    for (Map.Entry<ByteString, Long> entry : voteMap.entrySet()) {
      accountCapsule.addVotes(entry.getKey(), entry.getValue());
      votesCapsule.addNewVotes(entry.getKey(), entry.getValue());
    }
    repo.updateAccount(accountCapsule.createDbKey(), accountCapsule);
    repo.updateVotes(ownerAddress, votesCapsule);
```
