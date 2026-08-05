### Title
Non-total-order Comparator in `getAllProposals` can throw `IllegalArgumentException` and yield non-deterministic proposal ordering - (File: chainbase/src/main/java/org/tron/core/store/ProposalStore.java)

### Finding Description
`ProposalStore.getAllProposals` sorts proposals using the lambda `(a, b) -> a.getCreateTime() <= b.getCreateTime() ? 1 : -1`. [1](#0-0) 

This comparator violates the `Comparator` contract's antisymmetry requirement: for any two proposals `a` and `b` with equal `createTime`, `compare(a,b)` evaluates `a.getCreateTime() <= b.getCreateTime()` which is `true`, returning `1`; and `compare(b,a)` evaluates `b.getCreateTime() <= a.getCreateTime()`, also `true` (equal values), also returning `1`. Thus `compare(a,b) == compare(b,a) == 1`, meaning the comparator claims `a > b` and `b > a` simultaneously — never returning `0` for equal elements, and never being consistent with swap. This is a stronger defect than mere non-transitivity: it is a direct antisymmetry violation.

`List.sort`/`Stream.sorted()` for object arrays in the JDK use `TimSort`, which performs runtime contract-validation (`mergeCollapse`/`mergeHi`/`mergeLo` invariant checks). For arrays whose length reaches the merge-sort threshold (`TimSort.MIN_MERGE = 32`), TimSort can detect the contract violation and throw `IllegalArgumentException("Comparison method violates its general contract!")`. For smaller lists (using binary insertion sort), no exception is thrown, but the resulting order among tied `createTime` values is an implementation-dependent artifact rather than a well-defined order, and is not guaranteed to be stable or reproducible across JVM versions.

`getAllProposals()` feeds `Wallet.getPaginatedProposalList`-style logic, which backs `GetPaginatedProposalListServlet` (HTTP) and the corresponding gRPC method in `RpcApiService`, both public/unauthenticated read endpoints. [2](#0-1) [3](#0-2) 

An unprivileged user can submit ordinary `ProposalCreateContract` transactions; multiple proposals created within the same block (or via the same committee-approved timestamp path) commonly share identical `createTime`, since `createTime` is typically block-time based, not per-transaction unique — making ties trivial to produce, not requiring any special "fuzzed timing" exploit. Once ≥32 proposals with a tie (or a triple `a==b`, `b==c`, `a<c` etc.) exist, `getAllProposals()`'s `sorted()` call risks throwing at runtime on any node that calls this read path.

### Impact Explanation
- If TimSort's internal invariant check triggers, `getAllProposals()` throws an unhandled `IllegalArgumentException`, causing the public `getProposalListPaginated` HTTP/gRPC endpoint to fail (denial of service for that endpoint) on any full node that has accumulated the offending proposal set.
- Where no exception is thrown (list size < 32), the ordering of tied entries is not a well-defined total order, so the same underlying data could, in principle across different JDK sort algorithm versions, be presented in different orders — impacting reproducibility of query results, though this does not affect on-chain consensus state (only the presentation of a read-only API result).
- This is a query-serving-layer correctness/availability issue, not a consensus-state-divergence or fund-theft issue, since `getAllProposals` is not used in block validation or state-transition logic (only in `ProposalStoreTest` and `Wallet.java` for querying).

### Likelihood Explanation
- Reaching the array-size threshold (32) requires ≥32 committee proposals to exist simultaneously in the store with a comparator-violating tie among them — proposal creation is rate-limited implicitly by proposal deposit/frozen-balance requirements and by committee proposal semantics, but nothing in `ProposalCreateContract` prevents an actor (or several) from creating many proposals over time whose `createTime` values collide, since `createTime` typically derives from block time shared by all transactions in a block.
- No admin/governance privilege is required to create a `ProposalCreateContract` (subject to normal witness/committee submission rules) — this analysis assumes the standard, unprivileged transaction submission path is reachable, per repo context in `ProposalStore.java`/`Wallet.java`. Exact eligibility restrictions on proposal creation were not verified in this pass.

### Recommendation
Replace the comparator with a proper total-order comparator, e.g.:
```java
.sorted(Comparator.comparingLong(ProposalCapsule::getCreateTime))
```
This returns `0` for equal keys, satisfies antisymmetry/transitivity, and eliminates both the `IllegalArgumentException` risk and any related output ordering instability. The same fix should be applied to the analogous comparator in `getSpecifiedProposals` (`a.getExpirationTime() > b.getExpirationTime() ? 1 : -1`), which has the identical defect. [4](#0-3) 

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/db/ProposalStoreComparatorTest.java
@Test
public void testGetAllProposalsThrowsOnTiesAtScale() {
  // Build >=32 ProposalCapsule mocks with duplicate createTime values,
  // e.g. 16 proposals with createTime=1000L and 16 with createTime=2000L,
  // inserted into the store in non-sorted iteration order.
  List<ProposalCapsule> proposals = new ArrayList<>();
  for (int i = 0; i < 16; i++) {
    proposals.add(mockProposal(/*id*/ i, /*createTime*/ 1000L));
  }
  for (int i = 16; i < 32; i++) {
    proposals.add(mockProposal(i, 2000L));
  }
  Collections.shuffle(proposals);

  // Directly exercise the same comparator used in ProposalStore.getAllProposals
  Comparator<ProposalCapsule> cmp =
      (a, b) -> a.getCreateTime() <= b.getCreateTime() ? 1 : -1;

  assertThrows(IllegalArgumentException.class, () -> proposals.sort(cmp));
  // Expected: "Comparison method violates its general contract!"
}
```
A companion fuzz test can randomize the count and distribution of `createTime` ties over many runs (array sizes swept from 8 to 128) asserting that either (a) no exception occurs and the output is verified to be a valid total order (`compare(x[i], x[i+1]) <= 0` for all `i`), or (b) flag any run that throws `IllegalArgumentException`, demonstrating the contract violation is real and reachable with realistic data (many proposals sharing a block-derived `createTime`).

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/store/ProposalStore.java (L44-53)
```java
  public List<ProposalCapsule> getSpecifiedProposals(State state, long code) {
    return Streams.stream(iterator())
        .map(Map.Entry::getValue)
        .filter(proposalCapsule -> proposalCapsule.getState().equals(state))
        .filter(proposalCapsule -> proposalCapsule.getParameters().containsKey(code))
        .sorted(
            (ProposalCapsule a, ProposalCapsule b) -> a.getExpirationTime() > b.getExpirationTime()
                ? 1 : -1)
        .collect(Collectors.toList());
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedProposalListServlet.java (L1-1)
```java
package org.tron.core.services.http;
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1-1)
```java
/*
```
