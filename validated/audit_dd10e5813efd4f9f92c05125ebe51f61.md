### Title
Unvalidated `rcm` length in `GrpcAPI.Note` causes uncaught `ArrayIndexOutOfBoundsException` in `Note.encode()`/native zk calls, bypassing checked `ZksnarkException` handling - ([File: framework/src/main/java/org/tron/core/zen/note/Note.java])

### Summary
`Wallet.createShieldedTransaction`, `createShieldedTransactionWithoutSpendAuthSig`, and `createShieldedContractParameters` build `Note` objects directly from attacker-supplied `GrpcAPI.Note.rcm` bytes without validating their length against `ZC_R_SIZE` (32 bytes). `Note.decode()` itself only operates on internally generated/decrypted buffers and is not attacker-reachable, but the sibling method `Note.encode()` and the JNA-bound native calls (`librustzcashComputeCm`, `librustzcashSaplingSpendProof`, etc.) consume this same unvalidated `rcm`/`pkD` data and perform fixed-size `System.arraycopy`/native buffer reads, which throw uncatchable `RuntimeException`s or corrupt native memory when the input is short.

### Finding Description
In `Wallet.createShieldedTransaction` (and its sibling `createShieldedTransactionWithoutSpendAuthSig`/`createShieldedContractParameters`), the attacker-controlled `GrpcAPI.Note.getRcm()` bytes are copied straight into a `Note` with no length check: [1](#0-0) [2](#0-1) 

The `Note` constructors used here perform no bounds validation on `r`/`pkD`: [3](#0-2) 

When the output note is later serialized for encryption, `Note.encode()` performs a fixed-size `System.arraycopy(rcm, 0, data, ..., ZC_R_SIZE)`, which throws an unchecked `IndexOutOfBoundsException`/`ArrayIndexOutOfBoundsException` if `rcm.length < ZC_R_SIZE` (32): [4](#0-3) 

By contrast, `setMemo()` explicitly clamps the copy length (`Math.min(memo.length, ZC_MEMO_SIZE)`), so a short/long `memo` array is safe: [5](#0-4) 
—but no equivalent guard exists for `rcm`, and `pkD` is also passed unchecked into native `librustzcashComputeCm`/`librustzcashSaplingSpendProof` calls: [6](#0-5) 

`Wallet.createShieldedTransaction`'s try/catch only handles `ArithmeticException` and `ZksnarkException`; any other `RuntimeException` (such as the `IndexOutOfBoundsException` from `encode()`) propagates uncaught: [7](#0-6) 
The same pattern repeats in `createShieldedTransactionWithoutSpendAuthSig`: [8](#0-7) 

Note: `Note.decode(EncPlaintext)` itself is only invoked from `Note.decrypt(...)`, which operates on internally decrypted ciphertext buffers of guaranteed fixed size, not on raw attacker bytes — so the literal function named in the question is not directly reachable from the RPC entrypoint. The actual attacker-reachable defect is in the `Note` constructors / `encode()` / native parameter marshalling path described above, which shares the same root cause (missing input-length validation before fixed-offset `arraycopy`/native buffer access).

### Impact Explanation
An unprivileged client can submit a `PrivateParameters`/`PrivateShieldedTRC20Parameters` request with a `GrpcAPI.Note.rcm` shorter than 32 bytes. This throws an uncaught `IndexOutOfBoundsException` from `Note.encode()` (reached via `shieldedOutput` → `Note.encrypt()` → `Note.encode()`), which is not a `ZksnarkException`, bypassing the intended checked-exception error handling and propagating as an unhandled `RuntimeException` in the gRPC/HTTP request-handling thread. Depending on the server's top-level exception handling, this can cause an inconsistent/500-style error response, differ across implementations, or (via the unchecked native calls with mismatched buffer sizes) risk memory-safety issues in the JNA-bound `librustzcash` native library when `pkD`/`rcm` arrays are shorter than the native code expects.

### Likelihood Explanation
Requires only `AllowShieldedTransaction=1` (a standard, non-privileged node configuration for shielded transactions) and a single malformed RPC call to `createShieldedTransaction`, `createShieldedTransactionWithoutSpendAuthSig`, or `createShieldedContractParameters`. No prior authentication or state is required; it is trivially and repeatably reachable by any client that can reach the shielded RPC endpoints.

### Recommendation
Add explicit length validation for `rcm`, `pkD`, and `memo` fields of `GrpcAPI.Note`/`SpendNoteTRC20`/`ReceiveNote` in `Wallet.createShieldedTransaction`, `createShieldedTransactionWithoutSpendAuthSig`, `buildShieldedTRC20Input`, and `shieldedOutput` before constructing `Note` objects, throwing `ContractValidateException`/`ZksnarkException` for any field whose length does not exactly match `ZenChainParams.ZC_R_SIZE` (32) for `rcm`/`pkD`. Additionally, harden `Note.encode()`/`Note` constructors to validate array lengths defensively, and audit `LibrustzcashParam`/`JLibrustzcash` native call sites to reject arrays that don't match the native buffer size before invoking JNA.

### Proof of Concept
```java
@Test
public void testShortRcmInReceiveNoteCausesUncaughtException() {
  dbManager.getDynamicPropertiesStore().saveAllowShieldedTransaction(1);
  PrivateParameters.Builder builder = PrivateParameters.newBuilder();
  builder.setOvk(ByteString.copyFrom(new byte[32]));
  GrpcAPI.Note.Builder noteBuilder = GrpcAPI.Note.newBuilder()
      .setValue(1000000L)
      .setPaymentAddress(validPaymentAddressStr)
      .setRcm(ByteString.copyFrom(new byte[16])) // shorter than ZC_R_SIZE=32
      .setMemo(ByteString.copyFrom(new byte[512]));
  builder.addShieldedReceives(
      ReceiveNote.newBuilder().setNote(noteBuilder.build()).build());
  builder.setTransparentToAddress(ByteString.copyFrom(toAddress));
  builder.setToAmount(0); // or valid balancing values

  try {
    wallet.createShieldedTransaction(builder.build());
    Assert.fail("expected exception");
  } catch (ZksnarkException | ContractValidateException expected) {
    // acceptable
  } catch (RuntimeException e) {
    // FAILS current code: IndexOutOfBoundsException/ArrayIndexOutOfBoundsException
    // thrown from Note.encode() propagates here uncaught
    Assert.fail("Uncaught RuntimeException leaked past checked exception handling: " + e);
  }
}
```
Fuzz extension: repeat for `rcm` lengths {0, 1, 31, 33, 512} and for `SpendNote.getNote().getRcm()` on the spend path (`createShieldedTransaction`, `createShieldedContractParameters`), asserting only `ZksnarkException`/`ContractValidateException` are ever thrown.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2344-2345)
```java
          Note baseNote = new Note(paymentAddress.getD(),
              paymentAddress.getPkD(), note.getValue(), note.getRcm().toByteArray());
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2362-2368)
```java
    } catch (ArithmeticException e) {
      throw new ZksnarkException("shielded amount overflow", e);
    } catch (ZksnarkException e) {
      logger.error("createShieldedTransaction except, error is {}", e.toString());
      throw e;
    }
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2466-2472)
```java
    } catch (ArithmeticException e) {
      throw new ZksnarkException("shielded amount overflow", e);
    } catch (ZksnarkException e) {
      logger.error("createShieldedTransaction exception, error is {}", e.toString());
      throw e;
    }
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2483-2485)
```java
      builder.addOutput(ovk, paymentAddress.getD(), paymentAddress.getPkD(),
          receiveNote.getNote().getValue(), receiveNote.getNote().getRcm().toByteArray(),
          receiveNote.getNote().getMemo().toByteArray());
```

**File:** framework/src/main/java/org/tron/core/zen/note/Note.java (L58-63)
```java
  public Note(DiversifierT d, byte[] pkD, long value, byte[] r) {
    this.d = d;
    this.pkD = pkD;
    this.value = value;
    this.rcm = r;
  }
```

**File:** framework/src/main/java/org/tron/core/zen/note/Note.java (L178-184)
```java
  public void setMemo(byte[] memo) {
    if (ByteArray.isEmpty(memo)) {
      return;
    }
    int memoSize = memo.length < ZC_MEMO_SIZE ? memo.length : ZC_MEMO_SIZE;
    System.arraycopy(memo, 0, this.memo, 0, memoSize);
  }
```

**File:** framework/src/main/java/org/tron/core/zen/note/Note.java (L186-194)
```java
  // Call librustzcash to compute the commitment
  public byte[] cm() throws ZksnarkException {
    byte[] result = new byte[32];
    if (!JLibrustzcash.librustzcashComputeCm(
        new ComputeCmParams(d.getData(), pkD, value, rcm, result))) {
      return null;
    }
    return result;
  }
```

**File:** framework/src/main/java/org/tron/core/zen/note/Note.java (L262-273)
```java
    byte[] data = new byte[ZC_ENCPLAINTEXT_SIZE];
    data[0] = 0x01;
    System.arraycopy(d.getData(), 0, data, ZC_NOTEPLAINTEXT_LEADING, ZC_DIVERSIFIER_SIZE);
    System.arraycopy(valueLong, 0, data, ZC_NOTEPLAINTEXT_LEADING + ZC_DIVERSIFIER_SIZE, ZC_V_SIZE);
    System.arraycopy(rcm, 0, data, ZC_NOTEPLAINTEXT_LEADING + ZC_DIVERSIFIER_SIZE + ZC_V_SIZE,
        ZC_R_SIZE);
    System.arraycopy(
        memo,
        0,
        data,
        ZC_NOTEPLAINTEXT_LEADING + ZC_DIVERSIFIER_SIZE + ZC_V_SIZE + ZC_R_SIZE,
        ZC_MEMO_SIZE);
```
