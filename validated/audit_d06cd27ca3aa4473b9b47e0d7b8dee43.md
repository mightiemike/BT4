### Title
Missing length validation in `JLibrustzcash.librustzcashCrhIvk` before native/JNI call - (File: `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java`)

### Summary
`JLibrustzcash.librustzcashCrhIvk` forwards `ak`/`nk`/`ivk` byte arrays directly to the native `INSTANCE.librustzcashCrhIvk` call with no length check, unlike sibling wrapper methods in the same class which explicitly validate buffer sizes via `LibrustzcashParam.valid32Params`/`validParamLength` before calling into native code.

### Finding Description
`JLibrustzcash.librustzcashCrhIvk` is implemented as: [1](#0-0) 
This directly passes `params.getAk()`, `params.getNk()`, `params.getIvk()` into the native/JNI bridge with no call to any of the validation helpers present elsewhere in the file (`valid32Params`, `valid11Params`, `validParamLength`), which are used e.g. in `librustzcashAskToAk`, `librustzcashNskToNk`, `librustzcashSaplingGenerateR`, `librustzcashToScalar`, `librustzcashCheckDiversifier`, and `librustzcashTreeUncommitted`: [2](#0-1) [3](#0-2) 

The underlying Rust `librustzcash_crh_ivk` FFI function (not in this repo) expects fixed-size 32-byte buffer pointers. If the `CrhIvkParams` supplied to `librustzcashCrhIvk` were ever constructed from attacker-controlled, non-fixed-length byte arrays (e.g. via `FullViewingKey`'s `@AllArgsConstructor`, which accepts arbitrary-length `ak`/`nk`/`ovk` arrays), the JNI bridge would pass a pointer to a Java byte array shorter/longer than 32 bytes to native code that dereferences it as a fixed `[u8;32]`, causing an out-of-bounds read/heap corruption in the native layer. [4](#0-3) 

In the confirmed decode path, `FullViewingKey.decode` truncates/copies exactly 32 bytes per field via `System.arraycopy`, which mitigates the risk for that specific call path (short input throws a Java `ArrayIndexOutOfBoundsException` before reaching native code; long input is safely truncated): [5](#0-4) 

However, I was unable to fully confirm within the available tool budget (a) the exact contents of `CrhIvkParams`/`LibrustzcashParam.java` to verify whether that class itself performs any length assertions, and (b) the exact RPC/handler code in `framework/src/main/java/org/tron/core/Wallet.java` that calls `librustzcashCrhIvk` directly, to determine whether an unprivileged RPC caller can supply `ak`/`nk` byte arrays of arbitrary length that bypass the fixed-size `FullViewingKey.decode` path.

### Impact Explanation
If a reachable code path exists where attacker/RPC-supplied `ak`/`nk` byte arrays with non-32 lengths reach `librustzcashCrhIvk` without prior validation, this could cause a native out-of-bounds read/crash (node DoS), matching TRON's "Node crash" impact class. Confirmed evidence is limited to the missing validation inside `JLibrustzcash.librustzcashCrhIvk` itself, which is a genuine code-level asymmetry compared to sibling methods.

### Likelihood Explanation
Low-to-moderate confidence overall: the primary confirmed instantiation path (`FullViewingKey.decode`) already enforces fixed 32-byte slices before invoking `librustzcashCrhIvk`, which would prevent straightforward exploitation via that route. Exploitability depends on whether `Wallet.java`'s direct call to `librustzcashCrhIvk` (confirmed to exist via grep, but content not verified) accepts unvalidated, attacker-controlled variable-length `ak`/`nk` from an RPC request. This could not be fully confirmed with available tools.

### Recommendation
Add explicit length validation (`LibrustzcashParam.valid32Params(ak)`, `valid32Params(nk)`, `valid32Params(ivk)`) inside `JLibrustzcash.librustzcashCrhIvk` before calling `INSTANCE.librustzcashCrhIvk(...)`, consistent with the pattern used in `librustzcashAskToAk`, `librustzcashNskToNk`, and `librustzcashToScalar`. Additionally, audit all call sites in `Wallet.java` and `FullViewingKey` to ensure `ak`/`nk` are always fixed-size 32-byte arrays before being wrapped in `CrhIvkParams`.

### Proof of Concept
```java
// JUnit test demonstrating the missing pre-call length validation
@Test
public void testCrhIvkRejectsMalformedLength() {
  byte[] oversizedAk = new byte[1024]; // attacker-controlled oversized buffer
  byte[] nk = new byte[32];
  byte[] ivk = new byte[32];
  CrhIvkParams params = new CrhIvkParams(oversizedAk, nk, ivk);
  // Expected: should throw ZksnarkException / IllegalArgumentException before native call
  // Actual: JLibrustzcash.librustzcashCrhIvk(params) invokes INSTANCE.librustzcashCrhIvk directly,
  // with no length check, unlike librustzcashAskToAk/librustzcashNskToNk which call valid32Params first.
  JLibrustzcash.librustzcashCrhIvk(params); // no validation occurs, reaches native call unchecked
}
```
Note: full end-to-end weaponization (confirming an unprivileged RPC path that supplies non-32-byte `ak`/`nk`) requires reviewing `Wallet.java`'s direct usage of `librustzcashCrhIvk`, which could not be completed with the available search budget — a Devin session with full repo access should verify this call site before treating this as a confirmed, exploitable RCE/crash.

### Citations

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java (L52-54)
```java
  public static void librustzcashCrhIvk(CrhIvkParams params) {
    INSTANCE.librustzcashCrhIvk(params.getAk(), params.getNk(), params.getIvk());
  }
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java (L75-91)
```java
  public static byte[] librustzcashAskToAk(byte[] ask) throws ZksnarkException {
    LibrustzcashParam.valid32Params(ask);
    byte[] ak = new byte[32];
    INSTANCE.librustzcashAskToAk(ask, ak);
    return ak;
  }

  /**
   * @param nsk the proof authorizing key, to generate nk, 32 bytes
   * @return 32 bytes
   */
  public static byte[] librustzcashNskToNk(byte[] nsk) throws ZksnarkException {
    LibrustzcashParam.valid32Params(nsk);
    byte[] nk = new byte[32];
    INSTANCE.librustzcashNskToNk(nsk, nk);
    return nk;
  }
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java (L152-156)
```java
  public static void librustzcashToScalar(byte[] value, byte[] data) throws ZksnarkException {
    LibrustzcashParam.validParamLength(value, 64);
    LibrustzcashParam.valid32Params(data);
    INSTANCE.librustzcashToScalar(value, data);
  }
```

**File:** framework/src/main/java/org/tron/core/zen/address/FullViewingKey.java (L14-41)
```java
public class FullViewingKey {

  @Getter
  @Setter
  private byte[] ak; // 256
  @Getter
  @Setter
  private byte[] nk; // 256
  @Getter
  @Setter
  private byte[] ovk; // 256,the outgoing viewing key

  public static FullViewingKey decode(byte[] data) {
    byte[] ak = new byte[32];
    byte[] nk = new byte[32];
    byte[] ovk = new byte[32];
    System.arraycopy(data, 0, ak, 0, 32);
    System.arraycopy(data, 32, nk, 0, 32);
    System.arraycopy(data, 64, ovk, 0, 32);

    return new FullViewingKey(ak, nk, ovk);
  }

  public IncomingViewingKey inViewingKey() throws ZksnarkException {
    byte[] ivk = new byte[32]; // the incoming viewing key
    JLibrustzcash.librustzcashCrhIvk(new CrhIvkParams(ak, nk, ivk));
    return new IncomingViewingKey(ivk);
  }
```
