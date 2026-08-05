### Title
Comparator in `ProposalStore.getAllProposals` violates `Comparator` contract on tied `createTime` values, risking `IllegalArgumentException`/non-deterministic ordering on the public proposal-list API - (File: `chainbase/src/main/java/org/tron/core/store/ProposalStore.java`)

### Summary
`ProposalStore.getAllProposals()` sorts `ProposalCapsule` entries with a comparator that never returns `0`, always returning `1` for ties (`a.getCreateTime() <= b.getCreateTime() ? 1 : -1`), which is a fundamentally broken comparator. Any account that self-registers as a witness via `WitnessCreateContract` can then submit multiple `ProposalCreateContract` transactions inside a single block, producing many `ProposalCapsule` entries with identical `createTime` (block timestamp), which is the exact condition needed to trip `TimSort`'s contract-violation detection or produce non-deterministic ordering on this public endpoint.

### Finding Description
`getAllProposals()` sorts with: [1](#0-0) 
This comparator returns `1` whenever `a.getCreateTime() <= b.getCreateTime()`, meaning `compare(a, a)` returns `1` instead of the required `0`, and `compare(a, b)` and `compare(b, a)` both return `1` when `a.getCreateTime() == b.getCreateTime()` — violating antisymmetry, a core requirement of `Comparator`/`TimSort`.

`ProposalCreateActuator.execute` sets `createTime` from the block's `LatestBlockHeaderTimestamp`, which is constant for the duration of a block: [2](#0-1) 
The only gating check in `validate()` is that the sender must exist in `WitnessStore` — i.e., be a registered witness — with no per-block or per-account limit on the number of proposals that can be created: [3](#0-2) 
Witness registration itself is reachable by any unprivileged account via `WitnessCreateContract`/`WitnessCreateActuator` (no special admin privilege required beyond the normal witness-creation fee), so an attacker can self-qualify to submit proposals.

By submitting ≥32 `ProposalCreateContract` transactions in the same block, the attacker creates ≥32 `ProposalCapsule` records sharing the same `createTime`. Java's `TimSort` performs additional invariant checks once run lengths grow past a threshold (around 32 elements is the point where merge-related invariant checks activate), and a comparator that never returns 0 for ties is a classic trigger of `IllegalArgumentException("Comparison method violates its general contract!")` for lists of this size, or at minimum produces platform/JVM-version-dependent, non-deterministic ordering since the comparator has no defined tie-break rule (no secondary key such as id).

`getAllProposals()` backs the public proposal-list surface reachable via `Wallet.java`'s proposal listing methods, which are exposed by the gRPC/HTTP API used by any client, not just administrators.

### Impact Explanation
This affects a public, unauthenticated read API. If the exception is thrown, calls to the proposal-list endpoint on affected nodes would fail (denial of service for that RPC) once the store accumulates ≥32 (or whatever the actual JVM-dependent threshold is) proposals with a tied `createTime`. Even absent an exception, the lack of a deterministic tie-break means different full nodes (or the same node across JVM versions/GC states) could return proposal lists in different orders for an identical store snapshot, which is undesirable for a consensus-adjacent read API but does not by itself corrupt accounting/settlement state, since `getAllProposals()` is a read helper, not part of block execution/state-root computation. No funds, fees, or settlement values are affected — this is confined to a query-serving-layer determinism/availability issue.

### Likelihood Explanation
- Precondition: attacker must register as a witness (public via `WitnessCreateContract`, requires only the standard witness-registration fee/frozen balance, not privileged access).
- Attacker must then get ≥~32 `ProposalCreateContract` transactions from that address packed into a single block — feasible since there's no per-block/per-owner proposal count limit in `validate()`.
- Whether `TimSort` actually throws depends on JVM version and exact run-length patterns produced by the merge sort; it is not guaranteed on every JVM/every element count, making this "likely reproducible with a specifically crafted test" rather than "always triggers in production," but the underlying comparator defect is unconditionally real and violates the `Comparator` contract regardless of whether the exception manifests.

### Recommendation
Fix the comparator to be a valid total order, e.g. compare by `createTime` using `Long.compare` and add a deterministic tie-breaker (such as proposal id):
```java
.sorted(Comparator.comparingLong(ProposalCapsule::getCreateTime).reversed()
    .thenComparing(ProposalCapsule::getID))
```
Apply the same fix pattern to `getSpecifiedProposals`, which has an analogous non-contract-compliant comparator.

### Proof of Concept
Java unit test (JUnit) plan, mirroring the existing `ProposalStoreTest`/`ProposalCreateActuatorTest` patterns:
1. Set up a `ChainBaseManager` with `DynamicPropertiesStore.saveLatestBlockHeaderTimestamp(T)` fixed to a constant `T`.
2. Register one witness account in `WitnessStore` (simulating a successful `WitnessCreateContract` execution) and fund it in `AccountStore`.
3. Build and `execute()` 40 distinct `ProposalCreateContract` transactions (varying only the parameters map to make each contract distinct) through `ProposalCreateActuator`, all within the same simulated block (timestamp `T` unchanged), each incrementing `LatestProposalNum` and writing to `ProposalStore`.
4. Call `chainBaseManager.getProposalStore().getAllProposals()`.
5. Assert either:
   - the call throws `IllegalArgumentException` with message containing "Comparison method violates its general contract", or
   - two invocations on cloned/re-opened store instances (or across a JVM restart) return lists whose orderings differ despite identical `createTime`/content, demonstrating non-determinism.
6. As a secondary invariant check, directly assert `comparator.compare(a, a) == 0` fails for the existing lambda, proving the contract violation independent of `TimSort` behavior.

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

**File:** actuator/src/main/java/org/tron/core/actuator/ProposalCreateActuator.java (L48-51)
```java
      long now = chainBaseManager.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
      long maintenanceTimeInterval = chainBaseManager.getDynamicPropertiesStore()
          .getMaintenanceTimeInterval();
      proposalCapsule.setCreateTime(now);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ProposalCreateActuator.java (L100-112)
```java
    if (!chainBaseManager.getAccountStore().has(ownerAddress)) {
      throw new ContractValidateException(
          ACCOUNT_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
    }

    if (!chainBaseManager.getWitnessStore().has(ownerAddress)) {
      throw new ContractValidateException(
          WITNESS_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
    }

    if (contract.getParametersMap().size() == 0) {
      throw new ContractValidateException("This proposal has no parameter.");
    }
```
