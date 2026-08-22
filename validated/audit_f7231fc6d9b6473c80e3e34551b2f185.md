### Title
Unauthenticated O(n) VotesStore full-scan on every getPaginatedNowWitnessList call enables cheap, sustained RPC-latency DoS - ([File: framework/src/main/java/org/tron/core/Wallet.java])

### Summary
`Wallet.getPaginatedNowWitnessList` calls a private `countVote(VotesStore)` that performs a full linear iteration over the entire `VotesStore` on **every** invocation of the public/unauthenticated `getPaginatedNowWitnessList` gRPC/HTTP API. Because ordinary `VoteWitnessContract` transactions insert one persistent `VotesCapsule` row per distinct voter address into `VotesStore`, and that store is only cleared once per ~6-hour maintenance cycle (`MaintenanceManager.doMaintenance` → `countVote`), an unprivileged attacker who funds many accounts and casts votes from each can grow the store and force every subsequent read-path call to pay for an O(n) scan until the next maintenance cycle runs.

### Finding Description
- `Wallet.getPaginatedNowWitnessList(offset, limit)` [1](#0-0)  unconditionally calls `countVote(votesStore)`, which fully iterates `votesStore.iterator()` from start to end, decoding every `VotesCapsule` and building a `Map<ByteString, Long>` of vote deltas, on **every call**, regardless of the requested `offset`/`limit` pagination window [2](#0-1) .
- The only guard is a maintenance-window check (`isMaintenance`) that throws `MaintenanceUnavailableException` for HEAD-cursor fullnode requests during the maintenance flag window [3](#0-2) ; it does **not** bound or cache the cost of the scan based on `VotesStore` size.
- `VotesStore` rows are written by `VoteWitnessActuator.countVoteAccount`, one row keyed by `ownerAddress` per distinct voter, on every successful `VoteWitnessContract` [4](#0-3) . `validate()` only checks that vote counts don't exceed the voter's own TRON power and caps votes-per-tx to `MAX_VOTE_NUMBER` (30); it does **not** limit the number of distinct voter accounts globally [5](#0-4) .
- `VotesStore` rows are only removed in bulk by `MaintenanceManager.countVote`, which deletes each entry as it iterates during `doMaintenance` [6](#0-5) . This runs on a fixed ~6-hour cycle, so attacker-created rows persist and accumulate between cycles.
- The HTTP endpoint `GetPaginatedNowWitnessListServlet` wraps `wallet.getPaginatedNowWitnessList` and extends `RateLimiterServlet` [7](#0-6) , which throttles per-caller request rate but does not bound the per-request computational cost, so any caller within the rate limit (or many distinct callers/IPs) still triggers the full-store scan each time.
- The gRPC path (`RpcApiService`) exposes the same `Wallet.getPaginatedNowWitnessList` method without any additional cost-based throttling tied to store size.

### Impact Explanation
This is a **DoS via RPC-API** class issue: an unauthenticated/unprivileged caller invoking a public read endpoint repeatedly causes the node to perform unbounded, attacker-influenced work (linear in `VotesStore` size) on every call, increasing CPU/GC pressure and response latency for all clients using that fullnode/solidity-node's witness-list query path until the next maintenance cycle purges the store. It does not corrupt consensus state, steal funds, or leak keys — impact is scoped to degraded read-path responsiveness for `ListWitnesses`/`GetPaginatedNowWitnessList` callers.

### Likelihood Explanation
- Feasible with only unprivileged capabilities: fund N accounts, each needs minimal TRX to activate + freeze enough for TRON power to cast at least 1 vote, then broadcast `VoteWitnessContract` transactions naming any existing witness.
- Cost scales with the number of distinct voter addresses the attacker wants resident in `VotesStore` (account activation fee + frozen TRX, which is recoverable after unfreeze, not burned) — real but bounded/moderate cost, not free.
- The attack must be repeated/replenished every ~6 hours since `doMaintenance` clears `VotesStore`, but this is a cheap, repeatable action (rebroadcast votes to keep entries fresh in the "new votes" epoch) via ordinary transactions.
- No signature, permission, or fork-gate check prevents this; `validate()` only limits per-transaction vote count/targets, not the total number of distinct voters in the store.

### Recommendation
- Decouple the real-time vote-count computation from a full per-request store scan: e.g., maintain and incrementally update an in-memory or on-disk aggregate of pending vote deltas as each `VoteWitnessContract` is executed (in `VoteWitnessActuator.countVoteAccount`), rather than recomputing it from scratch on every read call.
- Alternatively, cache the result of `countVote(votesStore)` for a short TTL or until the next new vote/maintenance event, invalidating the cache on writes to `VotesStore`.
- Consider bounding/monitoring `VotesStore` size and/or adding a computational-cost-aware rate limiter to `getPaginatedNowWitnessList` (distinct from the existing per-IP request-rate limiter) so the endpoint's cost cannot grow unbounded between maintenance cycles.

### Proof of Concept
JUnit-style test (mirrors existing `WalletTest.testGetPaginatedNowWitnessList_CornerCase`/`testGetPaginatedNowWitnessList`) demonstrating linear scaling:

```java
// Pseudocode based on framework/src/test/java/org/tron/core/WalletTest.java
@Test
public void testCountVoteScalesWithVotesStoreSize() throws Exception {
  dbManager.getChainBaseManager().getDynamicPropertiesStore().saveStateFlag(0);

  int[] sizes = {1_000, 10_000, 100_000};
  for (int n : sizes) {
    for (int i = 0; i < n; i++) {
      VotesCapsule vc = new VotesCapsule(
          ByteString.copyFromUtf8("voter_" + i), new ArrayList<>());
      vc.addNewVotes(ByteString.copyFromUtf8("someWitnessAddress"), 1L);
      chainBaseManager.getVotesStore().put(vc.createDbKey(), vc);
    }
    long start = System.nanoTime();
    wallet.getPaginatedNowWitnessList(0, 10);
    long elapsed = System.nanoTime() - start;
    // Assert elapsed time grows roughly linearly with n,
    // demonstrating O(n) scan cost per unauthenticated request.
  }
}
```

Raw RPC/HTTP sequence to reproduce in a live testnet setting:
1. Fund N accounts with minimal TRX; activate and freeze minimal balance for TRON power on each.
2. Broadcast `VoteWitnessContract` from each account (one vote for any existing witness).
3. Repeatedly call `wallet/getpaginatednowwitnesslist` (HTTP) or `GetPaginatedNowWitnessList` (gRPC) before the next maintenance cycle and measure increasing response latency correlated with N (`VotesStore` size). [8](#0-7)

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L770-800)
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

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L86-150)
```java
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

      AccountCapsule accountCapsule = accountStore.get(ownerAddress);
      if (accountCapsule == null) {
        throw new ContractValidateException(
            ACCOUNT_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
      }

      long tronPower;
      DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
      if (dynamicStore.supportAllowNewResourceModel()) {
        tronPower = accountCapsule.getAllTronPower();
      } else {
        tronPower = accountCapsule.getTronPower();
      }

      sum = LongMath
          .checkedMultiply(sum, TRX_PRECISION); //trx -> drop. The vote count is based on TRX
      if (sum > tronPower) {
        throw new ContractValidateException(
            "The total number of votes[" + sum + "] is greater than the tronPower[" + tronPower
                + "]");
      }
    } catch (ArithmeticException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }

    return true;
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

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedNowWitnessListServlet.java (L16-30)
```java
public class GetPaginatedNowWitnessListServlet extends RateLimiterServlet {

  @Autowired
  private Wallet wallet;

  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      long offset = Long.parseLong(request.getParameter("offset"));
      long limit = Long.parseLong(request.getParameter("limit"));
      fillResponse(offset, limit, visible, response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```

**File:** framework/src/test/java/org/tron/core/WalletTest.java (L921-957)
```java
  @Test
  public void testGetPaginatedNowWitnessList() {
    GrpcAPI.WitnessList witnessList = wallet.getWitnessList();
    logger.info(witnessList.toString());

    // iterate through the witness list and find the existing maximum vote count
    long maxVoteCount = 0L;
    for (Protocol.Witness witness : witnessList.getWitnessesList()) {
      if (witness.getVoteCount() > maxVoteCount) {
        maxVoteCount = witness.getVoteCount();
      }
    }
    String fakeWitnessAddressPrefix = "fake_witness_address_for_paged_now_witness_list";
    int fakeNumberOfWitnesses = 10;
    // Mock additional witnesses with vote counts greater than the maximum
    for (int i = 0; i < fakeNumberOfWitnesses; i++) {
      saveWitnessWith(fakeWitnessAddressPrefix + i, maxVoteCount + 1000000L);
    }

    // Create a VotesCapsule to simulate the votes for the fake witnesses
    VotesCapsule votesCapsule = new VotesCapsule(ByteString.copyFromUtf8(ACCOUNT_ADDRESS_ONE),
        new ArrayList<Protocol.Vote>());
    votesCapsule.addOldVotes(ByteString.copyFromUtf8(fakeWitnessAddressPrefix + 0), 100L);
    votesCapsule.addOldVotes(ByteString.copyFromUtf8(fakeWitnessAddressPrefix + 1), 50L);
    votesCapsule.addNewVotes(ByteString.copyFromUtf8(fakeWitnessAddressPrefix + 2), 200L);
    votesCapsule.addNewVotes(ByteString.copyFromUtf8(fakeWitnessAddressPrefix + 3), 300L);
    chainBaseManager.getVotesStore().put(votesCapsule.createDbKey(), votesCapsule);

    logger.info("now request paginated witness list with 0 offset and 10 limit:");
    GrpcAPI.WitnessList witnessList2 = null;
    try {
      // To avoid throw MaintenanceClearingException
      dbManager.getChainBaseManager().getDynamicPropertiesStore().saveStateFlag(0);
      witnessList2 = wallet.getPaginatedNowWitnessList(0, 10);
    } catch (MaintenanceUnavailableException e) {
      Assert.fail(e.getMessage());
    }
```
