## Finding

The premise in the question—that a malleable ECDSA twin `(r, n-s, flipped v)` recovers to a **different** address than the original signature—is cryptographically incorrect. By construction, `(r, s, v)` and `(r, n-s, 1-v_parity)` recover to the exact **same** public key/address; malleability produces a second valid *encoding* of the same signature from the same key, not a different signer's identity. So the "impersonate another signer via ecrecover" scenario as described is not exploitable: `ECRecover.execute` at [1](#0-0)  combined with `ECDSASignature.validateComponents` at [2](#0-1)  indeed accepts non-canonical high-`s` signatures (no low-s enforcement, only `v ∈ {27,28}` and `r,s ∈ [1, N)` are checked), but doing so cannot make ecrecover output an address belonging to a different, unauthorized account.

However, this same lack of canonical/low-s enforcement does enable a real, distinct authorization-bypass in `ValidateMultiSign.execute`, which trusts raw `recoverAddrBySign` output without deduplicating by recovered address:

```
actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java:1088-1106
for (byte[] sign : signatures) {
  byte[] recoveredAddr = recoverAddrBySign(sign, hash);
  sign = merge(recoveredAddr, sign);
  if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
    if (ByteArray.matrixContains(executedSignList, sign)) {
      continue;                 // only skips exact duplicate (addr+sign) bytes
    }
    MUtil.checkCPUTime();        // otherwise falls through and re-counts weight
  }
  long weight = TransactionCapsule.getWeight(permission, recoveredAddr);
  ...
  totalWeight += weight;         // same address's weight added again
  executedSignList.add(sign);
  executedSignList.add(recoveredAddr);
}
```

Because dedup is keyed on the raw signature bytes (`addr||sign`) rather than on `recoveredAddr` alone, a single key-holder can submit two byte-distinct signatures over the same `hash` (e.g., the canonical `(r,s,v)` and its malleable twin `(r, n-s, v')`, both valid because `validateComponents` doesn't reject high-`s`) and have their permission weight counted **twice** toward `permission.getThreshold()`.

### Title
Multisig threshold bypass via malleable-signature weight double-counting - (`actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
`ValidateMultiSign.execute` deduplicates submitted signatures by the raw `(recoveredAddr, sign)` byte pair instead of by recovered address alone, and `ECRecover`/`ECDSASignature.validateComponents` accept non-canonical (high-`s`) ECDSA signatures. A single signer can submit two distinct valid byte-encodings of a signature over the same message (canonical + malleable twin) and have their weight counted twice, allowing an attacker who controls only one of N required signing keys to satisfy a higher multisig threshold without the cooperation of other permission holders.

### Finding Description
`recoverAddrBySign` at [3](#0-2)  calls `signature.validateComponents()`, which (via `ECDSASignature.validateComponents`) only checks `v ∈ {27,28}` and `r, s ∈ [1, SECP256K1N)` [2](#0-1) ; there is no rejection of non-canonical (`s > N/2`) signatures. Any holder of one permission key can therefore produce two distinct byte-level signatures for the same `hash` that both recover to their own address.

In `ValidateMultiSign.execute`, the loop over submitted signatures dedupes using `executedSignList` keyed by the full `merge(recoveredAddr, sign)` byte blob, and only `continue`s (skips weight accrual) when that exact blob repeats [4](#0-3) . If the attacker instead resubmits a *different* signature encoding for an address already counted, the `matrixContains(executedSignList, sign)` check fails, execution falls through `MUtil.checkCPUTime()`, and `totalWeight += weight` is executed again for the same signer — silently doubling their contribution to `permission.getThreshold()`.

### Impact Explanation
This is an authorization-bypass in the on-chain multisig verification precompile (`ValidateMultiSign`, address `0x0a`), used by contracts and account-permission logic that call it to validate weighted-threshold signatures. An attacker controlling a single signing key with sufficient (but individually below-threshold) weight can synthesize a second valid signature encoding for the same message and pass the multisig check without obtaining any additional legitimate signer's approval, bypassing the intended M-of-N (weighted) authorization invariant.

### Likelihood Explanation
Fully exploitable by an unprivileged attacker holding one permission key with nonzero weight: producing the malleable twin of an ECDSA signature is a standard, cheap, deterministic operation (`s' = n - s`, flip parity bit of `v`), requiring no privileged access, and does not need the target contract to have custom flaws — the bug is inside the precompile's own accounting loop.

### Recommendation
In `ValidateMultiSign.execute` (and similarly `BatchValidateSign` if applicable), deduplicate strictly by `recoveredAddr` — once an address has contributed weight, skip it entirely regardless of the raw signature bytes, rather than allowing fallthrough on non-identical signature encodings. Additionally, enforce canonical low-`s` signatures in `ECDSASignature.validateComponents` (reject `s > HALF_CURVE_ORDER`) to eliminate the source of duplicate encodings for the same key/message.

### Proof of Concept
```java
// Pseudocode outline for a Java unit test in PrecompiledContractsTest
@Test
public void testValidateMultiSignWeightDoubleCounting() {
  // 1. Create an account with a Permission of threshold T,
  //    containing signer K with weight W < T but 2*W >= T.
  // 2. Sign `hash` with K to get ECDSASignature sig (r, s, v).
  ECKey key = new ECKey();
  ECDSASignature sig = key.sign(hash);

  // 3. Compute malleable twin: s2 = CURVE.getN().subtract(sig.s); v2 = flip(sig.v)
  ECDSASignature sigMalleable = new ECDSASignature(sig.r, sig.s2); sigMalleable.v = v2;

  // 4. Build ValidateMultiSign calldata containing [sig, sigMalleable] as the signature array
  //    for the account/permissionId, with `data` matching `hash`.

  // 5. Execute ValidateMultiSign.execute(rawData) and assert:
  //    - both recovered addresses equal key.getAddress()
  //    - totalWeight computed == 2*W >= T
  //    - result == dataOne() (i.e., Pair.of(true, dataOne()))
  //    even though only ONE distinct signer key actually authorized the action,
  //    demonstrating threshold bypass with a single key.
}
```

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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L598-621)
```java
    @Override
    public Pair<Boolean, byte[]> execute(byte[] data) {

      byte[] h = new byte[32];
      byte[] v = new byte[32];
      byte[] r = new byte[32];
      byte[] s = new byte[32];

      DataWord out = null;

      try {
        System.arraycopy(data, 0, h, 0, 32);
        System.arraycopy(data, 32, v, 0, 32);
        System.arraycopy(data, 64, r, 0, 32);

        int sLength = data.length < 128 ? data.length - 96 : 32;
        System.arraycopy(data, 96, s, 0, sLength);

        SignatureInterface signature = SignUtils.fromComponents(r, s, v[31]
            , CommonParameter.getInstance().isECKeyCryptoEngine());
        if (validateV(v) && signature.validateComponents()) {
          out = new DataWord(SignUtils.signatureToAddress(h, signature
              , CommonParameter.getInstance().isECKeyCryptoEngine()));
        }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1088-1106)
```java
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

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L923-941)
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
    }
```
