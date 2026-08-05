## Finding

### Title
Unsafe truncation in `DataWord.intValue()` silently wraps 256-bit values into a 32-bit `int` instead of the documented overflow check - ([File: common/src/main/java/org/tron/common/runtime/vm/DataWord.java])

### Summary
The reported Solidity issue is a classic unsafe downcast: a `uint256` value is narrowed to `uint128` via a raw cast with no bounds check, silently wrapping instead of reverting. The java-tron TVM has a structurally identical defect in `DataWord.intValue()`, which narrows a 256-bit EVM word down to a Java `int` (32 bits) via raw bit-shifting with no bounds check, despite its own Javadoc claiming it is safe.

### Finding Description
`DataWord` represents the 256-bit EVM word (analogous to Solidity's `uint256`). Its `intValue()` method is documented as:

```
Converts this DataWord to an int, checking for lost information. If this DataWord is out of the
possible range for an int result then an ArithmeticException is thrown.
``` [1](#0-0) 

But the actual implementation does not check anything - it iterates over all 32 bytes and repeatedly shifts/accumulates into a 32-bit `int`, which silently overflows (wraps) exactly like the Solidity `uint128(a + b)` downcast in the report: [2](#0-1) 

A safe variant, `intValueSafe()`, exists elsewhere and correctly clamps to `Integer.MAX_VALUE` when the value doesn't fit: [3](#0-2) 

However, `intValue()` (the unsafe one) is still used directly in security-sensitive TVM memory operations instead of `intValueSafe()`, e.g. in `Program.memorySave(DataWord, DataWord)`, `Program.memoryExpand`, and `Program.memoryLoad(DataWord)`: [4](#0-3) 

A caller passing an attacker-controlled 256-bit offset (e.g. `2^32` or `2^32 + N`) through `MSTORE`/`MLOAD` opcodes reaching this path would get the address silently wrapped mod `2^32` rather than triggering the expected out-of-memory / gas-based rejection, exactly mirroring how the Solidity report's `newSpotPrice`/`newDelta` silently wrapped instead of reverting.

### Impact Explanation
This breaks the safety invariant advertised by the method's own contract (throw on overflow) and could let a contract-supplied huge memory offset alias to an unintended small address instead of being rejected, potentially bypassing the intended memory-expansion gas accounting/bounds enforcement for those code paths that rely on `intValue()` rather than `intValueSafe()`. This is a state/accounting-integrity concern within the TVM analogous in root cause to the XykCurve unsafe downcast (Medium severity in the original report): a value that should be bounds-checked before narrowing is instead silently truncated.

### Likelihood Explanation
Reachable by any unprivileged smart-contract deployer/caller supplying crafted 256-bit stack values to opcodes that funnel through these `Program` methods; no privileged role is required. However, the actual exploitability depends on whether the opcode dispatch code (e.g., `MSTORE`/`MLOAD` handlers in `OperationActions`) pre-validates/clamps offsets via `intValueSafe()` or a gas-cost check (as seen in the hardened `EnergyCost`/`checkMemorySize` BigInteger paths used elsewhere, e.g. `VoteWitnessCost3Test`) before reaching `memorySave`/`memoryLoad`. This gating was not confirmed for the generic `MSTORE`/`MLOAD` opcodes within the available investigation, so likelihood should be treated as moderate/unconfirmed pending direct verification of the opcode call sites in `OperationActions`.

### Recommendation
- Fix `DataWord.intValue()` to match its own Javadoc: throw `ArithmeticException` when the value exceeds `Integer.MAX_VALUE` (mirroring `longValue()`'s stated contract), or
- Replace all unchecked `intValue()` calls in memory-address contexts (`Program.memorySave(DataWord,...)`, `Program.memoryExpand`, `Program.memoryLoad(DataWord)`) with the already-existing safe variant `intValueSafe()`, consistent with how `OperationActions` already does for `CALLDATACOPY`/`CODECOPY`/`EXTCODECOPY`.

### Proof of Concept
```java
// DataWord holding 2^32 (0x1_0000_0000), which does not fit in a signed 32-bit int
byte[] bytes = new byte[32];
bytes[27] = 1; // sets bit 32 -> value = 2^32
DataWord dw = new DataWord(bytes);

int truncated = dw.intValue();
// Javadoc claims: "throws ArithmeticException if this will not fit in an int"
// Actual behavior: truncated == 0 (silently wraps), no exception thrown
System.out.println(truncated); // prints 0
```
This demonstrates the same class of silent-overflow-on-narrowing defect described in the original report, now confirmed present in `DataWord.intValue()`. [5](#0-4) [4](#0-3)

### Citations

**File:** common/src/main/java/org/tron/common/runtime/vm/DataWord.java (L202-217)
```java
  /**
   * Converts this DataWord to an int, checking for lost information. If this DataWord is out of the
   * possible range for an int result then an ArithmeticException is thrown.
   *
   * @return this DataWord converted to an int.
   * @throws ArithmeticException - if this will not fit in an int.
   */
  public int intValue() {
    int intVal = 0;

    for (byte aData : data) {
      intVal = (intVal << 8) + (aData & 0xff);
    }

    return intVal;
  }
```

**File:** common/src/main/java/org/tron/common/runtime/vm/DataWord.java (L219-229)
```java
  /**
   * In case of int overflow returns Integer.MAX_VALUE otherwise works as #intValue()
   */
  public int intValueSafe() {
    int bytesOccupied = bytesOccupied();
    int intValue = intValue();
    if (bytesOccupied > 4 || intValue < 0) {
      return Integer.MAX_VALUE;
    }
    return intValue;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L396-427)
```java
  public void memorySave(DataWord addrB, DataWord value) {
    memory.write(addrB.intValue(), value.getData(), value.getData().length, false);
  }

  public void memorySave(int addr, byte[] value) {
    memory.write(addr, value, value.length, false);
  }

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

  public void memorySaveLimited(int addr, byte[] data, int dataSize) {
    memory.write(addr, data, dataSize, true);
  }

  public void memoryExpand(DataWord outDataOffs, DataWord outDataSize) {
    if (!outDataSize.isZero()) {
      memory.extend(outDataOffs.intValue(), outDataSize.intValue());
    }
  }

  public DataWord memoryLoad(DataWord addr) {
    return memory.readWord(addr.intValue());
  }
```
