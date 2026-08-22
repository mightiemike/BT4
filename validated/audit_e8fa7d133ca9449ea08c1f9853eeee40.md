### Title
Unbounded byte-array length in `ValidateMultiSign`/`BatchValidateSign` calldata decoding enables memory-exhaustion DoS — (`actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The Solady report describes `decodeBatch()` failing to validate that every pointer location referenced by the ABI-encoded input lies within the bounds of the supplied data, allowing crafted offsets to be decoded incorrectly. The closest reachable analog in java-tron is the manual ABI decoding helper `extractBytesArray()` used by the `ValidateMultiSign` and `BatchValidateSign` precompiled contracts, which extracts a dynamic-length byte array (signature bytes) from calldata using an attacker-controlled length field with **no check that the declared length fits inside the actual calldata buffer**.

### Finding Description
`extractBytesArray()` reads a "length" word from calldata and uses it directly to slice out bytes: [1](#0-0) 

`bytesLen` comes straight from `words[offset + bytesOffset + 1].intValueSafe()` — a 32-byte calldata word fully controlled by the caller — and is passed to `extractBytes()`, which calls `Arrays.copyOfRange(data, offset, offset + len)`. Java's `copyOfRange` zero-pads when `offset + len` exceeds `data.length`, but it still allocates a new array of the *full requested size* before copying, so an attacker can request an allocation of up to ~2^31 bytes (~2 GB) per call with a single crafted length word, regardless of how small the actual calldata is.

This routine is reached from `ValidateMultiSign.execute()`: [2](#0-1) 

and from `BatchValidateSign.doExecute()` (same helper, same missing check): [3](#0-2) 

Crucially, the **energy charged for the call is derived from the physical calldata length**, not from the attacker-declared `bytesLen`: [4](#0-3) 

so a minimal, cheap calldata payload (just enough words to pass the `offset > words.length - 1` guard) can encode a single dynamic-bytes entry whose declared length is an arbitrary large 32-byte integer, decoupling the energy cost from the actual memory the precompile will try to allocate — this is structurally the same class of defect as the reported bug: a bounds check exists for the "header" location but not for every value derived from decoded offsets/lengths, allowing the check to be bypassed.

The only mitigation present, `isValidAbiEncoding()`, is a purely structural check (`data.length` is a multiple of item size) gated behind the `VMConfig.allowTvmOsaka()` feature flag, and it does not validate any of the *nested* dynamic length fields such as `bytesLen`: [5](#0-4) 

An `OutOfMemoryError` raised inside the precompile is a Java `Error`, not a `RuntimeException`. `VM.play()`'s inner exception handling only catches `RuntimeException`, `JVMStackOverFlowException`/`OutOfTimeException`, and `StackOverflowError` — it does not catch `OutOfMemoryError`: [6](#0-5) 

so the error propagates further up to `VMActuator`, which does catch generic `Throwable`: [7](#0-6) 

meaning a single malicious transaction is technically contained at the transaction-processing layer rather than crashing the node outright. However, the large transient allocation attempt still stresses the shared JVM heap for the whole node process, and because signature verification runs through a shared, size-limited thread pool in `BatchValidateSign` (`workers`), repeated invocations across concurrently-processed transactions can degrade overall block/transaction processing throughput.

### Impact Explanation
An attacker can send an ordinary, unprivileged transaction (or have a smart contract issue a `CALL`/`STATICCALL`) to the `ValidateMultiSign` or `BatchValidateSign` precompiled contract addresses with a crafted, small calldata payload that declares a very large dynamic-bytes length. This can force the node to attempt multi-gigabyte heap allocations per invocation while paying energy proportional only to the small physical calldata size, producing a resource-exhaustion / availability impact on any node processing the transaction (full nodes and validating witnesses alike), which is a legitimate DoS-class concern reachable purely via broadcast transactions/contract calls.

### Likelihood Explanation
Exploitability depends on which hard-fork/config flags (`VMConfig.allowTvmOsaka()`, `VMConfig.allowTvmSelfdestructRestriction()`) are active on a given deployment, since some size caps (`MAX_SIZE`) are only enforced after the vulnerable extraction already occurred, or only under certain flags. This makes exploitability configuration-dependent, but the underlying decoding helper (`extractBytesArray`/`extractBytes`) itself never validates the declared length against the real data buffer size in any configuration, so the root cause is present in all cases; only the surrounding guards vary.

### Recommendation
In `extractBytesArray()` (and any similar manual ABI decoders in `PrecompiledContracts.java`), validate that `bytesOffset` and `bytesLen` are non-negative and that `(bytesOffset + offset + 2) * WORD_SIZE + bytesLen <= data.length` before calling `extractBytes()`, rejecting (returning failure) rather than attempting the allocation otherwise — mirroring the bounds-check pattern already used in `ContractEventParser.subBytes()` (`framework/src/main/java/org/tron/common/logsfilter/ContractEventParser.java`), which explicitly rejects oversized/negative lengths relative to the source buffer.

### Proof of Concept
Construct calldata for `ValidateMultiSign`/`BatchValidateSign` with:
- header words (`address`/`permissionId`/`data`, plus the array-offset words) sized to just pass the initial `offset > words.length - 1` check,
- an array length field (`len`) of `1`,
- a single per-entry offset word,
- and the corresponding "length" word for that single dynamic-bytes entry set to a very large value (e.g. `0x7FFFFFFF`),
while leaving the remainder of calldata short (well under the declared length).

Calling this precompile with such calldata drives `extractBytesArray()`/`extractBytes()` into `Arrays.copyOfRange(data, offset, offset + 0x7FFFFFFF)`, attempting a ~2 GB allocation, while `getEnergyForData()` charges energy based only on the small physical calldata size supplied.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L399-430)
```java
  private static byte[][] extractBytesArray(DataWord[] words, int offset, byte[] data) {
    if (offset > words.length - 1) {
      return new byte[0][];
    }
    int len = words[offset].intValueSafe();
    byte[][] bytesArray = new byte[len][];
    for (int i = 0; i < len; i++) {
      int bytesOffset = words[offset + i + 1].intValueSafe() / WORD_SIZE;
      int bytesLen = words[offset + bytesOffset + 1].intValueSafe();
      bytesArray[i] = extractBytes(data, (bytesOffset + offset + 2) * WORD_SIZE,
          bytesLen);
    }
    return bytesArray;
  }

  private static byte[][] extractSigArray(DataWord[] words, int offset, byte[] data) {
    if (offset > words.length - 1) {
      return new byte[0][];
    }
    int len = words[offset].intValueSafe();
    byte[][] bytesArray = new byte[len][];
    for (int i = 0; i < len; i++) {
      int bytesOffset = words[offset + i + 1].intValueSafe() / WORD_SIZE;
      bytesArray[i] = extractBytes(data, (bytesOffset + offset + 2) * WORD_SIZE,
          SIG_LENGTH);
    }
    return bytesArray;
  }

  private static byte[] extractBytes(byte[] data, int offset, int len) {
    return Arrays.copyOfRange(data, offset, offset + len);
  }
```

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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1044-1049)
```java
    @Override
    public long getEnergyForData(byte[] data) {
      long cnt = (data.length / WORD_SIZE - 5) / 5;
      // one sign 1500, half of ecrecover
      return cnt * ENGERYPERSIGN;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1052-1076)
```java
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

      if (signatures.length == 0 || signatures.length > MAX_SIZE) {
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1173-1177)
```java
      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[1].intValueSafe() / WORD_SIZE, data) :
          extractBytesArray(words, words[1].intValueSafe() / WORD_SIZE, data);
      byte[][] addresses = extractBytes32Array(
          words, words[2].intValueSafe() / WORD_SIZE);
