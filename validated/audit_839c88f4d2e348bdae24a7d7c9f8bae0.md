Based on my investigation, the ECDSA malleability property does exist but the concrete exploit path in `TransactionCapsule` (transaction-level dedup) is not affected, since `validateDup` keys on `transactionId`/`Sha256Hash` of raw data, not on raw signature bytes. However, I found a concrete exploitable pattern inside `PrecompiledContracts.ValidateMultiSign`.

### Title
Signature-malleability double-counts multisig weight in `ValidateMultiSign` precompile - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
`ECKey.ECDSASignature.validateComponents()` only checks `1 <= s < N` and never rejects the "upper-half" `s` values (`s > N/2`), so `(r, s)` and `(r, N-s)` are both accepted as valid, non-canonical signatures that recover to the same address via `SignUtils.signatureToAddress`. The `ValidateMultiSign` precompile (`PrecompiledContracts.java:1036-1121`) uses raw `sign` bytes (merged with the recovered address) as its "already-executed" de-duplication key instead of de-duplicating strictly by recovered address, allowing a single private key's weight to be counted twice toward the multisig threshold.

### Finding Description
`ECDSASignature.validateComponents()` at [1](#0-0)  only bounds `r` and `s` to `[1, N)` and never enforces the low-`s` canonical form (`HALF_CURVE_ORDER` check exists only in `toCanonicalised()`, not in `validateComponents()`). Consequently, for any valid signature `(r, s, v)`, the malleable counterpart `(r, N-s, v')` also passes `validateComponents()` and recovers to the identical address, as used by `recoverAddrBySign` at [2](#0-1) .

`ValidateMultiSign.execute()` relies on this for its "signature already used" check: [3](#0-2) 

Here, `sign` (the raw r/s/v bytes merged with the recovered address) — not just the recovered address alone — is used as the executed-list de-dup key. If the address was already seen but the *byte-exact signature* differs (as with a malleable `(r, N-s)` variant), the loop does **not** `continue`; it falls through, calls `MUtil.checkCPUTime()` (a CPU-budget check, not a rejection), and then adds `TransactionCapsule.getWeight(permission, recoveredAddr)` again into `totalWeight`. This lets an attacker who controls a single private key holding weight `w` in a permission submit two ECDSA-malleable variants of the same signature and have the precompile count `2w` toward `permission.getThreshold()`, instead of enforcing the number of *distinct signers* required by the permission scheme.

### Impact Explanation
This is an unauthorized-state-change / permission-bypass vector: TVM contracts (e.g., custom multisig wallets, DAO-style contracts) that call the `ValidateMultiSign` precompile to gate privileged actions (fund transfers, admin operations) can have their required-signer-count security assumption violated. A single signer (attacker) can satisfy a threshold that was intended to require multiple independent key holders, by submitting `sig` and its malleable twin `sig'` for the same key. This does not affect on-chain account permission enforcement in `TransactionCapsule.checkWeight` (which de-dupes by recovered address/base64, not raw sig bytes, and throws on repeats), but it does affect any Solidity/TVM contract logic relying on `validateMultiSign` semantics for its own authorization checks. Impact class: unauthorized account/contract operation via permission threshold bypass.

### Likelihood Explanation
Feasibility is high for the attacker with a single key already holding the minimum weight needed to exploit (e.g., weight 1 out of threshold 2, with another honest signer weight 1, or weight `w` counted twice to meet threshold `2w`). Cost is only standard TVM call energy (`1500` energy per signature, `ENGERYPERSIGN`), no special privilege required. It is fully reproducible offline: generate `(r,s)`, compute `n-s`, adjust recovery `v`, both validate and recover to the same address.

### Recommendation
In `ValidateMultiSign.execute()` (and `BatchValidateSign` if the same pattern is used elsewhere), de-duplicate strictly by `recoveredAddr` (skip immediately once an address has contributed weight), not by the raw signature bytes concatenated with the address. Alternatively/additionally, enforce canonical low-`s` signatures in `ECDSASignature.validateComponents()` (reject `s > HALF_CURVE_ORDER`), consistent with common EIP-2-style hardening, to eliminate malleable duplicate encodings network-wide.

### Proof of Concept
```java
// crypto module - malleability produces two distinct signatures for same key/message
ECKey key = new ECKey();
byte[] hash = Sha256Hash.hash(true, "test-message".getBytes());
ECKey.ECDSASignature sig = key.sign(hash); // (r, s, v)

BigInteger n = ECKey.CURVE.getN();
BigInteger sPrime = n.subtract(sig.s);
byte vPrime = (byte) (sig.v == 27 ? 28 : 27); // flip parity to match N - s

ECKey.ECDSASignature malleable =
    ECKey.ECDSASignature.fromComponents(
        ByteUtil.bigIntegerToBytes(sig.r, 32),
        ByteUtil.bigIntegerToBytes(sPrime, 32),
        vPrime);

Assert.assertTrue(sig.validateComponents());
Assert.assertTrue(malleable.validateComponents());
Assert.assertArrayEquals(
    ECKey.signatureToAddress(hash, sig),
    ECKey.signatureToAddress(hash, malleable));

// Then, in ValidateMultiSignContractTest-style harness:
// signs = [key1.sign(toSign), malleableVariantOf(key1.sign(toSign))]
// with permission requiring threshold=2 and only key1 registered with weight 2 needed via two "distinct" signers,
// but only key1's key material used twice -> validateMultiSign returns DataWord.ONE()
// even though only a single private key participated.
```

Note: I was unable to fully confirm whether `BatchValidateSign` or other actuators (outside `PrecompiledContracts.java`) implement equivalent signature-byte-keyed de-dup logic reachable by unprivileged users; the search only surfaced `ValidateMultiSign` with this specific vulnerable pattern. The core protocol-level transaction dedup (`Manager.validateDup`/`TransactionCapsule.checkWeight`) is not affected because it hashes/keys by recovered address and transaction ID, not raw signature bytes.

### Citations

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
