### Title
Pre-fork `checkWeight` de-duplicates on signature-bytes-derived base64 key instead of recovered signer address, allowing ECDSA-malleated re-signs to double-count the same key's weight - (File: `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java`)

### Summary
`TransactionCapsule.checkWeight` de-duplicates signers using a `HashMap` keyed by `base64` (derived from the raw `(r, s, v)` signature bytes via `getBase64FromByteString`) unless `ForkController.instance().pass(VERSION_4_7_1)` is active, in which case the key becomes `encode58Check(address)`. [1](#0-0)  Because the pre-fork key is derived from the signature encoding rather than the recovered address, two distinct-but-malleated ECDSA signatures over the same hash by the same key can produce two different `base64` strings while both recovering to the same address, bypassing the `addMap.containsKey(base64)` check.

### Finding Description
`checkWeight` accepts any signature of `sig.size() >= 65` and, for each one, computes:

```java
String base64 = TransactionCapsule.getBase64FromByteString(sig);
byte[] address = SignUtils.signatureToAddress(hash, base64, ...);
``` [2](#0-1) 

`getBase64FromByteString` parses the first 65 bytes into `r`, `s`, `v` via `Rsv.fromSignature` and re-encodes them with `ECDSASignature.fromComponents(r, s, v).toBase64()`. [3](#0-2) [4](#0-3) 

The de-dup key is switched depending on fork status:
```java
if (ForkController.instance().pass(Parameter.ForkBlockVersionEnum.VERSION_4_7_1)) {
  base64 = encode58Check(address);
}
if (addMap.containsKey(base64)) {
  throw new PermissionException(...);
}
addMap.put(base64, weight);
``` [5](#0-4) 

This fork-gated rewrite from a signature-derived key to an address-derived key is itself strong evidence that the base64-keyed de-dup was address-identity-unsound: ECDSA over secp256k1 (used when `isECKeyCryptoEngine()` is true, routed through `ECKey.signatureToAddress`) is malleable — for a valid `(r, s, v)` signature, the pair `(r, n-s, v')` (with `v'` the flipped recovery id, `n` the curve order) is also a valid signature that recovers to the identical public key/address, since standard recovery-based ECDSA verification (as used for `ecrecover`-style address recovery in Ethereum-style tooling) does not by itself enforce a canonical low-`s` value on recovery. Because `base64` pre-fork is computed purely from `(r, s, v)` bytes and not from the recovered address, the malleated variant produces a **different** `base64` string while resolving to the **same** `address` via `SignUtils.signatureToAddress`. Consequently:

- `addMap.containsKey(base64)` fails to detect the repeat (different key string).
- `addMap.put(base64, weight)` inserts a second entry.
- `currentWeight += weight` is added a second time for the same signer.

This is directly reachable by an unprivileged attacker via `broadcastTransaction`/`getTransactionSignWeight`/`addSign`, all of which call `checkWeight` on attacker-supplied signature lists over a transaction hash the attacker controls the pairing for. [6](#0-5) [7](#0-6) 

### Impact Explanation
If exploitable, a single private key holder can craft two malleated signatures over the same transaction hash, submit both in the `signature` list, and have `checkWeight` count the same key twice toward `permission.getThreshold()`. For a multisig account requiring e.g. 2 independent keys (threshold 2, weight 1 each), a single compromised/attacker-controlled key satisfies the threshold alone, letting the attacker execute transfers, votes, or account permission changes as the multisig owner without the other legitimate signer's cooperation. This is a threshold-bypass / authorization-integrity violation of the invariant "one signer contributes weight exactly once."

### Likelihood Explanation
Preconditions: (1) `ForkController.instance().pass(VERSION_4_7_1)` must be **false**, i.e. the fork has not yet activated on the target chain (a legitimate, attacker-independent chain-state precondition, not an admin action); (2) `isECKeyCryptoEngine()` uses the ECDSA/secp256k1 path (`ECKey`), and its recovery routine must not itself reject non-canonical/malleated `s` values. I could not fully verify the internals of `ECKey.recoverFromSignature`/`ECKey.signatureToAddress` within the available context to confirm the absence of a low-`s` canonical check on the recovery path — this is a genuine gap in my verification and should be checked directly in `crypto/src/main/java/org/tron/common/crypto/ECKey.java` before treating this as fully confirmed. If no such check exists (the common case for `ecrecover`-style recovery, where canonical-`s` policing is typically only enforced at signing time, not recovery time), the attack is deterministic and fully repeatable pre-fork with no rate limiting beyond normal transaction broadcast constraints (`sigs.size() > permission.getKeysCount()` bound and `getTotalSignNum()` cap, both trivially satisfied with 2 signatures for a 2-signer permission).

### Recommendation
Remove the fork-conditional branch and always de-duplicate on the recovered address (`encode58Check(address)` or raw `address` bytes) rather than any signature-byte-derived encoding, for all chains regardless of `VERSION_4_7_1` fork status. If the fork gate must remain for backward-compatibility of already-finalized blocks, ensure any new transaction validation path (post-current-height) unconditionally uses address-based de-duplication, and audit `ECKey`/`SM2` recovery routines to reject non-canonical signature malleations outright regardless of the `checkWeight` key choice.

### Proof of Concept
```java
// In WalletTest.java / a new TransactionCapsuleTest.java, pre-fork (VERSION_4_7_1 not active):
@Test
public void testCheckWeightMalleatedSignatureDoubleCounts() throws Exception {
  ECKey ecKey = new ECKey(Utils.getRandom());
  byte[] hash = Sha256Hash.hash(true, "test".getBytes());

  // Build a 2-of-2 permission with two distinct key slots, one of which is ecKey's address,
  // so keysCount is satisfiable with 2 signature slots.
  Permission permission = ... // threshold = 2, keys = [ecKey.address (weight1), otherKey.address (weight1)]

  ECKey.ECDSASignature sig = ecKey.sign(hash);
  byte[] recId = ...; // original recovery id used by ECKey.sign

  // Original signature bytes: r || s || v
  byte[] sigBytes1 = concat(sig.r, sig.s, v);

  // Malleated variant: s' = CURVE_ORDER - s, v' = flipped recovery id
  BigInteger sPrime = ECKey.CURVE.getN().subtract(sig.s);
  byte vPrime = flip(v);
  byte[] sigBytes2 = concat(sig.r, sPrime.toByteArray(), vPrime);

  // Confirm both recover to the SAME address:
  byte[] addr1 = SignUtils.signatureToAddress(hash, getBase64FromByteString(ByteString.copyFrom(sigBytes1)), true);
  byte[] addr2 = SignUtils.signatureToAddress(hash, getBase64FromByteString(ByteString.copyFrom(sigBytes2)), true);
  assertArrayEquals(addr1, addr2); // same signer

  List<ByteString> sigs = Arrays.asList(
      ByteString.copyFrom(sigBytes1),
      ByteString.copyFrom(sigBytes2));

  long weight = TransactionCapsule.checkWeight(permission, sigs, hash, new ArrayList<>());

  // EXPECTED (secure): weight should equal the single key's weight (e.g. 1), and/or
  // a PermissionException "has signed twice!" should be thrown.
  // ACTUAL (vulnerable, pre-fork): weight == 2 * singleKeyWeight, reaching threshold=2
  // with only one real signer.
  assertEquals(1L, weight); // fails pre-fork if malleability bypass exists
}
```
Note: this PoC's validity hinges on confirming that `ECKey`'s recovery path (`signatureToAddress`) accepts the malleated `(r, n-s, v')` pair as valid — a detail I was unable to fully verify against `ECKey.java` in this session and should be checked directly before relying on this finding as fully confirmed.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L242-263)
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
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L463-466)
```java
  public static String getBase64FromByteString(ByteString sign) {
    Rsv rsv = Rsv.fromSignature(sign.toByteArray());
    return ECDSASignature.fromComponents(rsv.getR(), rsv.getS(), rsv.getV()).toBase64();
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L468-496)
```java
  public static boolean validateSignature(Transaction transaction,
      byte[] hash, AccountStore accountStore, DynamicPropertiesStore dynamicPropertiesStore)
      throws PermissionException, SignatureException, SignatureFormatException {
    Transaction.Contract contract = transaction.getRawData().getContractList().get(0);
    int permissionId = contract.getPermissionId();
    byte[] owner = getOwner(contract);
    AccountCapsule account = accountStore.get(owner);
    Permission permission = null;
    if (account == null) {
      if (permissionId == 0) {
        permission = AccountCapsule.getDefaultPermission(ByteString.copyFrom(owner));
      }
      if (permissionId == 2) {
        permission = AccountCapsule
            .createDefaultActivePermission(ByteString.copyFrom(owner), dynamicPropertiesStore);
      }
    } else {
      permission = account.getPermissionById(permissionId);
    }
    if (permission == null) {
      throw new PermissionException("permission isn't exit");
    }
    checkPermission(permissionId, permission, contract);
    long weight = checkWeight(permission, transaction.getSignatureList(), hash, null);
    if (weight >= permission.getThreshold()) {
      return true;
    }
    return false;
  }
```

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

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L246-253)
```java
        if (trx.getSignatureCount() > 0) {
          List<ByteString> approveList = new ArrayList<>();
          long currentWeight = TransactionCapsule.checkWeight(permission, trx.getSignatureList(),
              Sha256Hash.hash(CommonParameter.getInstance()
                  .isECKeyCryptoEngine(), trx.getRawData().toByteArray()), approveList);
          tswBuilder.addAllApprovedList(approveList);
          tswBuilder.setCurrentWeight(currentWeight);
        }
```
