### Title
Pre-fork VERSION_4_7_1 signature-threshold bypass via duplicate ECDSA signatures from a single key - ([File: chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java])

### Summary
`TransactionCapsule.checkWeight` deduplicates signatures using a `HashMap` keyed by the raw base64-encoded signature bytes unless the `VERSION_4_7_1` fork has already activated, in which case the key is switched to the recovered address. Before that fork activates, one private key can produce two distinct valid ECDSA signatures (different nonce `k`) over the same transaction hash, both of which recover to the same address but have different base64 encodings, so the "has signed twice" guard fails to catch them and the same key's weight is counted twice.

### Finding Description
In `checkWeight`, for each signature in the transaction's signature list, the code computes `base64 = getBase64FromByteString(sig)` from the raw signature bytes, then only *replaces* `base64` with `encode58Check(address)` if `ForkController.instance().pass(VERSION_4_7_1)` is true: [1](#0-0) 

If the fork has not yet passed, `addMap` is keyed by the signature bytes' base64 string rather than by the signer's address. Since ECDSA signing is non-deterministic unless RFC 6979 is strictly enforced across all code paths, the same private key can sign the identical hash twice (e.g. via two calls to the signing routine, or by an attacker directly crafting a second `(r,s)` pair with a different ephemeral `k`), producing two different byte sequences that both recover to the same address via `SignUtils.signatureToAddress`. Both entries have distinct `base64` keys, so `addMap.containsKey(base64)` returns `false` both times, `weight` is added to `currentWeight` twice, and the "has signed twice" `PermissionException` is never thrown.

This directly undermines the invariant that `currentWeight` reflects the number of distinct real signers weighed against `permission.getThreshold()`, allowing a single key to reach a multisig threshold that should require multiple distinct approvers.

### Impact Explanation
Any transaction validated through `checkWeight` under an Active/Owner permission with `threshold` set such that `single_key_weight < threshold <= 2 * single_key_weight` can be authorized by one attacker-controlled key alone, including `AccountPermissionUpdateContract` (permission escalation/takeover) and any other contract type gated by multisig permissions (transfers, exchanges, withdrawals, etc.). This is a signer-threshold bypass, not merely a cosmetic issue.

### Likelihood Explanation
Requires: (1) the chain has not yet activated `VERSION_4_7_1` (a historical fork condition — not attacker controlled, but a real precondition that existed on-chain before the fork height), and (2) a permission whose threshold is between 1x and 2x a single key's weight — a common and legitimate multisig configuration. Given those preconditions, the attack requires no privilege beyond holding one key with sufficient weight and being able to produce two signatures over the same hash (trivial with standard ECDSA libraries not enforcing RFC 6979 determinism, or by directly constructing a second valid `(r,s)` pair). Exploitability is deterministic and repeatable pre-fork.

### Recommendation
Always key `addMap` by the recovered address (or public key) rather than by raw signature bytes, regardless of fork state — remove the conditional branch so `base64 = encode58Check(address)` unconditionally, eliminating any pre-fork window where duplicate signatures from one key are undercounted.

### Proof of Concept
```java
// Unit test sketch, place under chainbase or actuator test tree
@Test
public void testDuplicateSignatureFromSameKeyDoubleCountsWeight() throws Exception {
  // Simulate fork not yet active
  // (mock ForkController.instance().pass(VERSION_4_7_1) to return false)

  ECKey key = new ECKey();
  byte[] hash = Sha256Hash.hash(true, "test-tx-hash".getBytes());

  // Produce two distinct signatures over the same hash from the same key
  // e.g. by calling sign() twice with different random k, or crafting
  // a malleable/alternate (r,s) pair recovering to the same address.
  ECDSASignature sig1 = key.sign(hash);
  ECDSASignature sig2 = craftAlternateSignature(key, hash); // different k, same address on recovery

  ByteString sigBytes1 = ByteString.copyFrom(sig1.toByteArray());
  ByteString sigBytes2 = ByteString.copyFrom(sig2.toByteArray());

  Permission permission = Permission.newBuilder()
      .setThreshold(2)
      .addKeys(Key.newBuilder().setAddress(ByteString.copyFrom(key.getAddress())).setWeight(1))
      .build();

  List<ByteString> sigs = Arrays.asList(sigBytes1, sigBytes2);

  long currentWeight = TransactionCapsule.checkWeight(permission, sigs, hash, null);

  // BUG: currentWeight == 2 even though only one distinct key/address signed
  assertEquals(2L, currentWeight);
  assertTrue(currentWeight >= permission.getThreshold());
}
```
Expected pre-fix behavior: `checkWeight` returns `2` and satisfies `threshold`, confirming the bypass with only one real signer. After applying the fix (always key by address), the second call should throw `PermissionException` ("... has signed twice!").

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
