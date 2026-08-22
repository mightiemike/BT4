### Title
Signature-malleability enables double-counting of a single co-signer's weight in `ValidateMultiSign.execute` - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
`PrecompiledContracts.ValidateMultiSign.execute` deduplicates signer contributions by checking whether `merge(recoveredAddr, sign)` (the raw signature bytes concatenated with the recovered address) already exists in `executedSignList`, instead of deduplicating solely on `recoveredAddr`. Because two byte-distinct signatures (e.g., an ECDSA signature and its canonical malleable counterpart `(r, n-s, flipped v)`) can recover to the *same* address, the merged-byte check does not match on the second occurrence, and `TransactionCapsule.getWeight(permission, recoveredAddr)` is added to `totalWeight` a second time for the same real signer.

### Finding Description
The relevant loop is: [1](#0-0) 

For each signature:
1. `recoveredAddr = recoverAddrBySign(sign, hash)` recovers the signer address via `Rsv.fromSignature` + `SignUtils.fromComponents` + `SignUtils.signatureToAddress`. [2](#0-1) 
2. `sign = merge(recoveredAddr, sign)` builds a combined byte array of address+raw signature bytes.
3. The dedup check is `matrixContains(executedSignList, recoveredAddr)` (has this address been seen before) followed by `matrixContains(executedSignList, sign)` (has this *exact* address+raw-signature combination been seen before). Only if both are true does the loop `continue` (skip re-adding weight).

The flaw: on the first occurrence of address `A` via signature `sig1`, `executedSignList` stores `merge(A, sig1)` and `A`. On a second, byte-distinct signature `sig2` that also recovers to `A` (e.g. produced via ECDSA signature malleability by negating `s` modulo curve order and flipping the recovery id `v`, which yields the same recovered public key/address but different raw bytes), the check `matrixContains(executedSignList, A)` is true, but `matrixContains(executedSignList, merge(A, sig2))` is false, because the raw bytes differ from `merge(A, sig1)`. The inner `continue` is therefore skipped, weight for `A` is fetched again via `TransactionCapsule.getWeight(permission, A)`, and `totalWeight` is incremented a second time for the same underlying key. `sig2`'s entry is also appended to `executedSignList`, but that does not undo the double addition already made to `totalWeight`.

This lets an attacker with a *single* private key (no real second co-signer) craft two malleable encodings of their own signature to be counted as two independent co-signers, potentially crossing `permission.getThreshold()` when the actual number of distinct keys used is fewer than required.

Nothing else in the call path blocks this: `recoverAddrBySign` only calls `signature.validateComponents()` (which enforces basic component bounds, not S-canonicality/low-S uniqueness) before recovery, and there is no check anywhere in `ValidateMultiSign.execute` that rejects duplicate `recoveredAddr` values outright — the loop's *intended* dedup key is the address, but the actual dedup key used in the early-exit branch is the raw signature bytes merged with the address.

### Impact Explanation
This falls under Authorization Enforced / asset & accounting corruption: any TVM contract that gates fund transfers or privileged actions behind `ValidateMultiSign` (e.g. custom multisig wallets, escrow, or bridge contracts built on top of TRON account permissions) can be tricked into believing a multisig threshold was met using fewer distinct real co-signers than the permission's `keys` configuration requires. This is a cross-permission-threshold bypass reachable purely by a TVM contract call from an unprivileged account — no witness/SR/committee role or leaked keys needed, just the attacker's own key(s) and knowledge of signature malleability.

### Likelihood Explanation
- Precondition: attacker must control at least one signer key that is part of the target permission (or be the account owner attempting to bypass a higher threshold using their own single key twice), and must deploy or call a contract that invokes the `ValidateMultiSign` precompile.
- Cost: normal TVM call/energy cost of invoking the precompile (`ENGERYPERSIGN` = 1500 per signature entry, up to `MAX_SIZE` = 5 signatures) — no privileged access or unusual fees.
- Constructing a malleable variant of an ECDSA signature (flip `s -> n-s`, flip recovery bit) is a well-known, cheap, deterministic operation requiring no additional information beyond the original valid signature.
- Fully repeatable and controllable by the attacker; not probabilistic.

### Recommendation
Change the dedup logic in `ValidateMultiSign.execute` to key strictly on `recoveredAddr`, not on `merge(recoveredAddr, sign)`. As soon as `matrixContains(executedSignList, recoveredAddr)` is true, `continue` immediately (skip weight accumulation) regardless of the raw signature bytes, since the invariant that matters is "one weight contribution per unique recovered address," not "per unique signature byte string." Optionally also reject or canonicalize malleable signatures (enforce low-S) at signature parse time for defense in depth.

### Proof of Concept
```java
// JUnit-style outline (extends ValidateMultiSignContractTest fixtures)
@Test
public void testMalleableSignatureDoubleCounting() {
  // permission requires threshold that needs >1 distinct signer weight,
  // but only ONE real key is available to the attacker.
  ECKey key = new ECKey();
  byte[] hash = ...; // combine(address, permissionId, data) hash as computed by execute()
  ECKey.ECDSASignature sig1 = key.sign(hash);
  // Malleable transform: s' = CURVE_ORDER - s, flip recovery id
  ECKey.ECDSASignature sig2 = sig1.toCanonicalised() /* or manual malleable variant */;
  // sig1 and sig2 recover to the SAME address but differ byte-for-byte.

  List<Object> signatures = Arrays.asList(
      Hex.toHexString(sig1.toByteArray()),
      Hex.toHexString(sig2.toByteArray()));

  Pair<Boolean, byte[]> result = validateMultiSign(address, permissionId, hash, signatures);

  // Expected (fixed) behavior: DATA_FALSE, because only one distinct signer
  // weight should count toward totalWeight.
  Assert.assertArrayEquals(result.getValue(), DataWord.ZERO().getData());

  // Current (vulnerable) behavior: if attacker's single-key weight * 2 >=
  // permission.getThreshold(), result is dataOne() (DATA_TRUE) despite only
  // one real co-signer having participated.
}
``` [3](#0-2)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L371-388)
```java
  private static byte[] recoverAddrBySign(byte[] sign, byte[] hash) {
    byte[] out = null;
    if (ArrayUtils.isEmpty(sign) || sign.length < 65) {
      return new byte[0];
    }
    try {
      Rsv rsv = Rsv.fromSignature(sign);
      SignatureInterface signature = SignUtils.fromComponents(rsv.getR(), rsv.getS(), rsv.getV(),
          CommonParameter.getInstance().isECKeyCryptoEngine());
      if (signature.validateComponents()) {
        out = SignUtils.signatureToAddress(hash, signature,
            CommonParameter.getInstance().isECKeyCryptoEngine());
      }
    } catch (Throwable any) {
      logger.info("ECRecover error", any.getMessage());
    }
    return out;
  }
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
