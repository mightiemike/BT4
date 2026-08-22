I have sufficient evidence to identify a concrete analog of the reported bug class in java-tron's `ValidateMultiSign` precompile.

### Title
Unguarded exception in `ValidateMultiSign` precompile decoder aborts the entire calling transaction instead of gracefully failing the CALL - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The Kakarot report describes a class of bug where a call to a sub-contract that "panics" is not gracefully handled by the caller, so the panic bubbles up and aborts the entire outer execution context instead of just failing the sub-call. java-tron has a structurally identical bug in the `ValidateMultiSign` precompiled contract (address `0x...0a`): when a caller contract invokes it via `CALL` with malformed/undersized calldata (fully attacker-controlled), the calldata-decoding logic executes *before* the internal `try/catch`, throws an uncaught `RuntimeException`, and that exception propagates out of the precompile, through `Program.callToPrecompiledAddress()` (which has no surrounding try/catch around `contract.execute(data)`), into the enclosing `VM.play()` loop of the *calling* frame itself (precompile calls do not spawn a nested `Program`), aborting the whole transaction rather than the EVM-standard behavior of simply pushing `0` for a failed sub-call.

### Finding Description
`PrecompiledContracts.ValidateMultiSign.execute()` decodes the raw calldata unconditionally before any error containment: [1](#0-0) 

Only the account/permission-validation logic (lines 1082-1117) is wrapped in a `try { ... } catch (Throwable t)`. The decoding steps — `DataWord.parseArray(rawData)`, `words[0]`, `words[1]`, `words[2]`, `words[3]`, and `extractBytesArray`/`extractSigArray` — are executed *outside* that try block. If `rawData` is too short or misaligned (e.g., not a multiple of 32 bytes, or fewer than 5 head words), these calls throw an unguarded `ArrayIndexOutOfBoundsException` or similar `RuntimeException`.

The `TIP-854` fix added a guard, `isValidAbiEncoding()`, but it is only consulted `if (VMConfig.allowTvmOsaka())` — i.e., gated behind a chain-parameter hard-fork flag: [2](#0-1) 

Before `allowTvmOsaka` is activated network-wide, the decoder is fully unguarded, and the codebase's own test suite explicitly documents this as the "legacy behaviour" that the fix does not change pre-activation: [3](#0-2) 

When `contract.execute(data)` throws, `Program.callToPrecompiledAddress()` has no try/catch around the call: [4](#0-3) 

Because precompiled-contract invocations execute *inline* within the calling program's bytecode interpretation loop (unlike normal contract-to-contract `CALL`s, which spin up a nested `Program`/`VM.play()` in `callToAddress()`), the exception is caught by the *same* `VM.play()` try/catch that wraps every opcode of the calling contract: [5](#0-4) 

That handler spends all remaining energy, stops the program, and rethrows — which propagates to `VMActuator.execute()`'s outer catch block, causing the **entire transaction** to fail with an exception rather than merely causing the failed `CALL` to push `0` and let the caller continue, as standard EVM semantics require: [6](#0-5) 

This is precisely the reported bug class: an unhandled panic/exception in an invoked sub-contract (here, a precompile playing the role Starknet's `call_contract`/DualVmToken sub-call plays in the original report) bubbles up and aborts the whole calling context instead of being contained to the failing sub-call.

By contrast, `BatchValidateSign` (address `0x...09`) wraps its entire `doExecute()` — including calldata decoding — in an outer `try/catch(Throwable)` in `execute()`, so it does not suffer from this issue: [7](#0-6) 

### Impact Explanation
Any smart contract can be crafted (and deployed/called by any unprivileged account) to perform a `CALL`/`STATICCALL`/`DELEGATECALL` to precompile address `0x...0a` (`ValidateMultiSign`) with calldata that is too short or misaligned. Instead of the call failing gracefully (returning `0` on the stack per EVM semantics), the entire transaction — including any unrelated logic performed by the caller before or after that `CALL` — reverts/aborts with an exception and consumes all provided energy. This breaks expected EVM composability/availability guarantees (analogous to the "RPC-level revert" impact in the original report): contracts that defensively wrap external calls (e.g., "excessively safe call" patterns) cannot protect themselves against this failure mode, since the failure occurs before the call even returns control to the caller's bytecode.

### Likelihood Explanation
Triggering this requires nothing more than a standard `TriggerSmartContract` transaction from any account to a contract (which the attacker fully controls) that issues a `CALL` to the fixed precompile address with a short/misaligned payload. It is reachable pre-hard-fork (while `allowTvmOsaka` is not yet activated) with 100% reliability and no special privileges, race conditions, or preconditions beyond depositing enough energy for the transaction.

### Recommendation
Move the calldata-shape validation (equivalent to `isValidAbiEncoding`) in `ValidateMultiSign.execute()` outside of the `allowTvmOsaka` gate so that malformed/undersized calldata is rejected unconditionally (returning `(false, EMPTY_BYTE_ARRAY)` or `(true, DATA_FALSE)`) before any decoding is attempted, regardless of hard-fork activation status. Alternatively, wrap the entire body of `execute()` (including the `DataWord.parseArray` and word-indexing steps) in the same `try/catch(Throwable)` construct already used by `BatchValidateSign.execute()`, ensuring any decode-time exception is converted into a graceful `Pair.of(true, DATA_FALSE)` result rather than propagating out of the precompile.

### Proof of Concept
1. Deploy a simple contract `Attacker` containing:
   ```
   function trigger() public {
       address target = address(0x000000000000000000000000000000000000000a); // ValidateMultiSign precompile
       bytes memory shortData = new bytes(32); // 1 word: far fewer than the 5-word head the decoder assumes
       (bool ok, ) = target.call(shortData);
       // Expected (per EVM semantics): ok == false, execution continues here.
       // Actual (pre-TIP-854-activation): the call never returns; the whole tx reverts/aborts.
   }
   ```
2. Broadcast a `TriggerSmartContract` transaction calling `Attacker.trigger()` on a network/chain where `allowTvmOsaka` has not been activated.
3. Observe that the transaction's `resultCode` reflects the entire tx failing with an exception and all energy consumed, rather than `trigger()` executing to completion with `ok == false`. This is directly evidenced by the test `ValidateMultiSignContractTest.testTip854PreActivationNoOp` (lines 248-260), which documents that pre-activation the decoder "may throw" and this is "existing behaviour," and by `OperationsTest.testTip854OuterFrameContainment` (lines 857-898), which specifically asserts (only once `allowTvmOsaka` is enabled) that the outer frame must not inherit the exception — confirming that pre-activation, it does.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L432-438)
```java
  private static boolean isValidAbiEncoding(byte[] data, int headerWords, int itemWords) {
    if (data == null || data.length % WORD_SIZE != 0) {
      return false;
    }
    long tail = subtractExact(data.length, multiplyExact(headerWords, WORD_SIZE));
    return tail > 0 && tail % multiplyExact(itemWords, WORD_SIZE) == 0;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1051-1075)
```java
    @Override
    public Pair<Boolean, byte[]> execute(byte[] rawData) {
      if (VMConfig.allowTvmOsaka()
          && !isValidAbiEncoding(rawData, ABI_HEADER_WORDS, ABI_ITEM_WORDS)) {
        return Pair.of(false, EMPTY_BYTE_ARRAY);
      }
      DataWord[] words = DataWord.parseArray(rawData);
      byte[] address = words[0].toTronAddress();
      int permissionId = words[1].intValueSafe();
      byte[] data = words[2].getData();

      byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
      byte[] hash = Sha256Hash.hash(CommonParameter
          .getInstance().isECKeyCryptoEngine(), combine);

      if (VMConfig.allowTvmSelfdestructRestriction()) {
        int sigArraySize = words[words[3].intValueSafe() / WORD_SIZE].intValueSafe();
        if (sigArraySize > MAX_SIZE) {
          return Pair.of(true, DATA_FALSE);
        }
      }
      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[3].intValueSafe() / WORD_SIZE, rawData) :
          extractBytesArray(words, words[3].intValueSafe() / WORD_SIZE, rawData);

```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1144-1154)
```java
    @Override
    public Pair<Boolean, byte[]> execute(byte[] data) {
      try {
        return doExecute(data);
      } catch (Throwable t) {
        if (t instanceof InterruptedException){
          Thread.currentThread().interrupt();
        }
        return Pair.of(true, new byte[WORD_SIZE]);
      }
    }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L244-260)
```java
  // TIP-854: before activation, malformed calldata reaches the legacy decoder.
  // Assert the guard is not taken — this precompile has no outer catch, so a
  // too-short input raises inside the decoder; that is the documented
  // pre-activation failure mode the TIP explicitly preserves.
  @Test
  public void testTip854PreActivationNoOp() {
    VMConfig.initAllowTvmOsaka(0);
    contract.setRepository(RepositoryImpl.createRoot(StoreFactory.getInstance()));
    try {
      Pair<Boolean, byte[]> ret = contract.execute(new byte[(5 + 1) * 32]);
      // If the decoder happened to handle it without raising, we must not have
      // taken the post-activation reject path (false, empty).
      Assert.assertNotSame(ByteUtil.EMPTY_BYTE_ARRAY, ret.getRight());
    } catch (RuntimeException expectedLegacyBehaviour) {
      // Pre-activation: decoder may throw — this is the existing behaviour.
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1723-1755)
```java
    long requiredEnergy = contract.getEnergyForData(data);
    if (requiredEnergy > msg.getEnergy().longValue()) {
      // Not need to throw an exception, method caller needn't know that
      // regard as consumed the energy
      this.refundEnergy(0, CALL_PRE_COMPILED); //matches cpp logic
      this.stackPushZero();
    } else {
      // Delegate or not. if is delegated, we will use msg sender, otherwise use contract address
      if (msg.getOpCode() == Op.DELEGATECALL) {
        contract.setCallerAddress(getCallerAddress().toTronAddress());
      } else {
        contract.setCallerAddress(getContextAddress());
      }
      // this is the depositImpl, not contractState as above
      contract.setRepository(deposit);
      contract.setResult(this.result);
      contract.setConstantCall(isConstantCall());
      contract.setVmShouldEndInUs(getVmShouldEndInUs());
      Pair<Boolean, byte[]> out = contract.execute(data);

      if (out.getLeft()) { // success
        this.refundEnergy(msg.getEnergy().longValue() - requiredEnergy, CALL_PRE_COMPILED);
        this.stackPushOne();
        returnDataBuffer = out.getRight();
        deposit.commit();
      } else {
        // spend all energy on failure, push zero and revert state changes
        this.refundEnergy(0, CALL_PRE_COMPILED);
        this.stackPushZero();
        if (Objects.nonNull(this.result.getException())) {
          throw result.getException();
        }
      }
```

**File:** actuator/src/main/java/org/tron/core/vm/VM.java (L93-100)
```java
        } catch (RuntimeException e) {
          logger.info("VM halted: [{}]", e.getMessage());
          if (!(e instanceof TransferException)) {
            program.spendAllEnergy();
          }
          //program.resetFutureRefund();
          program.stop();
          throw e;
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L265-296)
```java
    } catch (JVMStackOverFlowException e) {
      program.spendAllEnergy();
      result = program.getResult();
      result.setException(e);
      result.rejectInternalTransactions();
      clearExceptionResult(result);
      result.setRuntimeError(result.getException().getMessage());
      logger.info("JVMStackOverFlowException: {}", result.getException().getMessage());
    } catch (OutOfTimeException e) {
      program.spendAllEnergy();
      result = program.getResult();
      result.setException(e);
      result.rejectInternalTransactions();
      clearExceptionResult(result);
      result.setRuntimeError(result.getException().getMessage());
      logger.info("timeout: {}", result.getException().getMessage());
    } catch (Throwable e) {
      if (!(e instanceof TransferException)) {
        program.spendAllEnergy();
      }
      result = program.getResult();
      result.rejectInternalTransactions();
      clearExceptionResult(result);
      if (Objects.isNull(result.getException())) {
        logger.error(e.getMessage(), e);
        result.setException(new RuntimeException("Unknown Throwable"));
      }
      if (StringUtils.isEmpty(result.getRuntimeError())) {
        result.setRuntimeError(result.getException().getMessage());
      }
      logger.info("runtime result is :{}", result.getException().getMessage());
    }
```
