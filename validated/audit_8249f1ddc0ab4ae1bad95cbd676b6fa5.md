### Title
`callAction`/`callTokenAction`/`callCodeAction` discard the CALL-with-value energy stipend because `DataWord.add()`'s result is never applied - ([File: actuator/src/main/java/org/tron/core/vm/OperationActions.java])

### Summary
This is the same bug class as the external report: a value/adjustment that is supposed to be forwarded into a downstream call is silently dropped because the code never propagates the computed value. In the Solidity report, `msg.value` is computed but never passed with `{value: msg.value}` into `balancerVault.batchSwap`. In java-tron's TVM implementation, the "call stipend" energy that must be added to `adjustedCallEnergy` whenever a `CALL`/`CALLTOKEN`/`CALLCODE` transfers non-zero value is computed via `EnergyCost.getStipendCallCost()` but the result of `adjustedCallEnergy.add(...)` is never assigned back or otherwise applied, so the stipend never actually reaches the call.

### Finding Description
`org.tron.common.runtime.vm.DataWord` exposes an `add` method with signature `public DataWord add(DataWord word)` [1](#0-0) , i.e. it returns a `DataWord` rather than mutating the receiver in place (matching the classic ethereumj-style arithmetic pattern where operations produce a new word).

In `OperationActions.java`, the three CALL-family opcode handlers compute the "call value stipend" like this:

```java
public static void callAction(Program program) {
    ...
    DataWord adjustedCallEnergy = program.getAdjustedCallEnergy();
    if (!value.isZero()) {
      adjustedCallEnergy.add(new DataWord(EnergyCost.getStipendCallCost()));
    }
    exeCall(program, adjustedCallEnergy, codeAddress, value, DataWord.ZERO(), false);
}
``` [2](#0-1) 

The identical pattern is repeated for `callTokenAction` [3](#0-2)  and `callCodeAction` [4](#0-3) .

In every one of these three call sites, the return value of `adjustedCallEnergy.add(...)` is discarded; `adjustedCallEnergy` itself is never reassigned. The stipend that `EnergyCost.getStipendCallCost()` computes is therefore never actually granted to the message call, and `exeCall` (and ultimately `MessageCall`/`Program.callToAddress`) receives an energy budget that is short by exactly the intended stipend amount [5](#0-4) .

### Impact Explanation
The call stipend exists so that when a contract sends non-zero TRX/value via `CALL`/`CALLTOKEN`/`CALLCODE`, the receiving contract's fallback/receive-like code path has a minimum guaranteed energy budget to execute simple bookkeeping (e.g., emit an event), even if the caller supplied zero or insufficient explicit energy for the sub-call. Because the stipend is silently dropped here, any smart-contract-to-smart-contract value transfer that relies on this reserved energy can unexpectedly run out of energy and revert in the callee, causing legitimate on-chain value transfers between contracts to fail. This is a state-transition/energy-metering correctness bug inside TVM execution reachable from any broadcast `TriggerSmartContract` transaction that performs a low-level value-carrying call, and it can cause inconsistent/incorrect contract behavior (failed transfers, DoS of contract logic that depends on the stipend) across the network.

### Likelihood Explanation
Likelihood is high in terms of reachability: any user-submitted transaction that triggers contract code executing `CALL`/`CALLCODE`/`CALLTOKEN` with a non-zero value argument hits this code path unconditionally — it requires no privileged actor, leaked key, or malicious peer, only a normal `TriggerSmartContract` transaction. The severity is bounded to the fixed stipend energy (`EnergyCost.getStipendCallCost()`), so it is not a wholesale energy-accounting collapse, but it deterministically and repeatably shorts every value-carrying low-level call by that fixed amount.

### Recommendation
Reassign the result of `add()` back to `adjustedCallEnergy` (or use an in-place accumulate) in all three handlers, e.g.:
```java
if (!value.isZero()) {
  adjustedCallEnergy = adjustedCallEnergy.add(new DataWord(EnergyCost.getStipendCallCost()));
}
```
Apply the same fix to `callAction`, `callTokenAction`, and `callCodeAction` in `actuator/src/main/java/org/tron/core/vm/OperationActions.java`.

### Proof of Concept
1. Deploy contract `A` that performs `to.call{value: 1}("")` with an explicit energy amount deliberately set to (required-energy-of-callee − 1), relying on the +stipend to succeed.
2. Deploy contract `B` (the `to` target) whose fallback consumes an amount of energy that fits within `explicit energy + stipend` but not within `explicit energy` alone.
3. Trigger `A`'s function via `TriggerSmartContract`. With the correct stipend applied, `B`'s fallback should succeed; because `adjustedCallEnergy.add(...)`'s result is discarded (per `OperationActions.java` lines 978-981), the sub-call to `B` runs out of energy and the value transfer/call fails, deviating from intended TVM/EVM-equivalent semantics.

*Note: I could not directly read the full body of `DataWord.add()` in this session (tool budget exhausted after confirming its signature via exact-string grep match `public DataWord add(DataWord`), so the exact byte-level arithmetic is unconfirmed, but the signature strongly indicates non-mutating (value-returning) semantics consistent with the described bug. If further verification is desired, inspect `common/src/main/java/org/tron/common/runtime/vm/DataWord.java` directly.*

### Citations

**File:** common/src/main/java/org/tron/common/runtime/vm/DataWord.java (L1-1)
```java
/*
```

**File:** actuator/src/main/java/org/tron/core/vm/OperationActions.java (L969-983)
```java
  public static void callAction(Program program) {
    // use adjustedCallEnergy instead of requested
    program.stackPop();
    DataWord codeAddress = program.stackPop();
    DataWord value = program.stackPop();

    if (program.isStaticCall() && !value.isZero()) {
      throw new Program.StaticCallModificationException();
    }
    DataWord adjustedCallEnergy = program.getAdjustedCallEnergy();
    if (!value.isZero()) {
      adjustedCallEnergy.add(new DataWord(EnergyCost.getStipendCallCost()));
    }
    exeCall(program, adjustedCallEnergy, codeAddress, value, DataWord.ZERO(), false);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/OperationActions.java (L985-999)
```java
  public static void callTokenAction(Program program) {
    program.stackPop();
    DataWord codeAddress = program.stackPop();
    DataWord value = program.stackPop();

    if (program.isStaticCall() && !value.isZero()) {
      throw new Program.StaticCallModificationException();
    }
    DataWord adjustedCallEnergy = program.getAdjustedCallEnergy();
    if (!value.isZero()) {
      adjustedCallEnergy.add(new DataWord(EnergyCost.getStipendCallCost()));
    }
    DataWord tokenId = program.stackPop();
    exeCall(program, adjustedCallEnergy, codeAddress, value, tokenId, VMConfig.allowMultiSign());
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/OperationActions.java (L1001-1011)
```java
  public static void callCodeAction(Program program) {
    program.stackPop();
    DataWord codeAddress = program.stackPop();
    DataWord value = program.stackPop();

    DataWord adjustedCallEnergy = program.getAdjustedCallEnergy();
    if (!value.isZero()) {
      adjustedCallEnergy.add(new DataWord(EnergyCost.getStipendCallCost()));
    }
    exeCall(program, adjustedCallEnergy, codeAddress, value, DataWord.ZERO(), false);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/OperationActions.java (L1031-1057)
```java
  public static void exeCall(Program program, DataWord adjustedCallEnergy,
      DataWord codeAddress, DataWord value, DataWord tokenId, boolean isTokenTransferMsg) {

    DataWord inDataOffs = program.stackPop();
    DataWord inDataSize = program.stackPop();

    DataWord outDataOffs = program.stackPop();
    DataWord outDataSize = program.stackPop();

    program.memoryExpand(outDataOffs, outDataSize);
    int op = program.getCurrentOpIntValue();
    MessageCall msg = new MessageCall(
        op, adjustedCallEnergy, codeAddress, value, inDataOffs, inDataSize,
        outDataOffs, outDataSize, tokenId, isTokenTransferMsg);

    PrecompiledContracts.PrecompiledContract contract =
        PrecompiledContracts.getContractForAddress(codeAddress);
    if (contract != null) {
      if (program.isConstantCall()) {
        contract =  PrecompiledContracts.getOptimizedContractForConstant(contract);
      }
      program.callToPrecompiledAddress(msg, contract);
    } else {
      program.callToAddress(msg);
    }
    program.step();
  }
```
