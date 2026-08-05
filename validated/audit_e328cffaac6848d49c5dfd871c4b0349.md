### Title
`BatchValidateSign` precompile swallows a CPU-timeout ("out-of-gas" analog) and returns a fabricated success result instead of reverting - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
The `PreimageOracle.loadPrecompilePreimagePart` report describes a precompile that fails with an out-of-gas condition, yet the caller still commits the erroneous output as if it were correct instead of reverting. The same bug class exists in java-tron's `BatchValidateSign` precompile: when the internal signature-recovery work exceeds the node's CPU time budget (the TVM analog of "out of gas" for CPU-bound work), the timeout exception is caught by a blanket `catch (Throwable t)` and converted into a fabricated **successful** result (`Pair.of(true, new byte[WORD_SIZE])`) rather than being propagated to abort/revert the call.

### Finding Description
`BatchValidateSign.execute` wraps `doExecute` in a catch-all: [1](#0-0) 

Inside `doExecute`, signature recovery for a batch of up to `MAX_SIZE` (16) signatures is dispatched to a thread pool, and the calling thread waits on a `CountDownLatch` bounded by the node's remaining CPU-time budget (`getCPUTimeLeftInNanoSecond()`). If the batch doesn't finish in time, an `OutOfTimeException` is explicitly thrown: [2](#0-1) 

This `OutOfTimeException` (a `Throwable`) is caught by the outer `execute` wrapper shown above and silently converted into `Pair.of(true, new byte[WORD_SIZE])` — a **success** flag (`true`) with an all-zero result byte array, meaning "no signature matched any of the provided addresses." The call is then treated by `Program.callToPrecompiledAddress` as a normal successful precompile execution: it refunds unused energy, pushes `1` onto the stack, commits the deposit, and copies the all-zero bytes into the calling contract's memory as the return data: [3](#0-2) 

This is the exact bug class from the report: a resource-exhaustion failure inside a precompile is silently absorbed and a fabricated "good" result is written into the caller-visible state, instead of the call reverting or otherwise signaling failure.

Notably, the sibling precompile `ValidateMultiSign` shows the correct behavior was known and implemented there: it explicitly re-throws `OutOfTimeException` instead of swallowing it, only catching genuine non-timeout errors as "verification failed": [4](#0-3) 

`BatchValidateSign`'s outer `catch (Throwable t)` has no equivalent carve-out for `OutOfTimeException`, so the timeout path is masked as a legitimate "all signatures invalid" verification result.

### Impact Explanation
`BatchValidateSign` is a public precompiled contract at a well-known address, callable by any smart contract (e.g., for TIP-854-style multisig/batch signature verification). When the node is under load or an attacker crafts a batch near `MAX_SIZE` such that the recovery workers cannot complete within the remaining CPU-time budget, the precompile returns `(true, 0x00...00)` instead of failing loudly. A calling contract that interprets this all-zero result as "no addresses matched" cannot distinguish a genuine cryptographic mismatch from an infrastructure-level timeout. This corrupts on-chain signature-verification accounting: legitimate signature batches can be non-deterministically reported as invalid depending on transient node CPU pressure, and the divergence between what "should" have happened (verification error/revert) and what is recorded on-chain (a definitive, silently-fabricated `false`-per-signature result) is directly analogous to the PreimageOracle case where an incorrect state is committed instead of reverting.

### Likelihood Explanation
Triggering the timeout does not require any privileged role — it only requires calling `BatchValidateSign` (directly or via an intermediate contract) with a batch large enough (up to `MAX_SIZE = 16` signatures) or during moments of contended thread-pool/CPU-time availability so that `countDownLatch.await(getCPUTimeLeftInNanoSecond(), ...)` times out. This is unprivileged, publicly reachable TVM behavior, matching the report's requirement that the analog be reachable by an unprivileged user through normal contract interaction.

### Recommendation
In `BatchValidateSign.execute`, do not swallow `OutOfTimeException` (or other resource-exhaustion signals) into a fabricated success result. Mirror the pattern already used in `ValidateMultiSign`: re-throw `OutOfTimeException` (and any other CPU/resource-budget exception) so the call fails/aborts instead of returning `Pair.of(true, ...)` with synthetic all-zero data.

### Proof of Concept
1. Construct calldata for `BatchValidateSign` with a large batch (close to `MAX_SIZE = 16`) of valid signature/address pairs that would all correctly verify given sufficient time.
2. Invoke the precompile (directly via a contract using `staticcall`/`call`, paying enough energy per `getEnergyForData`) in circumstances where the shared `validate-sign-contract` thread pool is busy or CPU time is scarce, such that `countDownLatch.await(...)` in `doExecute` [5](#0-4)  times out and throws `OutOfTimeException`.
3. Observe that `execute`'s outer catch block converts this into `Pair.of(true, new byte[WORD_SIZE])` [1](#0-0) , and that `Program.callToPrecompiledAddress` treats it as a successful call, committing state and returning the all-zero buffer to the caller as if every signature failed to match [3](#0-2) , even though the actual signatures were valid and verification simply never completed.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1112-1117)
```java
        } catch (Throwable t) {
          if (t instanceof OutOfTimeException) {
            throw t;
          }
          logger.info("ValidateMultiSign error:{}", t.getMessage());
        }
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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1191-1207)
```java
      } else {
        // add check
        CountDownLatch countDownLatch = new CountDownLatch(cnt);
        List<Future<RecoverAddrResult>> futures = new ArrayList<>(cnt);

        for (int i = 0; i < cnt; i++) {
          Future<RecoverAddrResult> future = workers
              .submit(new RecoverAddrTask(countDownLatch, hash, signatures[i], i));
          futures.add(future);
        }
        boolean withNoTimeout = countDownLatch
            .await(getCPUTimeLeftInNanoSecond(), TimeUnit.NANOSECONDS);

        if (!withNoTimeout) {
          logger.info("BatchValidateSign timeout");
          throw Program.Exception.notEnoughTime("call BatchValidateSign precompile method");
        }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1741-1761)
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

      if (VMConfig.allowTvmSelfdestructRestriction()) {
        this.memorySave(msg.getOutDataOffs().intValueSafe(), msg.getOutDataSize().intValueSafe(), out.getRight());
      } else {
        this.memorySave(msg.getOutDataOffs().intValue(), out.getRight());
      }
```
