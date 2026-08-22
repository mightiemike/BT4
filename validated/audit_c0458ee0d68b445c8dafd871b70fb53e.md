### Title
Unbounded Witness (Validator) Registration Enables O(n) State Growth and Consensus-Path DoS - (File: `actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java`)

### Summary
`WitnessCreateActuator` allows any funded account to register as a witness (java-tron's validator role) by broadcasting a `WitnessCreateContract` transaction. The only gate is a fixed balance check (`balance >= AccountUpgradeCost`), configurable and typically a flat cost — there is no protocol-enforced cap on the total number of witnesses that can exist in `WitnessStore`. Every witness added permanently grows tables that are iterated with linear-time (or worse) algorithms in consensus-critical and RPC-serving code paths, mirroring the "unrestricted validator registration" bug class described in the report.

### Finding Description
`WitnessCreateActuator.validate()` only checks address validity, URL validity, whether the account already has a witness record, and whether the requesting account's balance is at least `dynamicStore.getAccountUpgradeCost()`: [1](#0-0) 

There is no hard limit on the number of witnesses that can be created — `witnessStore.put(...)` unconditionally inserts a new `WitnessCapsule` for any account that passes the balance check: [2](#0-1) 

Every witness added to `WitnessStore` is later swept up by multiple linear/superlinear operations that run on the consensus hot path and on public RPC/HTTP endpoints:

1. **Consensus maintenance (every maintenance cycle, on the block-processing path):** `MaintenanceManager.doMaintenance()` iterates `consensusDelegate.getAllWitnesses()` (i.e., all rows in `WitnessStore`) multiple times per cycle — once to accumulate delegation `Vi` values, once to build the candidate address list, and again to persist brokerage/vote data — in addition to iterating the vote-count map: [3](#0-2) 

2. **`WitnessStore.getWitnessStandby`** pulls all witnesses and performs an O(n log n) sort over the entire witness set on every call: [4](#0-3) 

3. **Public RPC/HTTP surface** (`ListWitnesses`, `GetPaginatedNowWitnessList`) also loads the *entire* `WitnessStore` into memory, applies vote deltas, and sorts it, per anonymous request: [5](#0-4) [6](#0-5) [7](#0-6) 

A project test explicitly demonstrates that thousands of witness rows can be created and processed by these code paths without any protocol-level rejection: [8](#0-7) 

This is the direct structural analog of the Ditto report: entry into a validator/witness set is gated only by a flat, non-scaling economic cost (`AccountUpgradeCost`), with no protocol-level `max_n_validators`-style cap and no requirement to maintain minimum activity/stake once registered (a witness with 0 votes remains in `WitnessStore` forever unless explicitly a "standby" trim, which itself only applies to the top `WITNESS_STANDBY_LENGTH` after already sorting the full set).

### Impact Explanation
An attacker who can fund enough accounts to pay `AccountUpgradeCost` per witness (a fixed fee, not increasing with existing witness count) can register an arbitrarily large number of witnesses. Because `WitnessStore` is iterated in full:
- On every DPoS maintenance cycle (a consensus-critical, block-processing code path in `MaintenanceManager.doMaintenance()`), increasing per-cycle CPU/DB work for every node in the network.
- On unauthenticated `ListWitnesses` / `GetPaginatedNowWitnessList` gRPC and HTTP endpoints, allowing cheap repeated requests to force full-table scans, sorts, and vote-delta computations on the serving node.

In the worst case this degrades block-processing performance network-wide (all full nodes execute the same maintenance logic) and/or allows a remotely-reachable RPC endpoint to be used to consume disproportionate CPU/memory relative to the cost of the request — a DoS vector consistent with the "computation limit" risk called out in the source report.

### Likelihood Explanation
Likelihood is moderate: the attack requires capital proportional to `AccountUpgradeCost × N`, so it is not free, but nothing in the protocol scales this cost with the number of existing witnesses or requires witnesses to retain minimum stake/activity to remain in `WitnessStore`. A moderately funded actor (or a large botnet of low-value accounts, if `AccountUpgradeCost` is ever lowered via governance) could register thousands of no-vote witnesses cheaply relative to the increased per-node/per-request computational burden they impose. The RPC-facing amplification (repeated `ListWitnesses`/`GetPaginatedNowWitnessList` calls once the store is bloated) requires no additional cost beyond normal RPC rate limits.

### Recommendation
- Impose a protocol-enforced hard cap on the total number of registered witnesses (`max_n_validators`-style constant/parameter), rejected in `WitnessCreateActuator.validate()`.
- Consider requiring witnesses to maintain a minimum vote count or periodically re-affirm activity/stake, with automatic pruning of long-idle, zero-vote witnesses from `WitnessStore`.
- Scale `AccountUpgradeCost` (or add a separate escalating cost) with the current number of registered witnesses to disincentivize mass registration.
- Bound or paginate the RPC/HTTP witness endpoints' internal full-store scans/sorts, or cache sorted results, to reduce the impact of a bloated `WitnessStore` on request latency.

### Proof of Concept
1. Fund `N` distinct accounts each with at least `AccountUpgradeCost` TRX (a fixed protocol constant, unrelated to current total witness count).
2. Broadcast `N` `WitnessCreateContract` transactions, one per funded account, via any full node's transaction broadcast endpoint — each succeeds and inserts a new row into `WitnessStore` per `WitnessCreateActuator.execute`/`createWitness` (`actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java:121-149`), with no upper bound check.
3. Observe that `MaintenanceManager.doMaintenance()` (`consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java:89-163`) now iterates `N` extra witness rows every maintenance cycle on every full node.
4. Separately, repeatedly call the anonymous `ListWitnesses`/`GetPaginatedNowWitnessList` gRPC/HTTP endpoints (`framework/src/main/java/org/tron/core/Wallet.java:762-822`) against a target node; each call now performs a full-store scan and sort over the inflated `WitnessStore`, as validated by the existing test injecting 1000+ fake witnesses (`framework/src/test/java/org/tron/core/WalletTest.java:887-919`).

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L98-106)
```java
    if (witnessStore.has(ownerAddress)) {
      throw new ContractValidateException(
          WITNESS_EXCEPTION_STR + readableOwnerAddress + "] has existed");
    }

    if (accountCapsule.getBalance() < dynamicStore
        .getAccountUpgradeCost()) {
      throw new ContractValidateException("balance < AccountUpgradeCost");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L121-149)
```java
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

**File:** chainbase/src/main/java/org/tron/core/store/WitnessStore.java (L44-63)
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

  public static void sortWitnesses(List<WitnessCapsule> witnesses, boolean isSortOpt) {
    witnesses.sort(Comparator.comparingLong(WitnessCapsule::getVoteCount).reversed()
        .thenComparing(isSortOpt
            ? Comparator.comparing(WitnessCapsule::createReadableString).reversed()
            : Comparator.comparingInt((WitnessCapsule w) -> w.getAddress().hashCode()).reversed()));
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L762-822)
```java
  public WitnessList getWitnessList() {
    WitnessList.Builder builder = WitnessList.newBuilder();
    List<WitnessCapsule> witnessCapsuleList = chainBaseManager.getWitnessStore().getAllWitnesses();
    witnessCapsuleList
        .forEach(witnessCapsule -> builder.addWitnesses(witnessCapsule.getInstance()));
    return builder.build();
  }

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

**File:** framework/src/main/java/org/tron/core/services/RpcApiService.java (L393-409)
```java
    @Override
    public void listWitnesses(EmptyMessage request, StreamObserver<WitnessList> responseObserver) {
      responseObserver.onNext(wallet.getWitnessList());
      responseObserver.onCompleted();
    }

    @Override
    public void getPaginatedNowWitnessList(PaginatedMessage request,
        StreamObserver<WitnessList> responseObserver) {
      try {
        responseObserver.onNext(
            wallet.getPaginatedNowWitnessList(request.getOffset(), request.getLimit()));
      } catch (MaintenanceUnavailableException e) {
        responseObserver.onError(getRunTimeException(e));
      }
      responseObserver.onCompleted();
    }
```

**File:** framework/src/main/java/org/tron/core/services/http/ListWitnessesServlet.java (L19-31)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      WitnessList reply = wallet.getWitnessList();
      if (reply != null) {
        response.getWriter().println(JsonFormat.printToString(reply, visible));
      } else {
        response.getWriter().println("{}");
      }
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```

**File:** framework/src/test/java/org/tron/core/WalletTest.java (L887-919)
```java
  public void testGetPaginatedNowWitnessList_CornerCase() {
    try {
      // To avoid throw MaintenanceClearingException
      dbManager.getChainBaseManager().getDynamicPropertiesStore().saveStateFlag(0);

      GrpcAPI.WitnessList witnessList = wallet.getPaginatedNowWitnessList(-100, 0);
      Assert.assertTrue("Should return an empty witness list when offset is negative",
          witnessList == null);

      witnessList = wallet.getPaginatedNowWitnessList(100, 0);
      Assert.assertTrue("Should return an empty witness list when limit is 0", witnessList == null);

      String fakeWitnessAddressPrefix = "fake_witness";
      int fakeNumberOfWitnesses = 1000 + 10;
      // Mock additional witnesses with vote counts greater than 1000
      for (int i = 0; i < fakeNumberOfWitnesses; i++) {
        saveWitnessWith(fakeWitnessAddressPrefix + i, 200);
      }

      witnessList = wallet.getPaginatedNowWitnessList(0, 1000000);
      // Check the returned witness list should contain 1000 witnesses with descending vote count
      Assert.assertTrue("Witness list should contain 1000 witnesses",
          witnessList.getWitnessesCount() == 1000);

      // clean up, delete the fake witnesses
      for (int i = 0; i < fakeNumberOfWitnesses; i++) {
        chainBaseManager.getWitnessStore()
            .delete(ByteString.copyFromUtf8(fakeWitnessAddressPrefix + i).toByteArray());
      }
    } catch (MaintenanceUnavailableException e) {
      Assert.fail(e.getMessage());
    }
  }
```
