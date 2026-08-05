### Title
Multisig weight double-counting via ECDSA signature malleability in `ValidateMultiSign` - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
`ValidateMultiSign.execute` deduplicates signatures on the *exact byte content* of `merge(recoveredAddr, sign)`, not on the recovered address alone, so a single private key can be counted twice toward `totalWeight` by submitting two byte-distinct but validly-recovering signatures (e.g. the canonical `(r,s,v)` and its ECDSA-malleable counterpart `(r, N-s, v')`) for the same message hash. This lets an attacker with weight `w < threshold ≤ 2w` bypass multisig threshold enforcement using only one key.

### Finding Description
`ValidateMultiSign.execute` loops over caller-supplied signatures: [1](#0-0) 

For each signature it:
1. Recovers the signer address via `recoverAddrBySign(sign, hash)`, which parses `Rsv.fromSignature(sign)` and calls `SignUtils.fromComponents(r, s, v, ...)` then `signature.validateComponents()`: [2](#0-1) 

2. `ECDSASignature.validateComponents` only requires `1 ≤ r,s < SECP256K1N` and `v ∈ {27,28}` — it does **not** enforce canonical low-S, so both `s` and its malleable counterpart `N - s` (with flipped `v`) are accepted as valid, recovering to the *same* public key/address: [3](#0-2) 

3. The dedup check is: if `recoveredAddr` was already seen (`matrixContains(executedSignList, recoveredAddr)`), it only `continue`s (skips) when the *merged raw signature bytes* also already exist in the list (`matrixContains(executedSignList, sign)`). If the address matches but the raw signature bytes differ — which is trivially achievable via signature malleability — the code falls through, calls `MUtil.checkCPUTime()` (a CPU/time guard, not an anti-double-count guard), and then proceeds to add `weight` again and re-insert both the merged sign and the address into `executedSignList`.

This means the loop's true invariant is "no duplicate *signature bytes*", not the intended "no duplicate *signer*". Since ECDSA signatures for the same `(privkey, hash)` pair are trivially malleable into a second, distinct, validly-recovering byte encoding, an attacker holding one key can supply `[sig, malleableVariant(sig)]` and have `totalWeight` incremented by `w` twice instead of once.

### Impact Explanation
An attacker controlling a single key with `permission.weight = w` where `threshold ≤ 2w` (but `w < threshold`, so a lone signature is normally insufficient) can pass `validateMultiSignAddr` and any TVM contract logic gated behind it (e.g. custom multisig-based authorization contracts using this precompile at address `0x...a`) without a second real signer. This is a full authentication bypass of the multisig weight-threshold invariant for any contract relying on this precompile, using only a single unprivileged private key.

### Likelihood Explanation
- Preconditions are minimal and fully attacker-controlled: the attacker only needs one private key that already holds nonzero weight `w` under the target permission (`w < threshold ≤ 2w`), and calls the public precompile `validateMultiSignAddr` (`address 0x...0a`) reachable from any contract/transaction.
- Constructing the malleable signature requires only standard EC arithmetic (`s' = N - s`, flip `v` bit) — no computational hardness assumption is broken.
- The dedup bypass is deterministic and repeatable in every call matching this pattern; it does not depend on race conditions or timing.

### Recommendation
Change the dedup key in `ValidateMultiSign.execute` to be based solely on `recoveredAddr` (or canonicalize `s` to low-S / reject `s > N/2` before/at signature validation), so that once an address has contributed weight, any further signature recovering to the same address is skipped regardless of its raw byte encoding:
```java
if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
    continue; // any additional signature from the same signer must not add weight
}
```
Additionally, enforce canonical (low-S) signature form in `ECDSASignature.validateComponents` (reject `s > N/2`) to close the malleability vector at the recovery layer as defense in depth.

### Proof of Concept
```java
// framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignMalleabilityTest.java
@Test
public void testMalleableSignatureDoubleCountsWeight() {
    ECKey key = new ECKey();
    AccountCapsule toAccount = new AccountCapsule(ByteString.copyFrom(key.getAddress()),
        Protocol.AccountType.Normal, System.currentTimeMillis(), true,
        dbManager.getDynamicPropertiesStore());

    ECKey signerKey = new ECKey(); // weight 1, threshold 2 -> normally insufficient alone
    Protocol.Permission activePermission = Protocol.Permission.newBuilder()
        .setType(Protocol.Permission.PermissionType.Active)
        .setId(2).setPermissionName("active").setThreshold(2)
        .setOperations(ByteString.copyFrom(new byte[32]))
        .addKeys(Protocol.Key.newBuilder()
            .setAddress(ByteString.copyFrom(signerKey.getAddress())).setWeight(1).build())
        .build();
    toAccount.updatePermissions(toAccount.getPermissionById(0), null,
        Collections.singletonList(activePermission));
    dbManager.getAccountStore().put(key.getAddress(), toAccount);

    byte[] data = Sha256Hash.hash(true, longData);
    byte[] merged = ByteUtil.merge(key.getAddress(), ByteArray.fromInt(2), data);
    byte[] toSign = Sha256Hash.hash(true, merged);

    ECKey.ECDSASignature sig = signerKey.sign(toSign);
    byte[] sig1 = sig.toByteArray(); // canonical (r, s, v)

    // Malleable variant: s' = N - s, v' flipped between 27/28
    BigInteger sPrime = ECKey.CURVE.getN().subtract(sig.s);
    byte vPrime = (byte) (sig.v == 27 ? 28 : 27);
    ECKey.ECDSASignature malleable = new ECKey.ECDSASignature(sig.r, sPrime);
    malleable.v = vPrime;
    byte[] sig2 = malleable.toByteArray();

    List<Object> signs = new ArrayList<>();
    signs.add(Hex.toHexString(sig1));
    signs.add(Hex.toHexString(sig2));

    // Both signatures must recover to the SAME address (signerKey's), weight=1 each,
    // but threshold=2 requires two DISTINCT real signers.
    Pair<Boolean, byte[]> ret = validateMultiSign(
        StringUtil.encode58Check(key.getAddress()), 2, data, signs);

    // Expected (fixed) behavior: single real signer => weight=1 < threshold=2 => FALSE
    Assert.assertArrayEquals("single key must not satisfy threshold via malleable duplicate",
        DataWord.ZERO().getData(), ret.getValue());
    // If this assertion fails and ret == ONE, the vulnerability is confirmed:
    // totalWeight was computed as 2 (double-counted) instead of 1.
}
```
Note: exact byte layout/field order produced by `ECDSASignature.toByteArray()` vs. what `Rsv.fromSignature` expects should be confirmed against `crypto/src/main/java/org/tron/common/crypto/Rsv.java` and `ECKey.toByteArray()` (not fully inspected in this session) before running; the malleability transform (`s' = N - s`, `v` flip) itself is standard and independent of encoding.

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

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L923-940)
```java
    public static boolean validateComponents(BigInteger r, BigInteger s,
        byte v) {

      if (v != 27 && v != 28) {
        return false;
      }

      if (BIUtil.isLessThan(r, BigInteger.ONE)) {
        return false;
      }
      if (BIUtil.isLessThan(s, BigInteger.ONE)) {
        return false;
      }

      if (!BIUtil.isLessThan(r, SECP256K1N)) {
        return false;
      }
      return BIUtil.isLessThan(s, SECP256K1N);
```
