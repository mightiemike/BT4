### Title
Unbounded Growth of `WitnessStore` via Fee-Only `WitnessCreateContract` Enables DoS of Consensus-Critical Iteration Paths - (File: `actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java`)

### Summary
This is the same bug class as the external Compound report: an unbounded, fully-iterated list (`allMarkets`) that any unprivileged actor can grow without limit, which is then iterated in critical code paths, risking out-of-gas/DoS. In java-tron, the analogous unbounded, globally-iterated list is `WitnessStore` (all super representative candidates). `WitnessCreateActuator.validate()` places no cap on the total number of witnesses that can ever be created — the only gate is a balance check (`accountCapsule.getBalance() < dynamicStore.getAccountUpgradeCost()`), a purely economic (not privileged/role-based) barrier reachable by any account via a normal broadcast transaction.

### Finding Description
`WitnessCreateActuator.validate()` [1](#0-0)  only checks:
- address validity,
- URL validity,
- that the address doesn't already have a witness (`witnessStore.has(ownerAddress)`),
- that the balance covers `dynamicStore.getAccountUpgradeCost()`.

There is no check on the total number of entries in `WitnessStore` (no `maxWitnessNum`-style cap), unlike `VoteWitnessActuator` which explicitly enforces `MAX_VOTE_NUMBER` on the number of votes per transaction [2](#0-1) . Any account holding enough TRX (a normal account resource, not an admin/SR/witness role) can create a new `WitnessCapsule` and have it persisted forever in `WitnessStore` via `createWitness()` [3](#0-2) .

`WitnessStore.getAllWitnesses()` performs a full store scan and materializes every witness into memory [4](#0-3) . This unbounded list is iterated, without any limit, in multiple consensus-critical/RPC-reachable paths:

1. `MaintenanceManager.doMaintenance()` — executed synchronously on every block that crosses a maintenance boundary (i.e., in the normal block-application path for every full node), iterates `consensusDelegate.getAllWitnesses()` up to three separate times (for `accumulateWitnessVi`, for building `newWitnessAddressList`, and for `setBrokerage`/`setWitnessVote`) [5](#0-4) .
2. `WitnessStore.getWitnessStandby()` sorts the *entire* witness list before trimming to `WITNESS_STANDBY_LENGTH` [6](#0-5) .
3. `Wallet.getWitnessList()` — an anonymous RPC (`listWitnesses`) that returns the *entire* unbounded witness list with no pagination or limit [7](#0-6) , exposed via `RpcApiService.listWitnesses` [8](#0-7) .
4. `Wallet.getPaginatedNowWitnessList()` — although output is capped by `WITNESS_COUNT_LIMIT_MAX`, the full witness list is still fetched and fully sorted (`WitnessStore.sortWitnesses`, O(n log n)) before slicing, and `countVote()` fully iterates `VotesStore` on every call [9](#0-8) .

Unlike the active witness set (bounded by `MAX_ACTIVE_WITNESS_NUM`) or the vote count per transaction (bounded by `MAX_VOTE_NUMBER`), the *candidate* witness store size itself is never capped — mirroring exactly the Compound `allMarkets`/`maxAssets` gap: a related, deliberately-bounded value exists elsewhere in the system (active witnesses, vote count) while the underlying list that feeds those computations is left unbounded.

### Impact Explanation
If an attacker (or coordinated set of accounts) creates a very large number of witnesses — each requiring only `dynamicStore.getAccountUpgradeCost()` TRX, a fixed, non-slashed, one-time cost with no scarcity mechanism preventing reuse of funds across many accounts over time — the following consensus and RPC paths degrade:
- `doMaintenance()` runs inline in `applyBlock()` for every full node in the network at every maintenance cycle; if this computation exceeds the fixed block interval, all full nodes fall behind block production/validation at the same wall-clock point, risking network-wide stalls, forks, or missed maintenance windows — a DoS impacting consensus availability, not just a single node.
- The unpaginated `listWitnesses` RPC allows any anonymous gRPC client to force a full node to enumerate and serialize the entire witness store on every call, enabling a cheap RPC-based DoS/resource-exhaustion vector once the store is large.
- `getPaginatedNowWitnessList` still performs full-list retrieval, vote aggregation, and full sort per call even though it claims to paginate, compounding the RPC DoS surface.

### Likelihood Explanation
Reachable via ordinary `WitnessCreateContract` broadcast transactions from any account with sufficient balance — no special role, key leak, or malicious peer/node behavior needed, satisfying "unprivileged" reachability. The economic cost (`getAccountUpgradeCost`) is a governance-tunable value but currently fixed and not designed to bound the *count* of witnesses network-wide; it only limits speed of accumulation, not the eventual ceiling, since balance can be reused after burn/transfer cycles across many colluding or Sybil accounts over time. Given java-tron mainnet has operated for years without this becoming acute (similar to Compound's own historical-performance argument), the likelihood of practical exploitation depends on actual economic cost vs. attacker budget and current witness count, which cannot be fully verified from the indexed code alone.

### Recommendation
Add an explicit, enforced upper bound on the number of entries permitted in `WitnessStore` (a `maxWitnessNum`-style dynamic parameter, analogous to `MAX_VOTE_NUMBER`/`MAX_ACTIVE_WITNESS_NUM`), checked in `WitnessCreateActuator.validate()` before allowing a new `WitnessCapsule` to be created. Additionally:
- Enforce a mandatory `limit`/pagination bound on `listWitnesses`/`getWitnessList` RPCs instead of returning the unbounded full store.
- Avoid repeated full-store iteration/sorting inside `doMaintenance()` and `getPaginatedNowWitnessList()`; consider caching sorted results or restricting operations to witnesses with non-zero vote counts before sorting.
- Perform gas/time-cost simulations (as recommended in the source report) to determine the safe maximum candidate witness count under current maintenance/block-interval constraints, and align `getAccountUpgradeCost` economics with that ceiling.

### Proof of Concept
1. Repeatedly fund distinct accounts with at least `dynamicStore.getAccountUpgradeCost()` TRX each.
2. For each account, broadcast a `WitnessCreateContract` transaction; `WitnessCreateActuator.validate()`/`execute()` will accept it unconditionally (no store-size check) [10](#0-9) .
3. Repeat until `WitnessStore` contains a very large number of entries (e.g., tens of thousands).
4. Observe: (a) anonymous `listWitnesses` gRPC calls become slow/large-payload [8](#0-7) ; (b) `doMaintenance()` execution time at the next maintenance boundary grows with witness count across every full node in the network [5](#0-4) .

Note: The exact economic cost of `getAccountUpgradeCost` at genesis and any governance history of this value were not fully retrievable from the indexed code, so the precise attacker budget required to reach a DoS-triggering witness count could not be confirmed; a Devin session with full repo/config access would be needed to pin down that parameter and run concrete timing simulations.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L53-149)
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
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    WitnessStore witnessStore = chainBaseManager.getWitnessStore();
    if (!this.any.is(WitnessCreateContract.class)) {
      throw new ContractValidateException(
          "contract type error, expected type [WitnessCreateContract],real type[" + any
              .getClass() + "]");
    }
    final WitnessCreateContract contract;
    try {
      contract = this.any.unpack(WitnessCreateContract.class);
    } catch (InvalidProtocolBufferException e) {
      throw new ContractValidateException(e.getMessage());
    }

    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }

    if (!TransactionUtil.validUrl(contract.getUrl().toByteArray())) {
      throw new ContractValidateException("Invalid url");
    }

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);

    if (accountCapsule == null) {
      throw new ContractValidateException("account[" + readableOwnerAddress
          + ActuatorConstant.NOT_EXIST_STR);
    }
    /* todo later
    if (ArrayUtils.isEmpty(accountCapsule.getAccountName().toByteArray())) {
      throw new ContractValidateException("accountStore name not set");
    } */

    if (witnessStore.has(ownerAddress)) {
      throw new ContractValidateException(
          WITNESS_EXCEPTION_STR + readableOwnerAddress + "] has existed");
    }

    if (accountCapsule.getBalance() < dynamicStore
        .getAccountUpgradeCost()) {
      throw new ContractValidateException("balance < AccountUpgradeCost");
    }

    return true;
  }

  @Override
  public ByteString getOwnerAddress() throws InvalidProtocolBufferException {
    return any.unpack(WitnessCreateContract.class).getOwnerAddress();
  }

  @Override
  public long calcFee() {
    return chainBaseManager.getDynamicPropertiesStore().getAccountUpgradeCost();
  }

  private void createWitness(final WitnessCreateContract witnessCreateContract)
      throws BalanceInsufficientException {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    WitnessStore witnessStore = chainBaseManager.getWitnessStore();
    //Create Witness by witnessCreateContract
    final WitnessCapsule witnessCapsule = new WitnessCapsule(
        witnessCreateContract.getOwnerAddress(),
        0,
        witnessCreateContract.getUrl().toStringUtf8());

    logger.debug("createWitness,address[{}]", witnessCapsule.createReadableString());
    witnessStore.put(witnessCapsule.createDbKey(), witnessCapsule);
    AccountCapsule accountCapsule = accountStore
        .get(witnessCapsule.createDbKey());
    accountCapsule.setIsWitness(true);
    if (dynamicStore.getAllowMultiSign() == 1) {
      accountCapsule.setDefaultWitnessPermission(dynamicStore);
    }
    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
    long cost = dynamicStore.getAccountUpgradeCost();
    adjustBalance(accountStore, witnessCreateContract.getOwnerAddress().toByteArray(), -cost);
    if (dynamicStore.supportBlackHoleOptimization()) {
      dynamicStore.burnTrx(cost);
    } else {
      adjustBalance(accountStore, accountStore.getBlackhole(), +cost);
    }
    dynamicStore.addTotalCreateWitnessCost(cost);
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

**File:** chainbase/src/main/java/org/tron/core/store/WitnessStore.java (L29-36)
```java
  /**
   * get all witnesses.
   */
  public List<WitnessCapsule> getAllWitnesses() {
    return Streams.stream(iterator())
        .map(Entry::getValue)
        .collect(Collectors.toList());
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/WitnessStore.java (L44-56)
```java
  public List<WitnessCapsule> getWitnessStandby(boolean isSortOpt) {
    List<WitnessCapsule> ret;
    List<WitnessCapsule> all = getAllWitnesses();
    sortWitnesses(all, isSortOpt);
    if (all.size() > Parameter.ChainConstant.WITNESS_STANDBY_LENGTH) {
      ret = new ArrayList<>(all.subList(0, Parameter.ChainConstant.WITNESS_STANDBY_LENGTH));
    } else {
      ret = new ArrayList<>(all);
    }
    // trim voteCount = 0
    ret.removeIf(w -> w.getVoteCount() < 1);
    return ret;
  }
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L89-163)
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
      });

      dposService.updateWitness(newWitnessAddressList);

      incentiveManager.reward(newWitnessAddressList);

      List<ByteString> newWits = consensusDelegate.getActiveWitnesses();
      if (!CollectionUtils.isEqualCollection(currentWits, newWits)) {
        currentWits.forEach(address -> {
          WitnessCapsule witnessCapsule = consensusDelegate.getWitness(address.toByteArray());
          witnessCapsule.setIsJobs(false);
          consensusDelegate.saveWitness(witnessCapsule);
        });
        newWits.forEach(address -> {
          WitnessCapsule witnessCapsule = consensusDelegate.getWitness(address.toByteArray());
          witnessCapsule.setIsJobs(true);
          consensusDelegate.saveWitness(witnessCapsule);
        });

        SRMetrics.recordSrSetChange(currentWits, newWits);
      }

      logger.info("Update witness success. \nbefore: {} \nafter: {}",
          getAddressStringList(currentWits),
          getAddressStringList(newWits));
    }

    if (dynamicPropertiesStore.allowChangeDelegation()) {
      long nextCycle = dynamicPropertiesStore.getCurrentCycleNumber() + 1;
      dynamicPropertiesStore.saveCurrentCycleNumber(nextCycle);
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.setBrokerage(nextCycle, witness.createDbKey(),
            delegationStore.getBrokerage(witness.createDbKey()));
        delegationStore.setWitnessVote(nextCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L762-768)
```java
  public WitnessList getWitnessList() {
    WitnessList.Builder builder = WitnessList.newBuilder();
    List<WitnessCapsule> witnessCapsuleList = chainBaseManager.getWitnessStore().getAllWitnesses();
    witnessCapsuleList
        .forEach(witnessCapsule -> builder.addWitnesses(witnessCapsule.getInstance()));
    return builder.build();
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L770-822)
```java
  public WitnessList getPaginatedNowWitnessList(long offset, long limit) throws
      MaintenanceUnavailableException {
    if (limit <= 0 || offset < 0) {
      return null;
    }
    if (limit > WITNESS_COUNT_LIMIT_MAX) {
      limit = WITNESS_COUNT_LIMIT_MAX;
    }

    /*
      In the maintenance period, the VoteStores will be cleared.
      To avoid the race condition of VoteStores deleted but Witness vote counts not updated,
      return retry error.
      Only apply to requests that rely on the latest block,
      which means the normal fullnode requests with HEAD cursor.
    */
    boolean isMaintenance = chainBaseManager.getDynamicPropertiesStore().getStateFlag() == 1;
    if (isMaintenance && !Args.getInstance().isSolidityNode() && getCursor() == Cursor.HEAD) {
      String message =
          "Service temporarily unavailable during maintenance period. Please try again later.";
      throw new MaintenanceUnavailableException(message);
    }
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

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L1870-1874)
```java
    public void listWitnesses(EmptyMessage request,
        StreamObserver<WitnessList> responseObserver) {
      responseObserver.onNext(wallet.getWitnessList());
      responseObserver.onCompleted();
    }
```
