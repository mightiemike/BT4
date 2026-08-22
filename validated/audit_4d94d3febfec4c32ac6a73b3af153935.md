### Title
Storage deep-copy on nested CALLs causes CPU cost multiplicative in (call depth × storage slots) while energy is charged only additively - ([File: actuator/src/main/java/org/tron/core/vm/program/Storage.java])

### Summary
`RepositoryImpl.getStorage()` deep-copies the entire parent `Storage.rowCache` via `new Storage(Storage)` whenever `StorageUtils.getEnergyLimitHardFork()` is enabled and a child repository first touches a contract's storage. Because each nested `CALL` creates a new child `Repository`, and `Program`/`VMActuator` create one such child repository per call frame, an attacker who writes N storage slots and then recursively self-`CALL`s M times forces M independent `O(N)` `HashMap` copies (`Storage(Storage)` constructor), giving a total CPU cost on the order of `O(M*N)` while SSTORE/CALL energy is billed only for `M` calls plus `N` writes (i.e., `O(M+N)`).

### Finding Description
`Storage.Storage(Storage storage)` performs a full deep copy of the parent's `rowCache`, cloning each `DataWord` key and constructing a new `StorageRowCapsule` for every entry: [1](#0-0) 

This constructor is invoked from `RepositoryImpl.getStorage(byte[] address)` every time a child repository accesses a contract's storage for the first time and the parent already holds a `Storage` object, gated only by the `ENERGY_LIMIT_HARD_FORK` flag: [2](#0-1) 

Each internal message call (`CALL`, `CALLCODE`, `DELEGATECALL`, etc.) in the TVM creates a new child `Repository` for the sub-execution frame (`newRepositoryChild()`), and the first storage access (`SLOAD`/`SSTORE`) in that frame triggers `getStorage()`. Because this deep copy is memoized only within the *same* repository frame (`storageCache`), but a fresh frame is created at every nesting level, the copy work is repeated once per nesting level. If an attacker's contract first writes N distinct storage slots (`SSTORE`) and then recursively calls itself M times before returning, the storage row cache—already populated with N entries—gets fully cloned at each of the M frames, yielding `O(M*N)` total `HashMap`/object-allocation work, even though the opcodes only cost energy proportional to `M` (CALL) and `N` (SSTORE), i.e., `O(M+N)`.

