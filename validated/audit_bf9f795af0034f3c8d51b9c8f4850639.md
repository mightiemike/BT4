### Title
Unbounded Full `VotesStore` Iteration in `GetPaginatedNowWitnessList` API Allows Cheap-Vote-Spam DoS on Public Nodes - (File: `framework/src/main/java/org/tron/core/Wallet.java`)

### Summary
The `Wallet.getPaginatedNowWitnessList()` API, exposed via unauthenticated HTTP/gRPC endpoints, always performs a full linear scan of the entire `VotesStore` (via its internal `countVote()` helper) on every single call, regardless of the requested `offset`/`limit`. Because casting a vote (`VoteWitnessContract`) is nearly free on-chain — it costs zero protocol fee and only requires a small, reusable amount of frozen TRX — an attacker can cheaply inflate `VotesStore` with many entries and then repeatedly hit the paginated witness-list API to force every serving full node/solidity node to redo an O(n) full-store iteration per request, mirroring the "cheap spam on one axis, expensive cost imposed on a shared public-good operator" pattern described in the Across report.

### Finding Description
`VotesStore` holds at most one `VotesCapsule` per voter address, created/updated by `VoteWitnessActuator.countVoteAccount()`: [1](#0-0) 

`VoteWitnessActuator.calcFee()` returns `0`, and `validate()` only requires that `sum(votes) * TRX_PRECISION <= tronPower` (i.e., a small amount of frozen TRX per account): [2](#0-1) [3](#0-2) 

An attacker can therefore cheaply create a large number of `VotesStore` entries by funding many low-cost accounts (standard account activation cost), freezing a minimal amount of TRX for TronPower in each, and issuing one `VoteWitnessContract` per account. These entries persist until the next maintenance cycle (they are cleared only inside `MaintenanceManager.doMaintenance()` → `countVote()`, which runs once every ~6 hours): [4](#0-3) 

Separately, `Wallet.getPaginatedNowWitnessList()` — reachable from a public, only rate-limited (not cost-limited) HTTP servlet — calls its own `countVote(votesStore)` that unconditionally iterates the *entire* `VotesStore`, even though the caller only asked for a small page (`offset`/`limit`): [5](#0-4) [6](#0-5) 

The HTTP endpoint that invokes this is guarded only by a generic `RateLimiterServlet`, not by request cost: [7](#0-6) 

This produces the same cost asymmetry described in the report: the write side (creating VotesStore entries via cheap vote transactions) is inexpensive, while the read side (the paginated witness list query, which any anonymous client can call repeatedly) is forced to redo a full O(n) store scan per request. Because this query is served by full/solidity nodes acting as public infrastructure (analogous to the dataworker executing refund leaves), an attacker can impose disproportionate CPU/I-O cost on node operators simply by inflating vote-entry count and then spamming the paginated query endpoint.

### Impact Explanation
Repeated calls to the paginated witness-list API against an inflated `VotesStore` degrade full-node/solidity-node responsiveness for all API consumers (wallets, explorers, dApps) until the next maintenance cycle clears the store, and the attacker can refill it immediately afterward. This is a resource-exhaustion/DoS impact on public RPC-API infrastructure rather than fund loss, consistent with a DoS-via-RPC-API classification.

### Likelihood Explanation
Exploitability requires only broadcasting cheap `VoteWitnessContract` transactions from many low-cost accounts (no privileged role, no leaked keys, no P2P/node compromise needed) and then issuing unauthenticated HTTP/gRPC requests to a publicly exposed endpoint. The per-request cost the attacker incurs (rate-limited client requests) is far lower than the O(n) work forced on the serving node, making repeated exploitation economically favorable, similar to the original report's asymmetry.

### Recommendation
- Avoid a full `VotesStore` scan on every `getPaginatedNowWitnessList` call; cache/reuse the aggregated vote-delta map per block or per maintenance epoch instead of recomputing it per request.
- Bound or paginate the underlying iteration itself (not just the output), or maintain an incrementally-updated vote-count index that avoids O(n) work per query.
- Consider tightening the economic cost of casting votes from newly created/low-balance accounts, or capping the number of `VotesStore` entries scanned per query with a hard limit independent of client-supplied offset/limit.

### Proof of Concept
1. Create N low-balance accounts and freeze minimal TRX in each to obtain non-zero TronPower.
2. Broadcast one zero-fee `VoteWitnessContract` per account, each creating a distinct `VotesStore` entry (`VoteWitnessActuator.countVoteAccount`, `actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java:171-190`).
3. Before the next maintenance cycle, repeatedly call the public `GetPaginatedNowWitnessList` HTTP/gRPC endpoint (`framework/src/main/java/org/tron/core/services/http/GetPaginatedNowWitnessListServlet.java:21-30`) with small `offset`/`limit` values.
4. Observe that each call triggers a full linear scan over all N `VotesStore` entries in `Wallet.countVote()` (`framework/src/main/java/org/tron/core/Wallet.java:842-872`), and that request latency/CPU cost scales with N regardless of the requested page size, degrading node responsiveness for all API clients.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L129-150)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L171-190)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L198-201)
```java
  @Override
  public long calcFee() {
    return 0;
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

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedNowWitnessListServlet.java (L14-30)
```java
@Component
@Slf4j(topic = "API")
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
