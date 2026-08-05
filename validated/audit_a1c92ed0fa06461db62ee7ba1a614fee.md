### Title
Unbounded Witness (Validator) Registration Causes Unbounded Growth of Consensus State Iterated Every Maintenance Cycle - ([File: actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java])

### Summary
`WitnessCreateActuator` allows any account with sufficient balance to permanently register as a witness (TRON's validator role) by paying a fixed `AccountUpgradeCost` fee. There is no hard cap on the total number of witnesses that can be registered on-chain. Every registered witness is kept forever in `WitnessStore` and is iterated in full, multiple times, by core consensus code (`MaintenanceManager.doMaintenance`) on every maintenance cycle, as well as by several public API paths that return witness data. This mirrors the Ditto `add_validator` bug class: an actor can grow validator-like state without bound, increasing the linear-time cost of core protocol operations.

### Finding Description
`WitnessCreateActuator.validate()` only checks that the contract's owner address is valid, the account exists, has a non-existing witness entry, and has balance `>= dynamicStore.getAccountUpgradeCost()`: [1](#0-0) 

`createWitness()` then unconditionally writes a new `WitnessCapsule` into `WitnessStore`, with no upper bound check on total witness count: [2](#0-1) 

Unlike the "hard limit + activity/stake requirement" remediation applied by Ditto (`max_n_validators`), java-tron has no equivalent cap on the number of witnesses that may exist in `WitnessStore`. The only per-address restriction is that a single address can only register once (`witnessStore.has(ownerAddress)`), but an attacker can create arbitrarily many distinct accounts, each paying the (fixed, not scaling with total witness count) `AccountUpgradeCost`.

Once created, a witness is never automatically purged. `WitnessStore.getAllWitnesses()` performs a full linear scan of the underlying DB: [3](#0-2) 

This full scan is invoked multiple times per maintenance cycle inside `MaintenanceManager.doMaintenance()`, which is core DPoS consensus logic executed by every full node on every maintenance interval (default 6 hours), independent of witness activity: [4](#0-3) [5](#0-4) 

Additional linear-time operations over the full witness set exist in public-facing wallet/API code such as `Wallet.getWitnessList()` and `Wallet.getPaginatedNowWitnessList()`, both of which call `getAllWitnesses()` and then sort/iterate the entire result set: [6](#0-5) [7](#0-6) 

The `WITNESS_STANDBY_LENGTH` cap referenced in `WitnessStore.getWitnessStandby` only trims the *returned/considered* subset for consensus scheduling purposes, it does not limit how many witnesses can be *stored*; `getAllWitnesses()` and the maintenance loop still operate over the entire unbounded set: [8](#0-7) 

### Impact Explanation
Because there is no cap on the number of witnesses that can be registered, and because the cost per registration (`AccountUpgradeCost`) is fixed rather than increasing with total witness count, a well-funded attacker can create a very large number of low-vote/inactive witness accounts. Every one of these entries is then iterated — sometimes multiple times — inside `doMaintenance()`, which runs on every full node as part of normal block/consensus processing, not something that can be skipped or rate-limited by an operator. Growth of this table therefore degrades maintenance-cycle processing time network-wide, degrades the API endpoints that enumerate witnesses, and in the worst case could push per-cycle processing time high enough to threaten liveness/availability of the network — the same DoS impact class described in the Ditto report.

### Likelihood Explanation
Likelihood is moderate: the cost is not free (it requires funding an account to at least `AccountUpgradeCost`, e.g. historically 9999 TRX per witness on mainnet), but this cost is fixed and does not scale with the number of existing witnesses, and there is no protocol-level cap analogous to Ditto's `max_n_validators`. An attacker with sufficient capital (or by targeting a lower-cost private/test network) can create witnesses at will and is under no obligation to ever produce blocks, vote, or accrue votes — the entry remains in `WitnessStore` and continues to be iterated by `doMaintenance()` indefinitely.

### Recommendation
- Introduce a configurable hard cap on the total number of registered witnesses (analogous to Ditto's `max_n_validators`), enforced in `WitnessCreateActuator.validate()`.
- Consider requiring minimum ongoing vote/stake activity for a witness to remain in `WitnessStore`, and prune/expire witnesses that fail to meet it, to prevent unbounded accumulation of inert entries.
- Alternatively, make `AccountUpgradeCost` scale with the current total witness count so registration becomes progressively more expensive as the set grows.

### Proof of Concept
1. Fund N distinct accounts, each with balance `>= dynamicStore.getAccountUpgradeCost()`.
2. For each account, broadcast a `WitnessCreateContract` transaction (via `CreateWitnessServlet` / `RpcApiService.createWitness`), which passes validation in `WitnessCreateActuator.validate()` since only balance and uniqueness are checked (`actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java:87-106`).
3. Repeat for a large N (limited only by capital, not by any protocol cap).
4. Observe that `WitnessStore` now contains N extra entries, and that every subsequent maintenance cycle's `MaintenanceManager.doMaintenance()` call, along with `Wallet.getWitnessList()`/`getPaginatedNowWitnessList()`, now performs linear work proportional to N, without these new witnesses needing to ever be active or receive votes.

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

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L121-140)
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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L96-109)
```java
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

**File:** framework/src/main/java/org/tron/core/Wallet.java (L793-815)
```java
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
```
