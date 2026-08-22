### Title
Unmetered quadratic-cost `Storage` deep copy on repeated CALLs causes memory-allocation cost far exceeding charged energy - ([File: actuator/src/main/java/org/tron/core/vm/program/Storage.java])

### Summary
`RepositoryImpl.getStorage()` performs a full deep copy of a contract's `Storage.rowCache` (via the `Storage(Storage storage)` copy constructor) every time a *new* child `Repository` first touches that contract's storage, and a new child `Repository` is created on every CALL/CALLCODE/DELEGATECALL/STATICCALL, independent of call depth. Because the cost of that copy scales with the current number of previously-SSTORE'd keys but the energy charged for CALL and SSTORE is a fixed, size-independent constant, a contract that alternates "add one storage slot" with "make one more self-CALL that touches storage" in a loop can force O(M²) heap allocations for only O(M) charged energy.

### Finding Description
- `Storage`'s copy constructor clones every entry of `rowCache` with `new StorageRowCapsule(row)` and `rowKey.clone()`, at cost proportional to the number of entries in the source `Storage`: [1](#0-0) 

- This deep copy is triggered from `RepositoryImpl.getStorage(address)` whenever the current repository (a per-call child repository) has not yet cached storage for `address`, and `StorageUtils.getEnergyLimitHardFork()` is enabled (this is the default/enforced hardfork behavior, not an operator-configurable setting an attacker needs): [2](#0-1) 

- A brand-new child `Repository` (with an empty `storageCache`) is created on **every** CALL opcode execution via `Program.callToAddress` → `getContractState().newRepositoryChild()`, regardless of whether the calls are nested or purely sequential (i.e., a loop of calls at constant depth, each returning before the next starts): [3](#0-2) 

- The energy charged for a CALL is computed by `EnergyCost.getCallCost` / `getCalculateCallCost`, based on a fixed base cost, memory-expansion cost, and value-transfer surcharge — none of which reflect the size of the callee's/self's accumulated `rowCache`: [4](#0-3) 

- SSTORE itself is charged a fixed per-operation cost independent of how many total keys the contract has already written, so an attacker can grow `rowCache` linearly in the number of SSTOREs performed, each at constant marginal energy cost.

**Exploit flow**: A contract loop does, at each of M iterations: (1) `SSTORE` a new unique key (fixed cost, `rowCache` size grows by 1), (2) CALL itself (fixed cost) so that the callee frame touches its own storage (e.g., `SLOAD`). Step (2) causes the new child repository's `getStorage()` to deep-copy the entire current `rowCache` (now of size ~i at iteration i). Summed over M iterations, the deep-copy work is O(1+2+...+M) = O(M²), while the energy charged for the same M iterations is O(M) (linear, fixed-cost operations). No existing check — `Program.getCallDeep() == MAX_DEPTH` (call-depth limiter), energy/bandwidth accounting, or `TransactionCapsule.validateSignature` — bounds this, because the loop keeps call depth constant (each call returns before the next starts) and energy accounting has no notion of the size of the object being cloned.

### Impact Explanation
This is a node-level Denial-of-Service: a single transaction, paid for at ordinary linear energy cost, can force the executing full node to perform quadratically many object allocations and copies (new `HashMap` entries, cloned byte arrays for `rowKey`/`rowValue` per `StorageRowCapsule`), leading to excessive CPU time and heap growth that is disproportionate to the energy fee paid by the attacker. Depending on how large M can be made within the transaction/block energy budget, this can produce significant transient memory pressure/GC pauses across all nodes executing the transaction (all full nodes and SR nodes that replay the block), potentially causing node stalls or OOM under adversarial parameterization. This maps to the "DoS via the TRON protocol implementation" bounty impact class.

### Likelihood Explanation
- Preconditions: none beyond being an ordinary account able to deploy and trigger a smart contract (`TriggerSmartContract`), which is the baseline unprivileged capability assumed in scope.
- Cost to attacker: bounded by the transaction's energy/fee limit; since each iteration's charged cost is fixed (a constant SSTORE cost + a constant CALL cost), the attacker pays only linearly in M for a quadratic memory/CPU cost imposed on the node — this asymmetry is exactly what makes it an attractive, cheap DoS vector.
- Feasibility: fully within existing opcodes (SSTORE + CALL) and standard TVM execution; requires no protocol-level or address-privilege bypass, and no dependence on non-default configuration (`StorageUtils.getEnergyLimitHardFork()` gates the vulnerable deep-copy path, but this is the intended/activated hardfork behavior, not an attacker-controlled toggle).
- Repeatable: any account can redeploy/retrigger this pattern repeatedly, in different blocks, amplifying aggregate load.

Uncertainty: I could not extract the exact numeric constants for `MAX_DEPTH`, per-block/per-tx energy limits, and the fixed SSTORE energy cost from the index in this session (only file-level match counts were returned, not line contents), so the concrete achievable value of M (and thus the real-world severity/memory magnitude) could not be precisely bounded here. The structural quadratic-cost-vs-linear-charge mismatch is clearly established, however, from the cited code paths.

### Recommendation
- Avoid unconditional full-map deep copies of `rowCache` on repository-child creation; instead adopt copy-on-write / layered lookup (e.g., only copy the specific row being accessed on demand, or use a persistent/immutable map structure with structural sharing) so that per-CALL storage access cost is O(1) amortized rather than O(current cache size).
- If a full copy is architecturally required, meter it: charge energy proportional to `rowCache.size()` at the time of copy (similar to `COPY_ENERGY` for memory copies) so cost matches actual work performed.
- Alternatively, cache/reuse already-copied `Storage` objects across sibling repositories for the same address within a transaction where semantically safe, to avoid repeated re-copying of the same growing structure.

### Proof of Concept
```java
// JUnit-style TVM integration test sketch (framework module),
// following the pattern of framework/src/test/java/org/tron/common/runtime/vm/StorageTest.java

@Test
public void testQuadraticStorageCopyDoS() {
    // Deploy contract with bytecode implementing:
    // for (i = 0; i < M; i++) {
    //   SSTORE(uniqueKey(i), value)   // grows this contract's rowCache by 1
    //   CALL(self, gas, ...)          // triggers new child Repository;
    //                                 // callee does SLOAD(0) to force getStorage() deep copy
    // }
    byte[] contractAddress = deployContract(loopSstoreThenSelfCallBytecode(M));

    long energyBefore = ...; // measure allocated energy for the call
    long heapBefore = Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory();

    TriggerSmartContract trigger = buildTrigger(contractAddress, /* fixed energyLimit */);
    long start = System.nanoTime();
    runtime.execute(trigger);
    long elapsed = System.nanoTime() - start;

    long heapAfter = Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory();
    long energyUsed = runtime.getResult().getEnergyUsed();

    // Assertion: allocation/time growth should be bounded linearly by energyUsed.
    // Vulnerability is confirmed if heapAfter-heapBefore (or elapsed time) grows
    // quadratically as M increases while energyUsed grows only linearly with M.
    Assert.assertTrue("Heap allocation should scale ~linearly with energy used",
        (heapAfter - heapBefore) <= LINEAR_BOUND_FACTOR * energyUsed);
}
```
Run this test for increasing M (e.g., M = 100, 500, 1000) and plot `energyUsed` vs. `elapsed`/heap growth; observe elapsed time/heap growth increasing quadratically in M while `energyUsed` increases linearly — demonstrating the unmetered quadratic cost rooted in `Storage(Storage storage)` at `actuator/src/main/java/org/tron/core/vm/program/Storage.java:35-44` combined with per-CALL repository creation in `RepositoryImpl.getStorage`.

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

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1000-1030)
```java
  public void callToAddress(MessageCall msg) {
    returnDataBuffer = null; // reset return buffer right before the call

    if (getCallDeep() == MAX_DEPTH) {
      stackPushZero();
      refundEnergy(msg.getEnergy().longValue(), " call deep limit reach");
      return;
    }

    byte[] data = memoryChunk(msg.getInDataOffs().intValue(), msg.getInDataSize().intValue());

    // FETCH THE SAVED STORAGE
    byte[] codeAddress = msg.getCodeAddress().toTronAddress();
    byte[] senderAddress = getContextAddress();

    byte[] contextAddress;
    if (msg.getOpCode() == Op.CALLCODE || msg.getOpCode() == Op.DELEGATECALL) {
      contextAddress = senderAddress;
    } else {
      contextAddress = codeAddress;
    }

    if (logger.isDebugEnabled()) {
      logger.debug(Op.getNameOf(msg.getOpCode())
              + " for existing contract: address: [{}], outDataOffs: [{}], outDataSize: [{}]  ",
          Hex.toHexString(contextAddress), msg.getOutDataOffs().longValue(),
          msg.getOutDataSize().longValue());
    }

    Repository deposit = getContractState().newRepositoryChild();

```

**File:** actuator/src/main/java/org/tron/core/vm/EnergyCost.java (L441-537)
```java
  public static long getCallCost(Program program) {
    Stack stack = program.getStack();
    // here, contract call an other contract, or a library, and so on
    long energyCost = CALL_ENERGY;
    DataWord callAddressWord = stack.get(stack.size() - 2);
    DataWord value = stack.get(stack.size() - 3);
    int opOff = 4;
    //check to see if account does not exist and is not a precompiled contract
    if (!value.isZero()) {
      energyCost += VT_CALL;
      if (isDeadAccount(program, callAddressWord)) {
        energyCost += NEW_ACCT_CALL;
      }
    }
    return getCalculateCallCost(stack, program, energyCost, opOff);
  }

  public static long getStaticCallCost(Program program) {
    Stack stack = program.getStack();
    long energyCost = CALL_ENERGY;
    int opOff = 3;
    return getCalculateCallCost(stack, program, energyCost, opOff);
  }

  public static long getDelegateCallCost(Program program) {
    Stack stack = program.getStack();
    long energyCost = CALL_ENERGY;
    int opOff = 3;
    return getCalculateCallCost(stack, program, energyCost, opOff);
  }

  public static long getCallCodeCost(Program program) {
    Stack stack = program.getStack();
    long energyCost = CALL_ENERGY;
    DataWord value = stack.get(stack.size() - 3);
    int opOff = 4;
    if (!value.isZero()) {
      energyCost += VT_CALL;
    }
    return getCalculateCallCost(stack, program, energyCost, opOff);
  }

  public static long getCallTokenCost(Program program) {
    Stack stack = program.getStack();
    long energyCost = CALL_ENERGY;
    DataWord callAddressWord = stack.get(stack.size() - 2);
    DataWord value = stack.get(stack.size() - 3);
    int opOff = 5;
    //check to see if account does not exist and is not a precompiled contract
    if (!value.isZero()) {
      energyCost += VT_CALL;
      if (isDeadAccount(program, callAddressWord)) {
        energyCost += NEW_ACCT_CALL;
      }
    }
    return getCalculateCallCost(stack, program, energyCost, opOff);
  }

  public static long getCalculateCallCost(Stack stack, Program program,
                                          long energyCost, int opOff) {
    int op = program.getCurrentOpIntValue();
    long oldMemSize = program.getMemSize();
    DataWord callEnergyWord = stack.get(stack.size() - 1);
    // in offset+size
    BigInteger in = memNeeded(stack.get(stack.size() - opOff),
        stack.get(stack.size() - opOff - 1));
    // out offset+size
    BigInteger out = memNeeded(stack.get(stack.size() - opOff - 2),
        stack.get(stack.size() - opOff - 3));
    energyCost += calcMemEnergy(oldMemSize, in.max(out),
        0, op);

    if (VMConfig.allowDynamicEnergy()) {
      long factor = program.getContextContractFactor();
      if (factor > DYNAMIC_ENERGY_FACTOR_DECIMAL) {
        long penalty = energyCost * factor / DYNAMIC_ENERGY_FACTOR_DECIMAL - energyCost;
        if (penalty < 0) {
          penalty = 0;
        }
        program.setCallPenaltyEnergy(penalty);
        energyCost += penalty;
      }
    }

    if (energyCost > program.getEnergyLimitLeft().longValueSafe()) {
      throw new Program.OutOfEnergyException(
          "Not enough energy for '%s' operation executing: opEnergy[%d], programEnergy[%d]",
          Op.getNameOf(op),
          energyCost, program.getEnergyLimitLeft().longValueSafe());
    }
    DataWord getEnergyLimitLeft = program.getEnergyLimitLeft().clone();
    getEnergyLimitLeft.sub(new DataWord(energyCost));

    DataWord adjustedCallEnergy = program.getCallEnergy(callEnergyWord, getEnergyLimitLeft);
    program.setAdjustedCallEnergy(adjustedCallEnergy);
    energyCost += adjustedCallEnergy.longValueSafe();
    return energyCost;
```
