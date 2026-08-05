### Title
Silent 256-bit `DataWord` wraparound in `getVoteWitnessCost2` allows underpriced/mismatched energy charge for `VOTEWITNESS` versus `getVoteWitnessCost3` - (File: actuator/src/main/java/org/tron/core/vm/EnergyCost.java)

### Summary
`getVoteWitnessCost2` computes the required memory size for `VOTEWITNESS` using `DataWord.mul`/`add`, which operate modulo 2^256 and silently wrap around, whereas `getVoteWitnessCost3` (gated by `VMConfig.allowTvmOsaka()`) performs the identical calculation with exact `BigInteger` arithmetic. An attacker who fully controls the `witnessArrayLength`/`amountArrayLength` and offset stack operands can pick a length such that `length*32 + 32 (mod 2^256)` becomes a tiny value, causing `memNeeded`/`checkMemorySize` to see a trivially small memory requirement and charge only the base `VOTE_WITNESS` cost, while the same claimed length would be rejected (via `Program.Exception.memoryOverflow`) or charged enormous energy under the exact-arithmetic `getVoteWitnessCost3` path.

### Finding Description
`getVoteWitnessCost2` clones the stack `DataWord` operands and mutates them in place with 256-bit wraparound semantics: [1](#0-0) 
Because `DataWord.mul`/`add` truncate to 32 bytes, an attacker can select `witnessArrayLength` (or `amountArrayLength`) near a multiple of 2^256/32 so that `(length * 32 + 32) mod 2^256` collapses to a small residue. Combined with an attacker-chosen offset of `0`, `memNeeded(offset, wrappedSize)` in [2](#0-1) 
returns a small `BigInteger`, so `checkMemorySize`/`calcMemEnergy` never trips the 3MB `MEM_LIMIT` guard: [3](#0-2) 
The op is then charged only `VOTE_WITNESS` (30000) plus negligible memory expansion cost.

In contrast, `getVoteWitnessCost3` performs the same computation with unbounded `BigInteger` values (no modulo), so the identical crafted length yields a genuinely enormous `amountArrayMemoryNeeded`/`witnessArrayMemoryNeeded`, which trips `checkMemorySize` and throws `Program.Exception.memoryOverflow`, rejecting the transaction outright: [4](#0-3) 

The dispatch between these cost functions is controlled purely by hard-fork flags — `getVoteWitnessCost3` falls back to `getVoteWitnessCost2` when `!VMConfig.allowTvmOsaka()`, and `getVoteWitnessCost2` falls back to `getVoteWitnessCost` (also DataWord-based and equally susceptible to wraparound, minus the `+wordSize` term) when `!VMConfig.allowEnergyAdjustment()`: [5](#0-4) [6](#0-5) 

I was not able to directly inspect the body of `Program.voteWitness()` in this session (only match counts for `voteWitness`/`VOTEWITNESS` in `actuator/src/main/java/org/tron/core/vm/program/Program.java` were returned, not source lines), so I cannot fully confirm from source whether `Program.voteWitness()` re-validates `witnessArrayLength`/`amountArrayLength` independently of the cost function before calling `memoryLoad`/`memoryChunk`. In every other opcode in this file (`SHA3`, `CODECOPY`, `RETURNDATACOPY`, `LOG`, `CREATE`, etc.) the cost-calculation function is the sole gate that calls `checkMemorySize`, and the opcode's execution logic trusts that the already-computed cost implies a bounded/safe memory size — this is the standard TVM pattern in this codebase. If `Program.voteWitness()` follows this same pattern (no independent bound check before reading the array), then under `getVoteWitnessCost2` an attacker-crafted length that wraps to a cheap cost would proceed to actual execution with the real (unwrapped) enormous length, either throwing an uncontrolled exception deep in memory access/array construction (potential `OutOfMemoryError`/crash) or looping over an attacker-controlled huge bound, while the same transaction would have been cleanly rejected at the cost-calculation stage under `getVoteWitnessCost3` on `allowTvmOsaka()`-enabled nodes.

### Impact Explanation
If confirmed against `Program.voteWitness()`, this is a determinism/cost-integrity issue: nodes running with `allowTvmOsaka()==false` (older hard-fork state) would charge a fixed cheap base cost for a `VOTEWITNESS` call while attempting to process an attacker-claimed astronomically large array, risking a crash/DoS (`OutOfMemoryError`, unbounded loop) or, at minimum, materially underpriced computation relative to the exact-arithmetic path used post-Osaka. This also creates a behavioral divergence between nodes on different hard-fork configurations processing the identical transaction — pre-Osaka nodes would proceed past cost-checking (and potentially crash or hang) while post-Osaka nodes would cleanly reject via `memoryOverflow`, which is a consensus/availability concern only if both configurations are simultaneously live in the network (i.e., during the hard-fork transition window that `VMConfig.allowTvmOsaka()` is designed to gate).

### Likelihood Explanation
The precondition is straightforward for an unprivileged attacker: craft a contract call to a bytecode sequence invoking the `VOTEWITNESS` opcode with attacker-chosen stack values for `witnessArrayLength`/`amountArrayLength` and offsets, broadcast as a normal transaction. No special privileges are required. The exploit requires the network to be running in a state where `allowTvmOsaka()==false` (i.e., pre-Osaka hard fork, or during a network with mixed hard-fork adoption), which limits the exploitation window to older/transitional deployments rather than a fully upgraded network. Constructing the specific wraparound value is deterministic arithmetic (`length` chosen so `(length*32+32) mod 2^256` is small), fully repeatable, and requires no race conditions.

### Recommendation
Replace the `DataWord.mul`/`add` calls in `getVoteWitnessCost` and `getVoteWitnessCost2` with exact `BigInteger` arithmetic (as already done in `getVoteWitnessCost3`), removing the hard-fork gate around exact-arithmetic array-length checks, or alternatively bound-check `witnessArrayLength`/`amountArrayLength` against a sane maximum (e.g., derived from `MEM_LIMIT`) before performing the `DataWord` multiplication, in every one of `getVoteWitnessCost`, `getVoteWitnessCost2`, and `getVoteWitnessCost3`. Additionally, verify (and if necessary add) an independent bound check inside `Program.voteWitness()` prior to any `memoryLoad`/`memoryChunk` call, so that opcode execution never trusts a wrapped-around cost estimate.

### Proof of Concept
```java
// Differential test: EnergyCost.getVoteWitnessCost2 vs getVoteWitnessCost3
// Location suggestion: framework/src/test/java/org/tron/common/runtime/vm/VoteWitnessCost3Test.java

@Test
public void testVoteWitnessCostWraparoundDivergence() {
  // Choose witnessArrayLength L such that (L * 32 + 32) mod 2^256 == 0
  // i.e. L = (2^256 / 32) - 1  (so L*32 = 2^256 - 32, +32 wraps to 0)
  BigInteger wordSize = BigInteger.valueOf(DataWord.WORD_SIZE); // 32
  BigInteger modulus = BigInteger.TWO.pow(256);
  BigInteger craftedLength = modulus.divide(wordSize).subtract(BigInteger.ONE);

  // Build a Program/Stack with:
  // witnessArrayOffset = 0, witnessArrayLength = craftedLength (wraps to 0 in DataWord math)
  // amountArrayOffset  = 0, amountArrayLength  = craftedLength (same)
  Program programOsakaOff = buildProgramWithVoteWitnessStack(craftedLength, BigInteger.ZERO);
  Program programOsakaOn  = buildProgramWithVoteWitnessStack(craftedLength, BigInteger.ZERO);

  VMConfig.initAllowTvmOsaka(0); // simulate pre-Osaka: allowTvmOsaka() == false
  long cost2 = EnergyCost.getVoteWitnessCost2(programOsakaOff);
  // Expect: cost2 == VOTE_WITNESS (30000) due to wraparound producing memNeeded == 0

  VMConfig.initAllowTvmOsaka(1); // simulate Osaka: allowTvmOsaka() == true
  boolean threw = false;
  try {
    EnergyCost.getVoteWitnessCost3(programOsakaOn);
  } catch (Program.Exception e) {
    threw = true; // expect memoryOverflow due to exact BigInteger computation
  }

  // Assertion: the two configurations diverge for the identical crafted stack input -
  // cost2 silently succeeds with cheap cost, cost3 throws memoryOverflow.
  assertEquals(30000L, cost2);
  assertTrue("Cost3 should reject the crafted huge array length via memoryOverflow", threw);
}
```
Expected result: the test demonstrates that identical maximal `DataWord` array-length stack inputs produce divergent outcomes — `getVoteWitnessCost2` returns a cheap fixed cost (30000) due to `DataWord` wraparound, while `getVoteWitnessCost3` throws `Program.Exception.memoryOverflow`, confirming the invariant violation described. A follow-up integration test should invoke `Program.voteWitness()` directly with the same crafted stack under `allowTvmOsaka()==false` to confirm whether execution proceeds to `memoryLoad`/`memoryChunk` with the unwrapped (real) huge length, to fully validate the downstream DoS/crash impact.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/EnergyCost.java (L346-365)
```java
  public static long getVoteWitnessCost(Program program) {
    Stack stack = program.getStack();
    long oldMemSize = program.getMemSize();
    DataWord amountArrayLength = stack.get(stack.size() - 1).clone();
    DataWord amountArrayOffset = stack.get(stack.size() - 2);
    DataWord witnessArrayLength = stack.get(stack.size() - 3).clone();
    DataWord witnessArrayOffset = stack.get(stack.size() - 4);

    DataWord wordSize = new DataWord(DataWord.WORD_SIZE);

    amountArrayLength.mul(wordSize);
    BigInteger amountArrayMemoryNeeded = memNeeded(amountArrayOffset, amountArrayLength);

    witnessArrayLength.mul(wordSize);
    BigInteger witnessArrayMemoryNeeded = memNeeded(witnessArrayOffset, witnessArrayLength);

    return VOTE_WITNESS + calcMemEnergy(oldMemSize,
        (amountArrayMemoryNeeded.compareTo(witnessArrayMemoryNeeded) > 0
            ? amountArrayMemoryNeeded : witnessArrayMemoryNeeded), 0, Op.VOTEWITNESS);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/EnergyCost.java (L367-378)
```java
  public static long getVoteWitnessCost2(Program program) {
    if (!VMConfig.allowEnergyAdjustment()) {
      return getVoteWitnessCost(program);
    }

    Stack stack = program.getStack();
    long oldMemSize = program.getMemSize();
    DataWord amountArrayLength = stack.get(stack.size() - 1).clone();
    DataWord amountArrayOffset = stack.get(stack.size() - 2);
    DataWord witnessArrayLength = stack.get(stack.size() - 3).clone();
    DataWord witnessArrayOffset = stack.get(stack.size() - 4);

```

**File:** actuator/src/main/java/org/tron/core/vm/EnergyCost.java (L379-392)
```java
    DataWord wordSize = new DataWord(DataWord.WORD_SIZE);

    amountArrayLength.mul(wordSize);
    amountArrayLength.add(wordSize); // dynamic array length is at least 32 bytes
    BigInteger amountArrayMemoryNeeded = memNeeded(amountArrayOffset, amountArrayLength);

    witnessArrayLength.mul(wordSize);
    witnessArrayLength.add(wordSize); // dynamic array length is at least 32 bytes
    BigInteger witnessArrayMemoryNeeded = memNeeded(witnessArrayOffset, witnessArrayLength);

    return VOTE_WITNESS + calcMemEnergy(oldMemSize,
        (amountArrayMemoryNeeded.compareTo(witnessArrayMemoryNeeded) > 0
            ? amountArrayMemoryNeeded : witnessArrayMemoryNeeded), 0, Op.VOTEWITNESS);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/EnergyCost.java (L394-417)
```java
  public static long getVoteWitnessCost3(Program program) {
    if (!VMConfig.allowTvmOsaka()) {
      return getVoteWitnessCost2(program);
    }

    Stack stack = program.getStack();
    long oldMemSize = program.getMemSize();
    BigInteger amountArrayLength = stack.get(stack.size() - 1).value();
    BigInteger amountArrayOffset = stack.get(stack.size() - 2).value();
    BigInteger witnessArrayLength = stack.get(stack.size() - 3).value();
    BigInteger witnessArrayOffset = stack.get(stack.size() - 4).value();

    BigInteger wordSize = BigInteger.valueOf(DataWord.WORD_SIZE);

    BigInteger amountArraySize = amountArrayLength.multiply(wordSize).add(wordSize);
    BigInteger amountArrayMemoryNeeded = memNeeded(amountArrayOffset, amountArraySize);

    BigInteger witnessArraySize = witnessArrayLength.multiply(wordSize).add(wordSize);
    BigInteger witnessArrayMemoryNeeded = memNeeded(witnessArrayOffset, witnessArraySize);

    return VOTE_WITNESS + calcMemEnergy(oldMemSize,
        (amountArrayMemoryNeeded.compareTo(witnessArrayMemoryNeeded) > 0
            ? amountArrayMemoryNeeded : witnessArrayMemoryNeeded), 0, Op.VOTEWITNESS);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/EnergyCost.java (L549-576)
```java
  private static long calcMemEnergy(long oldMemSize, BigInteger newMemSize,
                             long copySize, int op) {
    long energyCost = 0;

    checkMemorySize(op, newMemSize);

    // memory SUN consume calc
    long memoryUsage = (newMemSize.longValueExact() + 31) / 32 * 32;
    if (memoryUsage > oldMemSize) {
      long memWords = (memoryUsage / 32);
      long memWordsOld = (oldMemSize / 32);
      long memEnergy = (MEMORY * memWords + memWords * memWords / 512)
          - (MEMORY * memWordsOld + memWordsOld * memWordsOld / 512);
      energyCost += memEnergy;
    }

    if (copySize > 0) {
      long copyEnergy = COPY_ENERGY * ((copySize + 31) / 32);
      energyCost += copyEnergy;
    }
    return energyCost;
  }

  private static void checkMemorySize(int op, BigInteger newMemSize) {
    if (newMemSize.compareTo(MEM_LIMIT) > 0) {
      throw Program.Exception.memoryOverflow(op);
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/EnergyCost.java (L578-580)
```java
  private static BigInteger memNeeded(DataWord offset, DataWord size) {
    return size.isZero() ? BigInteger.ZERO : offset.value().add(size.value());
  }
```
