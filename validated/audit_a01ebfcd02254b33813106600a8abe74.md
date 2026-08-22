### Title
Signature-malleability driven double-counting of a single signer's weight in `ValidateMultiSign` bypasses the distinct-signer threshold check - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
`PrecompiledContracts.ValidateMultiSign.execute()` deduplicates signatures by comparing the raw, byte-exact signature blob (`recoveredAddr + sign`) rather than the recovered address alone. Because ECDSA signatures are malleable (a valid `(r, s, v)` triple has an equally valid `(r, n-s, 1-v)` counterpart that recovers to the identical address), an attacker can submit two syntactically different signature byte arrays for the same key and have the precompile add that key's weight twice, letting a single signer's weight satisfy a multi-signer `permission.getThreshold()`.

### Finding Description
In `execute()`:
```
actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java:1086-1106
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
  ...
  totalWeight += weight;
  executedSignList.add(sign);
  executedSignList.add(recoveredAddr);
}
```
`ByteArray.matrixContains` does an exact `Arrays.equals` byte comparison (`common/src/main/java/org/tron/common/utils/ByteArray.java:189-196`). The dedup logic only `continue`s (skips adding weight again) when the *exact same raw signature bytes* were already processed. If `recoveredAddr` matches a prior entry but the new signature's raw bytes differ (e.g., a malleable variant `(r, n-s, v')` of the same key's signature over the same `hash`, computed via `recoverAddrBySign` -> `SignUtils.signatureToAddress` at line 371-388), the code falls through, re-resolves the same key's weight via `TransactionCapsule.getWeight(permission, recoveredAddr)` (`chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java:218-226`), and adds it to `totalWeight` again.

This differs from the on-chain transaction-signature path (`TransactionCapsule.checkWeight`, `chainbase/.../TransactionCapsule.java:233-270`), which dedups by **address** (`addMap.containsKey(base64)`) and explicitly throws `"has signed twice!"` — i.e., the correct invariant (distinct signer identity) is enforced there but not in the TVM precompile.

`recoverAddrBySign` only calls `signature.validateComponents()` before recovery (line 380); nothing in the visible code path enforces canonical/low-`s` signatures, so a caller can freely construct the flipped `(r, n-s, v XOR 1)` variant of any signature they already hold and pass both variants into the `signatures[]` array supplied to the precompile.

### Impact Explanation
A dApp or on-chain contract that trusts `ValidateMultiSign`'s boolean result to gate a privileged action (e.g., releasing funds, approving a multisig operation, authorizing an account-linked operation) can be tricked into accepting a threshold-2 (or higher) approval using signatures from only a single distinct real signer, by supplying that signer's signature plus its ECDSA-malleable twin. This is an authorization-bypass / unauthorized-account-operation impact category: the on-chain caller believes N independent keyholders approved an action when only 1 keyholder actually did.

### Likelihood Explanation
- Attacker precondition: only needs one valid signature from any authorized key for the target permission (which they can normally already influence/obtain if they are one of the keys, or which may leak via other channels/oracles the dApp exposes) plus the ability to derive its malleable twin — a purely local, deterministic elliptic-curve computation (`s' = n - s`, flip recovery id), requiring no cryptographic secret.
- Attacker calls this from an ordinary smart contract via `TriggerSmartContract`, paying only the standard energy cost (`ENGERYPERSIGN = 1500` per signature slot, `MAX_SIZE = 5`), no privileged role needed.
- Fully repeatable and deterministic; no race condition or timing dependency.
- Caveat: whether `signature.validateComponents()` (in `ECKey`/`SignUtils`) rejects non-canonical high-`s` values could not be fully confirmed in this pass — if it did enforce strict low-`s` canonicality, the malleable twin would be rejected at recovery time. This must be verified in `ECKey.java`'s `ECDSASignature`/`validateComponents` implementation before treating this as conclusively exploitable in production.

### Recommendation
Change the dedup key in `ValidateMultiSign.execute()` from raw signature bytes to `recoveredAddr` alone (mirroring `TransactionCapsule.checkWeight`'s address-based dedup): once an address has contributed weight, any further signature recovering to that same address should be skipped entirely, regardless of its raw bytes. Additionally, enforce canonical (low-`s`) signature components in `recoverAddrBySign`/`validateComponents` to eliminate malleable signature variants generally.

### Proof of Concept
```java
// Illustrative JUnit sketch (exact malleable-twin construction depends on
// ECKey/ECDSASignature internals not fully inspected here).
ECKey key1 = new ECKey();
ECKey key2 = new ECKey();
// permission threshold = 2, key1 weight=1, key2 weight=1 (key2 never signs)

byte[] sig1 = key1.sign(toSign).toByteArray();           // (r, s, v)
byte[] sig1Malleable = flipSToCurveOrderMinusS(sig1);     // (r, n-s, v^1), same recovered address

List<Object> signs = Arrays.asList(
    Hex.toHexString(sig1),
    Hex.toHexString(sig1Malleable));   // only key1 ever signed

Pair<Boolean, byte[]> ret = validateMultiSign(
    StringUtil.encode58Check(key.getAddress()), permissionId, data, signs);

// BUG: expected DataWord.ZERO() (only 1 distinct signer, threshold=2 not met)
// but if malleable twin isn't byte-identical to sig1, totalWeight becomes 2
// and result is DataWord.ONE(), i.e. threshold satisfied with 1 real signer.
Assert.assertArrayEquals(DataWord.ZERO().getData(), ret.getValue());
```
This test needs a helper `flipSToCurveOrderMinusS` that computes `n - s` (secp256k1 order) and toggles the recovery id byte, confirming that `recoverAddrBySign` still returns `key1`'s address for the malleable twin.