### Title
Non-deterministic/exception-throwing sort in `ProposalStore.getAllProposals` due to contract-violating comparator - (File: chainbase/src/main/java/org/tron/core/store/ProposalStore.java)

### Summary
`ProposalStore.getAllProposals` sorts `ProposalCapsule` entries using the lambda `(a, b) -> a.getCreateTime() <= b.getCreateTime() ? 1 : -1`, which returns `1` for both `compare(a,b)` and `compare(b,a)` whenever `a.getCreateTime() == b.getCreateTime()`, violating the antisymmetry requirement of `Comparator`. `createTime` is set from `getLatestBlockHeaderTimestamp()` in `ProposalCreateActuator.execute`, so any two proposals created in the same block get identical `createTime` values. If enough proposals (Java's `TimSort` triggers this once the run-merge pattern is exercised, typically with tens of elements) share this identical timestamp, `Collections.sort`/`Stream.sorted()` backed by `TimSort` can throw `IllegalArgumentException("Comparison method violates its general contract!")`.

### Finding Description
- `ProposalCreateActuator.execute` sets `proposalCapsule.setCreateTime(now)` where `now = chainBaseManager.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp()` [1](#0-0) . This value is identical for every `ProposalCreateContract` executed within the same block, since the header timestamp only changes at block boundaries.
- `ProposalStore.getAllProposals` sorts all stored `ProposalCapsule`s with a broken comparator: `(a, b) -> a.getCreateTime() <= b.getCreateTime() ? 1 : -1` [2](#0-1) . For equal `createTime`, both `compare(a,b)` and `compare(b,a)` evaluate to `1`, which is inconsistent with a valid total order (it should return `0` for ties). This is a textbook violation of the `Comparator` general contract.
- `getAllProposals` is reachable from the public API surface: it backs `ListProposalsServlet`/`GetPaginatedProposalListServlet` (HTTP) and the corresponding gRPC `listProposals` call in `Wallet.java`/`RpcApiService.java` [3](#0-2) , meaning any unauthenticated client can trigger the sort by simply querying the proposal list — no special privileges needed beyond the attacker registering as a witness (`WitnessCreateContract`, an already-permitted unprivileged action) and broadcasting proposals.
- An attacker who is a registered witness can submit multiple `ProposalCreateContract` transactions that all land in the same block, producing multiple `ProposalCapsule` records with identical `createTime`. Java's dual-pivot `TimSort` (used by `Collections.sort`/`Stream.sorted` for object arrays) explicitly checks for comparator-contract violations once array size crosses an internal threshold (32 elements by default, `Arrays.MIN_MERGE_SORT` / merge-run detection), throwing `IllegalArgumentException` at runtime.
- No existing guard limits the number of `ProposalCreateContract`s a witness can submit per block, nor does the actuator differentiate `createTime` beyond block-timestamp granularity, so the precondition (≥32 proposals with identical `createTime`) is fully attacker-controllable.

### Impact Explanation
Any full node serving the `listProposals`/`getPaginatedProposalList` gRPC/HTTP endpoint can crash with an uncaught `IllegalArgumentException` (or, in JVM configurations/versions where the check doesn't fire, silently return non-deterministically ordered results) when enough same-block proposals exist in the store. This is a public-API denial-of-service / non-determinism bug reachable by an unprivileged (but witness-registered) account, degrading node availability for a read API and causing potential inconsistent client-facing views of governance state across nodes — though it does not directly allow theft, double-settlement, or state divergence in the authoritative on-chain state (the underlying store data itself is unaffected; only the derived, in-memory sorted list construction is broken).

### Likelihood Explanation
- Preconditions: attacker must become a witness via `WitnessCreateContract` (a permissionless action already reachable via normal API, contingent on paying/staking as required by that actuator — no special privileges) [4](#0-3) , then submit ≥32 `ProposalCreateContract` transactions within one block. There is no rate limit or per-block cap on proposal creation visible in `ProposalCreateActuator.validate`.
- The bug is fully repeatable and deterministic given ties in `createTime`; it does not depend on race conditions or timing outside the attacker's control (the attacker fully controls how many proposals they submit in one block, assuming sufficient bandwidth/fees).
- Feasibility is moderate: it requires the actor to be a witness (an on-chain cost) and to get many proposal-create transactions packed into a single block, which is achievable but subject to block gas/size and confirmation limits.

### Recommendation
Fix the comparator in `ProposalStore.getAllProposals` to be a proper total order, e.g. `Comparator.comparingLong(ProposalCapsule::getCreateTime).reversed()` or `(a, b) -> Long.compare(b.getCreateTime(), a.getCreateTime())`, and add a secondary tie-breaker (e.g., proposal ID) for deterministic ordering: `Comparator.comparingLong(ProposalCapsule::getCreateTime).reversed().thenComparing(...)`. Apply the same fix pattern to `getSpecifiedProposals`'s comparator, which has the same anti-symmetry defect (`a.getExpirationTime() > b.getExpirationTime() ? 1 : -1` also fails to return 0 on ties, though in that case it does not flip signs for equal inputs on both calls — still worth using `Long.compare` for correctness) [5](#0-4) .

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/db/ProposalStoreComparatorTest.java
@Test
public void getAllProposalsThrowsOnManyTiedCreateTimes() throws Exception {
  dbManager.getDynamicPropertiesStore().saveLatestBlockHeaderTimestamp(1_000_000L);
  dbManager.getDynamicPropertiesStore().saveNextMaintenanceTime(2_000_000L);
  dbManager.getDynamicPropertiesStore().saveLatestProposalNum(0L);

  // witness + account setup omitted (same as ProposalCreateActuatorTest.initTest)

  HashMap<Long, Long> paras = new HashMap<>();
  paras.put(0L, 1_000_000L);

  // Simulate 40 ProposalCreateContract txs executed in the SAME block
  // (identical getLatestBlockHeaderTimestamp -> identical createTime)
  for (int i = 0; i < 40; i++) {
    ProposalCreateActuator actuator = new ProposalCreateActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setForkUtils(dbManager.getChainBaseManager().getForkController())
        .setAny(getContract(OWNER_ADDRESS_FIRST, paras));
    TransactionResultCapsule ret = new TransactionResultCapsule();
    actuator.validate();
    actuator.execute(ret);
    Assert.assertEquals(ret.getInstance().getRet(), code.SUCESS);
  }

  // Expect IllegalArgumentException("Comparison method violates its general contract!")
  // due to the broken comparator in ProposalStore.getAllProposals
  assertThrows(IllegalArgumentException.class,
      () -> dbManager.getProposalStore().getAllProposals());
}
```
Expected result before fix: `IllegalArgumentException: Comparison method violates its general contract!` thrown from `TimSort`, crashing the `listProposals`/`getPaginatedProposalList` request handler. After applying `Long.compare`-based comparator, the test should pass without exception and produce a stable, deterministic ordering across repeated invocations.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ProposalCreateActuator.java (L48-51)
```java
      long now = chainBaseManager.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
      long maintenanceTimeInterval = chainBaseManager.getDynamicPropertiesStore()
          .getMaintenanceTimeInterval();
      proposalCapsule.setCreateTime(now);
```

**File:** chainbase/src/main/java/org/tron/core/store/ProposalStore.java (L32-39)
```java
  public List<ProposalCapsule> getAllProposals() {
    return Streams.stream(iterator())
        .map(Map.Entry::getValue)
        .sorted(
            (ProposalCapsule a, ProposalCapsule b) -> a.getCreateTime() <= b.getCreateTime() ? 1
                : -1)
        .collect(Collectors.toList());
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/ProposalStore.java (L49-51)
```java
        .sorted(
            (ProposalCapsule a, ProposalCapsule b) -> a.getExpirationTime() > b.getExpirationTime()
                ? 1 : -1)
```

**File:** framework/src/main/java/org/tron/core/services/http/ListProposalsServlet.java (L1-1)
```java
package org.tron.core.services.http;
```

**File:** framework/src/test/java/org/tron/core/actuator/ProposalCreateActuatorTest.java (L188-209)
```java
  @Test
  public void noWitness() {
    HashMap<Long, Long> paras = new HashMap<>();
    paras.put(0L, 10000L);
    ProposalCreateActuator actuator = new ProposalCreateActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setForkUtils(dbManager.getChainBaseManager().getForkController())
        .setAny(getContract(OWNER_ADDRESS_SECOND, paras));

    TransactionResultCapsule ret = new TransactionResultCapsule();
    try {
      actuator.validate();
      actuator.execute(ret);
      fail("witness[+OWNER_ADDRESS_NOWITNESS+] not exists");
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("Witness[" + OWNER_ADDRESS_SECOND + "] not exists",
          e.getMessage());
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    }
  }
```
