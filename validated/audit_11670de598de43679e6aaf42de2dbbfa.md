### Title
Missing length validation on shielded output parameters (`pkD`, `d`, `rcm`/`r`) before native zk-SNARK proof call - ([File: framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java])

### Summary
`ZenTransactionBuilder.addOutput(byte[] ovk, DiversifierT d, byte[] pkD, long value, byte[] r, byte[] memo)` accepts caller-controlled diversifier, `pkD`, and `rcm` byte arrays and stores them in a `Note` without checking their lengths against the fixed sizes the native Rust/sodium code expects (32 bytes for `pkD`/`rcm`, 11 bytes for the diversifier). Only `memo` is defensively truncated in `Note.setMemo`; `ovk` length is validated, but only *after* the native proof call has already run.

### Finding Description
`addOutput` builds a `Note(d, pkD, value, r)` directly from attacker-influenced inputs with no bounds checks: [1](#0-0) 

That `Note` is later consumed in `build()` → `generateOutputProof()`, which passes `output.getNote().getD().getData()`, `output.getNote().getPkD()`, and `output.getNote().getRcm()` straight into `JLibrustzcash.librustzcashSaplingOutputProof(new OutputProofParams(...))`, a JNI call into the native rust/sodium sapling library: [2](#0-1) 

The only length check present in this path — on `ovk` — happens *after* `librustzcashSaplingOutputProof` has already been invoked: [3](#0-2) 

By contrast, `Note.setMemo` shows the codebase is aware that unchecked lengths are dangerous and explicitly truncates/guards the memo field before it is used: [4](#0-3) 

No equivalent guard exists for `d`, `pkD`, or `rcm`. These fields originate from protobuf `bytes` fields in shielded-transfer/shielded-TRC20 requests, which impose no length constraint at the protocol-buffer level, so an unprivileged client constructing a shielded output request can supply arbitrarily sized (oversized or undersized) byte arrays for the diversifier, `pkD`, and `rcm`. Because the native JNI method `librustzcashSaplingOutputProof` is implemented as unsafe Rust code that typically treats the passed byte arrays as fixed-size buffers when marshaling via JNI (`GetByteArrayElements`/pointer casts of a hardcoded length, e.g. 32 bytes), a shorter array can lead to an out-of-bounds read past the JVM-owned buffer, and this happens before any length is checked in Java.

### Impact Explanation
If the native output-proof routine reads a fixed number of bytes assuming 32-byte `pkD`/`rcm` inputs and the Java-supplied arrays are shorter, the native code can read past the end of the allocated Java byte array inside the JVM's heap, corrupting memory state or causing a JVM segfault/crash of the node process (Denial-of-Service). This matches the "Node RCE / crash" bounty impact class at least for the crash/DoS component; escalation to full RCE would additionally depend on the specific memory layout and unsafe pointer arithmetic inside the native library, which is not visible in this Java repository and could not be independently confirmed here.

### Likelihood Explanation
The attacker needs no privileged role: any client able to submit a shielded-transaction-building request (e.g., via the wallet/gRPC shielded transfer or shielded-TRC20 API surfaces that ultimately call `addOutput`) can supply a `pkD`/`rcm`/diversifier of arbitrary length, since these travel as raw protobuf `bytes` with no size assertion prior to reaching `ZenTransactionBuilder`. The only economic cost is constructing and sending the malformed request; no fee is paid before the native call executes on the node processing the request (this is typically triggered on the node building or validating the shielded parameters, not necessarily requiring a full signed/broadcast transaction). This makes the issue cheap and repeatable to trigger for DoS purposes.

### Recommendation
Add explicit length validation in `ZenTransactionBuilder.addOutput` (and in `Note`'s constructors) for `d` (11 bytes), `pkD` (32 bytes), and `r`/`rcm` (32 bytes), throwing `ZksnarkException` immediately if the lengths don't match, mirroring the existing `ovk` check but performed *before* any native call, not after. This should be enforced at the earliest point these arrays are accepted (both `addOutput` overloads and `Note` constructors), not deferred to `generateOutputProof`.

### Proof of Concept
```java
// JUnit-style PoC demonstrating missing pre-call validation
@Test(expected = ZksnarkException.class)
public void testAddOutputRejectsMalformedPkD() throws ZksnarkException {
  ZenTransactionBuilder builder = new ZenTransactionBuilder();
  byte[] ovk = new byte[32];
  DiversifierT d = new DiversifierT(); // expects 11 bytes internally
  byte[] malformedPkD = new byte[4];   // should be 32 bytes, oversized/undersized here
  byte[] malformedRcm = new byte[300]; // grossly oversized
  byte[] memo = new byte[10];

  // Expectation: addOutput should validate lengths and throw ZksnarkException
  // before ever building a Note or reaching JLibrustzcash native calls.
  builder.addOutput(ovk, d, malformedPkD, 1000L, malformedRcm, memo);

  // Currently: no exception is thrown here; the malformed Note is silently
  // accepted into `receives`, and the bad-length arrays are only discovered
  // (if at all) deep inside build() -> generateOutputProof() -> native JNI call.
}
```
Currently, `addOutput` performs no such check, so this test would fail (no exception thrown at `addOutput` time) — the malformed-length arrays are only forwarded to the native layer at `build()` time, at which point `librustzcashSaplingOutputProof` receives the raw, unchecked byte arrays. Full confirmation of native-side out-of-bounds behavior requires code review of the Rust/JNI native library (`librustzcash`), which is outside this Java repository and could not be directly inspected here.

### Citations

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L113-118)
```java
  public void addOutput(byte[] ovk, DiversifierT d, byte[] pkD, long value, byte[] r, byte[] memo) {
    Note note = new Note(d, pkD, value, r);
    note.setMemo(memo);
    valueBalance = StrictMathWrapper.subtractExact(valueBalance, value);
    receives.add(new ReceiveDescriptionInfo(ovk, note));
  }
```

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L283-293)
```java
    if (!JLibrustzcash.librustzcashSaplingOutputProof(
        new OutputProofParams(ctx,
            encryptor.getEsk(),
            output.getNote().getD().getData(),
            output.getNote().getPkD(),
            output.getNote().getRcm(),
            output.getNote().getValue(),
            cv,
            zkProof))) {
      throw new ZksnarkException("Output proof failed");
    }
```

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L295-297)
```java
    if (ArrayUtils.isEmpty(output.ovk) || output.ovk.length != 32) {
      throw new ZksnarkException("ovk is null or invalid and ovk should be 32 bytes (256 bit)");
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
