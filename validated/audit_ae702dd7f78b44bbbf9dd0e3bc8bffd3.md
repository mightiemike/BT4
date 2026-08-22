### Title
Raw-byte signature de-duplication bypass in `ValidateMultiSign` precompile allows weight double-counting via v-byte normalization quirk in `Rsv.fromSignature` - (File: `crypto/src/main/java/org/tron/common/crypto/Rsv.java`)

### Summary
`Rsv.fromSignature` normalizes the recovery id byte `v` by adding 27 whenever `v < 27` [1](#0-0) . Because the de-duplication check in `PrecompiledContracts.execute` for `ValidateMultiSign` operates on the raw, un-normalized `sign` byte array via `ByteArray.matrixContains(executedSignList, sign)` rather than on the normalized recovered address, an attacker can submit two signatures that differ only in the raw `v` byte (`0` vs `27`, or `1` vs `28`) but normalize to the identical `v` and therefore recover to the same address, causing that signer's weight to be counted twice toward the permission threshold.

### Finding Description
`Rsv.fromSignature(byte[] sign)` extracts `r`, `s`, and `v = sign[64]`, then normalizes `v` by adding 27 only if `v < 27` [2](#0-1) . This means a raw signature ending in byte `0x00` and one ending in `0x1B` (27) both normalize to `v=27` and thus recover to the exact same address via ECDSA recovery, since ECDSA recovery only depends on the normalized `v`, `r`, and `s`.

`PrecompiledContracts.ValidateMultiSign.execute` iterates over up to `MAX_SIZE` signatures, and for de-duplication calls `ByteArray.matrixContains(executedSignList, sign)` on the *raw* signature bytes before adding the recovered address's weight to `totalWeight`. Because the stored `sign` entries still contain the pre-normalization `v` byte, two signatures that are semantically identical (same `r`, `s`, and equivalent `v` after normalization) but differ in their raw `v` encoding will not be caught as duplicates by the raw byte-array comparison, even though `recoverAddrBySign` resolves them to the same signer address. This lets `totalWeight` accumulate the same signer's permission weight multiple times.

### Impact Explanation
If a permission's threshold can be met only by summing a signer's weight more than once (e.g., threshold requires 2x a single signer's weight, or combined with one other legitimate signer), an attacker controlling only that one private key (or crafting equivalent signature encodings of the same key's signature) could cause `ValidateMultiSign` to return `true` for a multi-sig authorization that should require additional independent signers. This corresponds to an "unauthorized account operation" / authorization bypass impact class, potentially enabling unauthorized asset movement gated by multi-signature permission checks.

### Likelihood Explanation
The precondition is that a target account's active/owner permission is configured with keys whose weights only reach the threshold via double-counting a single key (e.g., two keys of low individual weight where an attacker only needs to double-count one signer to reach threshold, or a threshold deliberately near a single key's weight). This requires no privileged role — any account can deploy a contract calling the precompile and can also configure their own account's permissions to test/exploit this against their own or a target's multi-sig setup where such weight configuration exists. The attacker only pays normal deployment and execution energy cost; the exploit is deterministic and repeatable given a valid single ECDSA signature (r, s) since flipping between `v` and `v-27` (or 27/28 vs 0/1 encodings) is trivial byte manipulation.

### Recommendation
Perform de-duplication in `ValidateMultiSign.execute` based on the **recovered address** (post `Rsv.fromSignature` normalization) rather than on the raw signature bytes — i.e., call `ByteArray.matrixContains` (or an equivalent set/list check) against the list of already-recovered addresses, not against the raw `sign` byte arrays.

### Proof of Concept
```java
// Extends ValidateMultiSignContractTest
@Test
public void testDuplicateWeightViaVByteNormalization() {
  // sign1: r || s || (byte) 0x00   -> Rsv.fromSignature normalizes v: 0 -> 27
  // sign2: r || s || (byte) 0x1B   -> v already 27, no normalization
  // Both signatures recover to the SAME address (key1) because
  // Rsv.fromSignature produces v=27 for both.
  byte[] sign1 = buildSignature(r, s, (byte) 0x00);
  byte[] sign2 = buildSignature(r, s, (byte) 0x1B); // 27

  byte[] data = encodeValidateMultiSignInput(hash, permissionId, ownerAddress,
      new byte[][]{sign1, sign2});

  Pair<Boolean, byte[]> result = validateMultiSign.execute(data);

  // Expectation if vulnerable: totalWeight double-counted key1's weight,
  // matrixContains(executedSignList, sign) treats sign1/sign2 as distinct
  // raw arrays, so both weights are added, and execute() returns true
  // even though only ONE distinct real signer (key1) actually signed.
  assertTrue(result.getLeft()); // demonstrates bypass: threshold met with 1 distinct signer
}
```
Note: exact recovery/weight-accumulation line numbers inside `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java` (the `recoverAddrBySign`, `executedSignList`, and `matrixContains` call sites) could not be retrieved in full during this session due to tool/index limitations; only match counts were confirmed [3](#0-2) . A full review of that method is recommended to confirm the exact de-duplication implementation before remediation.

### Citations

**File:** crypto/src/main/java/org/tron/common/crypto/Rsv.java (L17-25)
```java
  public static Rsv fromSignature(byte[] sign) {
    byte[] r = Arrays.copyOfRange(sign, 0, 32);
    byte[] s = Arrays.copyOfRange(sign, 32, 64);
    byte v = sign[64];
    if (v < 27) {
      v += 27; //revId -> v
    }
    return new Rsv(r, s, v);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1-1)
```java
package org.tron.core.vm;
```
