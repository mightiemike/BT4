### Title
Pre-fork signature-key duplication allows weight double-counting via malleable signature encodings - ([File: chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java])

### Summary
`checkWeight` deduplicates signatures using the raw base64-encoded signature bytes as the `addMap` key when `ForkController.instance().pass(VERSION_4_7_1)` is false, only switching to address-based deduplication when the fork is active. This allows an attacker who controls a single key with insufficient weight to submit two byte-distinct signatures (e.g., malleable `s`/`n-s` and recomputed recovery-id variants) that both recover to the same address, causing that key's weight to be counted twice.

### Finding Description
`TransactionCapsule.checkWeight` (lines 233-270) computes, for every signature in `sigs`, the recovered address via `SignUtils.signatureToAddress`, and only re-keys `addMap` by `encode58Check(address)` when the `VERSION_4_7_1` fork has passed: [1](#0-0) 
Before the fork activates, `base64` (the raw signature's base64 string) is used as the dedup key instead of the recovered address. If an attacker can produce two different byte encodings of a valid signature for the same private key that both pass `SignUtils.signatureToAddress` and recover to the same address, `addMap.containsKey(base64)` returns `false` for the second occurrence (different bytes), so both are accepted, and `currentWeight` accrues the key's weight twice. This directly violates the intended invariant that each authorized key contributes weight exactly once regardless of encoding.

### Impact Explanation
If exploitable, an attacker who legitimately controls one key of a multisig `Permission` with weight below `threshold` could inflate `currentWeight` to reach `threshold` without cooperation from other required co-signers, letting a transaction such as `AccountPermissionUpdateContract` pass authorization checks it should not (unauthorized permission/account takeover), matching an "unauthorized state change" bounty class.

### Likelihood Explanation
I was unable to fully verify, within the available context, whether the underlying signature/verification primitives (`ECKey.signatureToAddress`, `SM2.signatureToAddress`, and the base64 encode/decode path in `SignUtils`) actually permit constructing two *byte-distinct* 65+ byte signature encodings for the same key/hash that both (a) pass the `sig.size() < 65` format check and (b) independently recover to the same address via `SignUtils.signatureToAddress`. Standard secp256k1 ECDSA signature verification/recovery as typically implemented in this codebase includes a defined recovery id (`v`) per signature, and address recovery is tried across `v` candidates or fixed by construction—so whether an attacker can cheaply generate a second, different-byte encoding for the *same* signature without re-signing (which would just produce a different but equally legitimate single-use signature, not a "duplicate" in a meaningfully exploitable sense) depends on internals I could not fully inspect within the tool budget (`crypto/src/main/java/org/tron/common/crypto/ECKey.java`, `crypto/src/main/java/org/tron/common/crypto/sm2/SM2.java`, `SignUtils.java`). The dedup-key discrepancy pre-fork is real and confirmed in code, but I cannot confirm from the available context that a genuinely malleable/distinct-bytes-same-recovery signature pair is practically constructible against this specific signature scheme and encoding, nor whether other call sites already reject duplicate raw signatures before reaching `checkWeight`.

### Recommendation
Deduplicate `addMap` by the recovered address (`encode58Check(address)`) unconditionally, rather than gating this behavior behind the `VERSION_4_7_1` fork check, so that weight is counted per unique authorized key/address regardless of fork state or signature encoding.

### Proof of Concept
Unable to produce a concrete, verified PoC. A conclusive JUnit PoC would need to demonstrate producing two byte-distinct valid signatures (via `ECKey.sign`/`SM2.sign` low-level APIs) for the same key and message hash that both pass `SignUtils.signatureToAddress` recovery to the same address, then call `TransactionCapsule.checkWeight` with the dynamic properties/fork state pinned below `VERSION_4_7_1` and assert `currentWeight == 2 * singleKeyWeight`. This requires low-level access to the ECDSA/SM2 signing internals to confirm malleable-encoding generation is actually possible in this codebase, which I could not verify within the available tool budget.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L248-263)
```java
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
```
