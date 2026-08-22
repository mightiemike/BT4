## Title
Gas griefing / DoS via unbounded precompiled-contract return-data copy in `Program.callToPrecompiledAddress` - (File: `actuator/src/main/java/org/tron/core/vm/program/Program.java`)

### Summary
When the `AllowTvmSelfdestructRestriction` hard-fork flag is not yet active, `Program.callToPrecompiledAddress` copies the *entire* byte array returned by a precompiled contract into VM memory, ignoring the `outDataSize` that was specified (and paid for) by the calling opcode's `CALL`/`STATICCALL`/`DELEGATECALL` arguments. Because the energy charged for a call is computed from the caller-declared `outDataSize` (via `EnergyCost.getCalculateCallCost`), a contract can request a tiny `outDataSize`, but the actual memory write can be arbitrarily large (bounded only by the attacker-supplied calldata to the `Identity` precompile at address `0x04`, which echoes its input back unchanged). This is the same root cause as the reported Solidity issue: return data whose size is not bounded by what the caller paid for gets copied into memory, letting a caller/attacker force cheap allocation-then-expensive-copy work on the executing node/relayer.

### Finding Description
The relevant call path is:
1. `OperationActions.exeCall` pops `inDataOffs/inDataSize/outDataOffs/outDataSize` from the stack and expands memory only for `outDataOffs/outDataSize` via `program.memoryExpand(outDataOffs, outDataSize)`. [1](#0-0) 
2. Energy for the call, including the memory-expansion component, is computed from the same declared `in`/`out` offset+size pair in `EnergyCost.getCalculateCallCost`/`calcMemEnergy` — i.e., cost tracks the caller's *declared* `outDataSize`, not the actual bytes that will be written later. [2](#0-1) 
3. If the callee resolves to a precompiled contract, `Program.callToPrecompiledAddress` executes it and then writes the result to memory. When `VMConfig.allowTvmSelfdestructRestriction()` is `false`, it calls `this.memorySave(msg.getOutDataOffs().intValue(), out.getRight())`, which uses `memorySave(int addr, int allocSize, byte[] value)` semantics via `memory.extendAndWrite(addr, allocSize, value)` — copying and extending memory to fit the **full** `out.getRight()` buffer, not the requested `outDataSize`. [3](#0-2) [4](#0-3) 
4. The `Identity` precompile at address `0x04` returns its input unchanged, with size fully controlled by the caller/attacker: `execute(byte[] data) { return Pair.of(true, data); }`. Energy for the *execution itself* scales with the data size (`15 + words*3`), but this energy check happens once, and the subsequent unconditional `memorySave` still forces a memory extension proportional to the returned data size, independent of the small `outDataSize`/`in`/`out` figure that was priced into `getCalculateCallCost`. [5](#0-4) 

The `allowTvmSelfdestructRestriction()` flag is a hard-fork proposal switch defaulting to inactive until turned on by committee proposal, meaning any deployment (private/testnet chains, or the window before the corresponding mainnet proposal activates) executes the vulnerable branch. [6](#0-5) 

This mirrors the reported bug class: memory allocation/copy cost for returned call data is not properly bounded by what the caller declared and paid for, so a contract invoking a precompile with a large `Identity` payload but a tiny declared `outDataSize` gets an under-priced, outsized memory copy performed by the node executing the transaction (the relayer/validator running the TVM).

### Impact Explanation
Any node (full node, SR, or a service relaying `TriggerSmartContract` transactions on behalf of users) that executes such a contract call pays real CPU/memory cost for the oversized copy that was not proportionally charged in energy, causing resource exhaustion / DoS pressure on the executing/validating node — a "gas griefing" scenario analogous to the reported issue, rated Medium because it has no direct economic upside for the attacker but degrades node performance and can be repeated cheaply while the fork flag is inactive.

### Likelihood Explanation
Exploitation requires only a standard `TriggerSmartContract` transaction calling a contract that issues a `CALL`/`STATICCALL` to the `Identity` precompile (`0x04`) with large `inDataSize` and small `outDataSize`, on a chain/network where `AllowTvmSelfdestructRestriction` has not been activated (default state for new/private chains and possibly during the pre-activation window on public networks). No privileged access, leaked keys, or malicious peers are required — it is directly reachable from an unprivileged smart-contract call.

### Recommendation
Remove or retire the legacy branch in `Program.callToPrecompiledAddress` that copies unbounded return data; always bound the memory write to `msg.getOutDataSize()` regardless of the `allowTvmSelfdestructRestriction` flag, matching the already-fixed `memorySave(offset, size, out.getRight())` path, and ensure the energy cost model in `EnergyCost.getCalculateCallCost` always matches the amount of data actually written to memory.

### Proof of Concept
1. Deploy a contract `C` with a function that does: `staticcall(gas(), 0x04, in_ptr, LARGE_SIZE, out_ptr, 0)` — i.e., call the `Identity` precompile with a large input (`LARGE_SIZE`, e.g. hundreds of KB via calldata) but request `outDataSize = 0`.
2. Ensure the target chain/test node has not activated `AllowTvmSelfdestructRestriction` (default `false` unless a proposal set it). [6](#0-5) 
3. Send a `TriggerSmartContract` transaction invoking `C`; observe that `Program.callToPrecompiledAddress` still writes the full `LARGE_SIZE` buffer to memory via `memorySave(offset, out.getRight())` regardless of the requested `outDataSize`, while the energy pre-charged for the call's memory expansion in `EnergyCost.getCalculateCallCost` was computed from the declared `(outOffset, outSize=0)` pair. [3](#0-2) [7](#0-6) 
4. Measure actual CPU/memory work performed by the executing node versus the energy actually billed to confirm the under-charged memory-expansion cost.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/OperationActions.java (L1034-1045)
```java
    DataWord inDataOffs = program.stackPop();
    DataWord inDataSize = program.stackPop();

    DataWord outDataOffs = program.stackPop();
    DataWord outDataSize = program.stackPop();

    program.memoryExpand(outDataOffs, outDataSize);
    int op = program.getCurrentOpIntValue();
    MessageCall msg = new MessageCall(
        op, adjustedCallEnergy, codeAddress, value, inDataOffs, inDataSize,
        outDataOffs, outDataSize, tokenId, isTokenTransferMsg);

```

**File:** actuator/src/main/java/org/tron/core/vm/EnergyCost.java (L499-511)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L404-413)
```java
  /**
   * . Allocates a piece of memory and stores value at given offset address
   *
   * @param addr is the offset address
   * @param allocSize size of memory needed to write
   * @param value the data to write to memory
   */
  public void memorySave(int addr, int allocSize, byte[] value) {
    memory.extendAndWrite(addr, allocSize, value);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1757-1762)
```java
      if (VMConfig.allowTvmSelfdestructRestriction()) {
        this.memorySave(msg.getOutDataOffs().intValueSafe(), msg.getOutDataSize().intValueSafe(), out.getRight());
      } else {
        this.memorySave(msg.getOutDataOffs().intValue(), out.getRight());
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L504-524)
```java
  public static class Identity extends PrecompiledContract {

    public Identity() {
    }

    @Override
    public long getEnergyForData(byte[] data) {

      // energy charge for the execution:
      // minimum 1 and additional 1 for each 32 bytes word (round  up)
      if (data == null) {
        return 15;
      }
      return 15L + (data.length + 31) / 32 * 3;
    }

    @Override
    public Pair<Boolean, byte[]> execute(byte[] data) {
      return Pair.of(true, data);
    }
  }
```

**File:** common/src/main/java/org/tron/core/vm/config/VMConfig.java (L303-305)
```java
  public static boolean allowTvmSelfdestructRestriction() {
    return current().allowTvmSelfdestructRestriction;
  }
```