```

**File:** actuator/src/main/java/org/tron/core/vm/VM.java (L93-126)
```java
        } catch (RuntimeException e) {
          logger.info("VM halted: [{}]", e.getMessage());
          if (!(e instanceof TransferException)) {
            program.spendAllEnergy();
          }
          //program.resetFutureRefund();
          program.stop();
          throw e;
        } finally {
          program.fullTrace();
        }
      }

      if (allowDynamicEnergy) {
        program.addContextContractUsage(energyUsage);
      }

    } catch (JVMStackOverFlowException | OutOfTimeException e) {
      throw e;
    } catch (RuntimeException e) {
      // https://openjdk.org/jeps/358
      // https://bugs.openjdk.org/browse/JDK-8220715
      // since jdk 14, the NullPointerExceptions message is not empty
      if (e instanceof NullPointerException || StringUtils.isEmpty(e.getMessage())) {
        logger.warn("Unknown Exception occurred, tx id: {}",
            Hex.toHexString(program.getRootTransactionId()), e);
        program.setRuntimeFailure(new RuntimeException("Unknown Exception"));
      } else {
        program.setRuntimeFailure(e);
      }
    } catch (StackOverflowError soe) {
      logger.info("\n !!! StackOverflowError: update your java run command with -Xss !!!\n", soe);
      throw new JVMStackOverFlowException();
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L265-297)
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
    //use program returned fill context
```
