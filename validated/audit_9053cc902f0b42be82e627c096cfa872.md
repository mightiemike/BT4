### Title
Divergent multisig weight-dedup logic between `TransactionCapsule.checkWeight` and `PrecompiledContracts.ValidateMultiSign.execute` allows weight double-counting on-chain - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
`TransactionCapsule.checkWeight`, used by the HTTP `GetTransactionSignWeightServlet` preflight (via `TransactionUtil.getTransactionSignWeight`), rejects a signature set outright the moment any signer's address repeats, regardless of the raw signature bytes. `PrecompiledContracts.ValidateMultiSign.execute`, used on-chain by TVM contracts to validate multisig permissions, instead deduplicates on the exact `(recoveredAddr, sign-bytes)` pair only, and when the address repeats with *different* raw signature bytes for the same address it does not skip — it falls through and adds that key's weight again.

### Finding Description
`TransactionCapsule.checkWeight` (`chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java:233-270`) builds a `HashMap addMap` keyed by `base64` signature (pre-fork VERSION_4_7_1) or by `encode58Check(address)` (post-fork), and throws `PermissionException("... has signed twice!")` the instant a key is seen twice — i.e. as soon as the *same address* appears twice, weight computation aborts entirely, independent of whether the raw signature bytes differ. [1](#0-0) 

`PrecompiledContracts.ValidateMultiSign.execute` (`actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java:1086-1106`) instead tracks a flat `List<byte[]> executedSignList` containing both the merged `(recoveredAddr + sign)` entries and the bare `recoveredAddr` entries. For each signature it recovers the address, and only `continue`s (skips adding weight) if the *exact* merged `sign` bytes were already seen for that address:
```java
if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
  if (ByteArray.matrixContains(executedSignList, sign)) {
    continue;
  }
  MUtil.checkCPUTime();
}
long weight = TransactionCapsule.getWeight(permission, recoveredAddr);
...
totalWeight += weight;
executedSignList.add(sign);
executedSignList.add(recoveredAddr);
``` [2](#0-1) 

If the same address is seen again but with *different* raw signature bytes (e.g. via ECDSA malleability: `(r, N-s, 1-v)` recovers to the same address as `(r, s, v)` but is byte-for-byte different), the code does not `continue`; it falls through, calls `MUtil.checkCPUTime()`, and then unconditionally adds `weight` for that address to `totalWeight` again. This means a single private key can be counted multiple times toward `permission.getThreshold()` by supplying malleated variants of its own signature, which `TransactionCapsule.checkWeight` (keyed by address) would have flatly rejected as "has signed twice."

I was unable to fully verify in this session whether the underlying `recoverAddrBySign` → `ECKey`/`SM2` signature-recovery path enforces canonical low-S signatures (which would block trivial malleability) — `ECKey.java` does contain `HALF_CURVE_ORDER`/`isCanonical` references, but I could not confirm from the available context whether `recoverAddrBySign` (in `PrecompiledContracts.java`) actually calls into a canonical-form check before accepting the signature. This is a material gap: if canonical-form enforcement is present and applied on this path, the "different sign bytes, same address" branch may be unreachable via simple s/v flips and would require some other source of divergent-but-valid signature encodings (still plausible, e.g. different but semantically-equivalent signature serializations) to trigger the double-count. Without confirming this, I cannot assert the exploit is trivially reachable with a bare malleated `(r, N-s, 1-v)` pair specifically.

### Impact Explanation
If the double-count branch is reachable, a holder of a single key within a multisig `Permission` can satisfy a threshold that was intended to require multiple independent co-signers by submitting several *distinct* signature encodings that all recover to their one address, inflating `totalWeight` in `ValidateMultiSign.execute` beyond what `checkWeight` would ever allow for the identical signature set. This would let TVM contracts that gate value transfer, permission changes, or other privileged actions behind `validatemultisign(...)` be unlocked by a single signer instead of the configured quorum — a direct threshold-bypass / unauthorized-execution risk. It also produces a genuine behavioral divergence between the HTTP `GetTransactionSignWeightServlet` preflight (which would report `PERMISSION_ERROR`/rejection for the same signature array) and on-chain contract execution (which could report success), misleading callers who rely on the API as a preflight check.

### Likelihood Explanation
Preconditions: an attacker must control at least one key in a permission with `threshold > 1` and be able to produce more than one distinct valid signature over the same fixed hash from that key. ECDSA signature malleability makes producing a second valid-but-different byte encoding for the same `(privateKey, hash)` computationally trivial *if not blocked by canonical-signature enforcement on this specific verification path*. This is the unresolved uncertainty flagged above — I could not confirm from the retrieved code whether `recoverAddrBySign` rejects non-canonical `s` values before recovery. If it does not, the exploit is fully attacker-controlled, repeatable, and requires no privileged access — just crafting extra signature bytes for a contract call to `validatemultisign`/any TVM contract using this precompile.

### Recommendation
Make `ValidateMultiSign.execute`'s deduplication consistent with `TransactionCapsule.checkWeight`: deduplicate strictly by `recoveredAddr` (not by the `(address, signBytes)` pair), so that any repeat of the same address — regardless of raw signature bytes — is skipped without adding weight again, mirroring the "has signed twice" rejection semantics of the consensus/HTTP path. Additionally, confirm and, if absent, add canonical-signature (low-S) enforcement in the signature-recovery path used by this precompile to eliminate trivial malleability regardless of the dedup fix.

### Proof of Concept
```java
// Differential test: identical logical signer set, but attacker resubmits
// a second, malleated encoding of the SAME key's signature.
@Test
public void testMalleatedDuplicateDivergesFromCheckWeight() throws Exception {
  ECKey key1 = new ECKey();
  ECKey key2 = new ECKey(); // never actually signs
  // permission: threshold=2, keys=[key1(weight1), key2(weight1)]
  Permission permission = buildPermission(2, key1, key2);

  byte[] hash = someFixedHash();
  ECKey.ECDSASignature sig = key1.sign(hash);
  byte[] sigBytes = sig.toByteArray();
  // Malleate: s' = N - s, v' = flip(v) — same address recovers, different bytes.
  ECKey.ECDSASignature malleated = malleate(sig);
  byte[] malleatedBytes = malleated.toByteArray();

  List<ByteString> sigs = Arrays.asList(
      ByteString.copyFrom(sigBytes), ByteString.copyFrom(malleatedBytes));

  // 1) HTTP preflight path: must reject ("has signed twice") -> currentWeight never reaches threshold.
  boolean checkWeightThrew = false;
  try {
    TransactionCapsule.checkWeight(permission, sigs, hash, null);
  } catch (PermissionException e) {
    checkWeightThrew = true;
  }
  Assert.assertTrue("checkWeight must reject duplicate-address signer", checkWeightThrew);

  // 2) On-chain precompile path: build validatemultisign(address, permId, hash, [sigBytes, malleatedBytes]) calldata
  Pair<Boolean, byte[]> result = validateMultiSign(ownerAddress, permId, hash,
      Arrays.asList(Hex.toHexString(sigBytes), Hex.toHexString(malleatedBytes)));

  // Expected (if bug present): totalWeight = weight(key1)*2 >= threshold(2) -> returns DATA_ONE
  // despite only ONE distinct key having signed — divergent from checkWeight's rejection.
  Assert.assertArrayEquals(DataWord.ONE().getData(), result.getValue());
}
```
Expected outcome demonstrating the bug: `checkWeight` throws `PermissionException` (0 weight / rejection) for the two-signature array from a single key, while `ValidateMultiSign.execute` returns `DATA_ONE` (threshold met) for the identical logical signer set, confirming the divergence in "threshold met" outcome between the HTTP preflight and the on-chain precompile.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L242-269)
```java
    HashMap addMap = new HashMap();
    for (ByteString sig : sigs) {
      if (sig.size() < 65) {
        throw new SignatureFormatException(
            "Signature size is " + sig.size());
      }
      String base64 = TransactionCapsule.getBase64FromByteString(sig);
      byte[] address = SignUtils
          .signatureToAddress(hash, base64, CommonParameter.getInstance().isECKeyCryptoEngine());
      long weight = getWeight(permission, address);
      if (weight == 0) {
        throw new PermissionException(
            ByteArray.toHexString(hash) + " is signed by " + encode58Check(address)
                + " but it is not contained of permission.");
      }
      if (ForkController.instance().pass(Parameter.ForkBlockVersionEnum.VERSION_4_7_1)) {
        base64 = encode58Check(address);
      }
      if (addMap.containsKey(base64)) {
        throw new PermissionException(encode58Check(address) + " has signed twice!");
      }
      addMap.put(base64, weight);
      if (approveList != null) {
        approveList.add(ByteString.copyFrom(address)); //out put approve list.
      }
      currentWeight += weight;
    }
    return currentWeight;
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1086-1106)
```java
            long totalWeight = 0L;
            List<byte[]> executedSignList = new ArrayList<>();
            for (byte[] sign : signatures) {
              byte[] recoveredAddr = recoverAddrBySign(sign, hash);

              sign = merge(recoveredAddr, sign);
              if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
                if (ByteArray.matrixContains(executedSignList, sign)) {
                  continue;
                }
                MUtil.checkCPUTime();
              }
              long weight = TransactionCapsule.getWeight(permission, recoveredAddr);
              if (weight == 0) {
                //incorrect sign
                return Pair.of(true, DATA_FALSE);
              }
              totalWeight += weight;
              executedSignList.add(sign);
              executedSignList.add(recoveredAddr);
            }
```