No existing check limits this: `ForkController`/`VMConfig` only gate whether the deep-copy code path is active at all (it doesn't gate the size of `rowCache` or call depth against copy cost), and standard SSTORE/CALL energy costs in `EnergyCost` are linear per-opcode, not aware of the cumulative cross-frame duplication cost this deep-copy strategy introduces.

### Impact Explanation
This is a CPU-cost/fee mismatch (DoS via the TRON protocol implementation): a single transaction can force a full node to perform substantially more CPU/memory-allocation work processing that one transaction than the energy fee paid for it reflects, because the `HashMap` clone cost scales with the product of two attacker-controlled dimensions (slot count and call depth) rather than their sum. With max TVM call depth (historically 1000 in java-tron) and, e.g., a few hundred to a thousand written slots, the constructor is invoked repeatedly, each copy proportional to slot count, producing significantly amplified node-side latency for processing that transaction relative to its energy cost.

### Likelihood Explanation
Preconditions: `ENERGY_LIMIT_HARD_FORK` must be active (this is a chain-level hard-fork flag exposed via `StorageUtils.getEnergyLimitHardFork()` / `CommonParameter.ENERGY_LIMIT_HARD_FORK`, on chains where the fork has activated this code path is always taken by default, not a special node configuration). An attacker needs only to deploy an ordinary contract (no privileged role) and fund enough energy to cover N SSTOREs and M nested self-CALLs, both of which are attacker-controlled and can be tuned to maximize `M*N` within a feasible energy budget and the max call-depth limit. This is fully repeatable — the attacker can rebroadcast the same style of transaction repeatedly.

I was not able to confirm from the available context whether `ENERGY_LIMIT_HARD_FORK` is enabled by default on the target network at the current block height, or the exact numeric value of the current max TVM call-depth constant used to bound the `CALL` recursion depth; this would need to be verified directly in `CommonParameter`/`Program` before assigning a severity multiplier.

### Recommendation
Avoid re-copying the full `rowCache` on every nested call frame. Options: (1) use a copy-on-write / persistent-map data structure for `rowCache` so that child frames share unmodified entries with parents in O(1) and only diverge lazily on write; (2) charge additional energy proportional to `rowCache.size()` whenever a deep copy is performed (making the cost accounting faithful to the CPU work); or (3) memoize/reuse a single `Storage` object across the call stack for the same contract address instead of deep-copying it per frame, reconciling divergent writes only at commit/revert time.

### Proof of Concept
```java
// JUnit-style benchmark demonstrating multiplicative cost vs linear energy accounting
// (conceptual; to be run against RepositoryImpl/Storage in test harness with
// StorageUtils.getEnergyLimitHardFork() forced true)

@Test
public void testStorageDeepCopyCostScalesWithDepthTimesSlots() {
  byte[] address = randomAddress();
  StorageRowStore store = mockStorageRowStore();

  Storage base = new Storage(address, store);
  int N = 2000; // attacker-controlled SSTORE count
  for (int i = 0; i < N; i++) {
    base.put(new DataWord(i), new DataWord(i));
  }

  int M = 900; // attacker-controlled nested CALL depth (near TVM max depth)
  long start = System.nanoTime();
  Storage current = base;
  for (int depth = 0; depth < M; depth++) {
    current = new Storage(current); // simulates getStorage() deep copy per nested CALL frame
  }
  long elapsed = System.nanoTime() - start;

  // Energy charged for M CALLs + N SSTOREs is O(M+N);
  // assert that measured wall-clock cost grows much faster (super-linearly)
  // than O(M+N), demonstrating disproportionate CPU cost vs metered energy.
  assertTrue("Deep-copy cost should be multiplicative (M*N), disproportionate to linear energy charge",
      elapsed > /* baseline linear-cost threshold */ (long) (M + N) * SINGLE_OP_NANOS_THRESHOLD);
}
```
Real-world trigger: deploy a contract with a fallback/entry function that performs N `SSTORE`s on first invocation, then recursively `CALL`s itself M times (each nested call re-touching the same storage slots via `SLOAD`/`SSTORE`), and invoke it via `TriggerSmartContract` with an energy limit large enough to cover the linear opcode costs for M+N operations. Expected: node-side processing time for this single transaction is disproportionately large compared to equivalent M+N-cost transactions without storage sharing across deep call chains.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Storage.java (L35-44)
```java
  public Storage(Storage storage) {
    this.addrHash = storage.addrHash.clone();
    this.address = storage.getAddress().clone();
    this.store = storage.store;
    this.contractVersion = storage.contractVersion;
    storage.getRowCache().forEach((DataWord rowKey, StorageRowCapsule row) -> {
      StorageRowCapsule newRow = new StorageRowCapsule(row);
      this.rowCache.put(rowKey.clone(), newRow);
    });
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L702-728)
```java
  @Override
  public Storage getStorage(byte[] address) {
    Key key = Key.create(address);
    if (storageCache.containsKey(key)) {
      return storageCache.get(key);
    }
    Storage storage;
    if (this.parent != null) {
      Storage parentStorage = parent.getStorage(address);
      if (StorageUtils.getEnergyLimitHardFork()) {
        // deep copy
        storage = new Storage(parentStorage);
      } else {
        storage = parentStorage;
      }
    } else {
      storage = new Storage(address, getStorageRowStore());
    }
    ContractCapsule contract = getContract(address);
    if (contract != null) {
      storage.setContractVersion(contract.getContractVersion());
      if (!ByteUtil.isNullOrZeroArray(contract.getTrxHash())) {
        storage.generateAddrHash(contract.getTrxHash());
      }
    }
    return storage;
  }
```
