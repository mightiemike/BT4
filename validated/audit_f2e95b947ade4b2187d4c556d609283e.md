### Title
Uncaught decoder exceptions in `ValidateMultiSign`/`BatchValidateSign` precompiles break CALL semantics and halt the whole transaction - ([File: actuator/src/main/java/org/tron/core/vm/program/Program.java])

### Summary
Mirrors the Gearbox `claimRewards()` bug class: a callee that is expected to fail gracefully (return `false`/`0` to the caller) instead throws an uncaught, unbounded/out-of-range-style exception that propagates past the intended failure boundary, forcing an unintended abort of the entire enclosing execution instead of a contained, catchable failure.

### Finding Description
In the TVM, a `CALL`/`STATICCALL` to a precompiled contract is supposed to behave like any other external call: on failure the inner call pushes `0` onto the caller's stack and the caller's execution continues normally. This flow is implemented in `Program.callToPrecompiledAddress`, which invokes `contract.execute(data)` and, only if the contract signaled failure *and* set an exception, re-throws that exception into the VM's opcode loop: [1](#0-0) 

That re-thrown exception is caught by `VM.play`'s per-opcode loop only to spend all energy and `stop()` the whole program, i.e. it aborts the *entire* transaction rather than being contained to the failed inner call: [2](#0-1) 

For the `ValidateMultiSign` and `BatchValidateSign` precompiles, malformed/too-short calldata is decoded without adequate bounds checking, and the decoder itself throws a raw `RuntimeException` (array-index/argument style exception) rather than returning `Pair.of(false, ...)`. The codebase's own regression tests document this exact defect and its guarded fix (TIP-854), which wraps the decode/execute path in a try/catch so a malformed call is contained (pushes `0`, no exception on the outer frame, execution continues) — but only when `VMConfig.allowTvmOsaka()` is enabled: [3](#0-2) 

Crucially, the pre-activation (i.e. current default/unactivated) behavior is explicitly called out in the test suite as unfixed and equivalent to the original bug — "this precompile has no outer catch, so a too-short input raises inside the decoder; that is the documented pre-activation failure mode": [4](#0-3) 

This is structurally identical to the Gearbox `ClaimZap` issue: an external/precompiled call that is documented/expected to fail gracefully (return `false`) instead reverts/throws due to unguarded low-level access on attacker/caller-controlled input, and that failure is not contained where it should be, propagating up and destroying more state/execution than intended.

### Impact Explanation
Before the TIP-854 activation flag is turned on, any user-deployed smart contract that calls the `ValidateMultiSign` or `BatchValidateSign` precompiled contract addresses with malformed or too-short calldata (fully attacker/caller controlled, since it's just a `CALL`/`STATICCALL` with arbitrary input) causes:
- All remaining energy in the transaction to be spent (`program.spendAllEnergy()`), and
- The entire transaction execution to halt with an exception, instead of the inner call simply returning `0`/`false` as EVM semantics require.

This breaks composability for any legitimate contract that follows the standard "try external call, check return value, handle failure gracefully" pattern (exactly the pattern the Gearbox `ClaimZap` adapter relied on and which failed). Callers relying on graceful failure of these permission-validation precompiles instead lose their entire transaction and fee/energy — an availability/DoS-style and unfair-cost impact reachable by any unprivileged user through ordinary contract interaction with a fixed-address system precompile.

### Likelihood Explanation
High reachability: any account can deploy a trivial contract that calls the `ValidateMultiSign`/`BatchValidateSign` precompile addresses with a short/malformed payload — no special privilege required. The only gating factor is whether the TIP-854 activation flag (`VMConfig.allowTvmOsaka()`) is enabled on the target network; the test suite explicitly preserves and documents the un-guarded, throwing behavior for the pre-activation state as "existing behaviour," confirming the bug is live whenever that governance parameter has not been turned on.

### Recommendation
Apply the same containment guard used in the TIP-854 code path unconditionally (not gated behind `allowTvmOsaka()`), i.e. wrap the calldata decoding/execution of `ValidateMultiSign` and `BatchValidateSign` (and audit any other precompiled contracts with similar unguarded decoding) in try/catch so that any decoding/runtime exception results in `Pair.of(false, EMPTY_BYTE_ARRAY)` rather than propagating a `RuntimeException` out of `execute()`. This ensures `Program.callToPrecompiledAddress` never sees `result.getException() != null` for pure input-decoding failures, preserving standard EVM CALL semantics (push `0`, continue outer frame) instead of aborting the whole transaction.

### Proof of Concept
1. Ensure the network parameter enabling TIP-854 (`allowTvmOsaka`) is not activated (default state).
2. Deploy a contract that performs `staticcall`/`call` to the `ValidateMultiSign` (or `BatchValidateSign`) precompiled address with calldata shorter than the expected 5-head-word minimum (e.g., empty/zero-length calldata), as constructed in the existing test: [5](#0-4) 
3. Pre-activation, this raises inside the decoder (`ValidateMultiSignContractTest.testTip854PreActivationNoOp`), all transaction energy is spent, and the whole calling transaction fails/aborts rather than the inner call simply returning `0` and letting the outer contract logic continue — reproducing the "unexpectedly reverts instead of returning a graceful sentinel" bug class from the external report.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1741-1755)
```java
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

**File:** framework/src/test/java/org/tron/common/runtime/vm/OperationsTest.java (L857-898)
```java
  // TIP-854 outer-frame containment: a CALL to validateMultiSign or
  // batchValidateSign with malformed calldata must (a) push 0 onto the outer
  // stack, (b) leave the outer frame free of any propagated exception, and
  // (c) allow the outer frame to continue executing afterwards.
  @Test
  public void testTip854OuterFrameContainment() throws ContractValidateException {
    byte prePrefixByte = DecodeUtil.addressPreFixByte;
    DecodeUtil.addressPreFixByte = Constant.ADD_PRE_FIX_BYTE_MAINNET;
    VMConfig.initAllowTvmOsaka(1);
    try {
      for (PrecompiledContracts.PrecompiledContract contract :
          new PrecompiledContracts.PrecompiledContract[]{
              new PrecompiledContracts.ValidateMultiSign(),
              new PrecompiledContracts.BatchValidateSign()}) {
        invoke = new ProgramInvokeMockImpl();
        InternalTransaction interTrx = new InternalTransaction(
            Protocol.Transaction.getDefaultInstance(),
            InternalTransaction.TrxType.TRX_UNKNOWN_TYPE);
        program = new Program(new byte[0], new byte[0], invoke, interTrx);
        // inDataSize=0 ⇒ data=[] ⇒ fewer than H=5 head words ⇒ guard rejects.
        MessageCall messageCall = new MessageCall(
            Op.CALL, new DataWord(10000),
            DataWord.ZERO(), DataWord.ZERO(),
            DataWord.ZERO(), DataWord.ZERO(),
            DataWord.ZERO(), DataWord.ZERO(),
            DataWord.ZERO(), false);
        program.callToPrecompiledAddress(messageCall, contract);

        Assert.assertNull(contract.getClass().getSimpleName()
                + ": outer frame must not inherit an exception",
            program.getResult().getException());
        Assert.assertEquals(contract.getClass().getSimpleName() + ": inner CALL pushes 0",
            DataWord.ZERO(), program.getStack().pop());
        // Outer frame continues: another stack op works without throwing.
        program.stackPush(new DataWord(1));
        Assert.assertEquals(new DataWord(1), program.getStack().pop());
      }
    } finally {
      VMConfig.initAllowTvmOsaka(0);
      DecodeUtil.addressPreFixByte = prePrefixByte;
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
