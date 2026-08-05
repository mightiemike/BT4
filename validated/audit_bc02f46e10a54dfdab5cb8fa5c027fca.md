### Title
ECDSA signature malleability enables double-counting of a single signer's weight in `ValidateMultiSign` - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Finding Description
`ValidateMultiSign.execute` iterates over the caller-supplied signature array and, for each entry, recovers the signer address via `recoverAddrBySign`, which calls `SignUtils.signatureToAddress` → `ECKey.signatureToKeyBytes` → `ECKey.recoverPubBytesFromSignature`, a standard ECDSA public-key-recovery routine that accepts any `(r, s, v)` in valid range without enforcing the canonical low-`s` restriction (no `HALF_CURVE_ORDER`/low-s check exists anywhere in `ECKey.java`). [1](#0-0) [2](#0-1) 

The deduplication logic in the multisig weight-accumulation loop is keyed on **exact byte-equality of the merged `(recoveredAddr || rawSignatureBytes)` tuple**, not solely on the recovered address:

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
  ...
  totalWeight += weight;
  executedSignList.add(sign);
  executedSignList.add(recoveredAddr);
}
``` [3](#0-2) 

If `recoveredAddr` was already seen but the newly submitted raw signature bytes differ from the previously recorded ones (e.g., a malleable variant with `s' = n - s` and flipped `v` parity, which recovers to the same public key/address per standard secp256k1 ECDSA malleability), the exact-byte check fails, `continue` is skipped, and execution falls through to add `weight` again for the same address, incrementing `totalWeight` a second time for one real signer.

### Impact Explanation
An attacker who has captured (or been given, e.g. via a legitimate multisig co-signer flow) one valid signature for a given `(address, permissionId, data)` tuple can locally derive a second, syntactically distinct but semantically identical signature (same recovered address) using standard ECDSA malleability math, without any additional private-key access. Submitting both to `ValidateMultiSign` (reachable from any TVM contract call, hence from any unprivileged public transaction/contract) causes `totalWeight` to be incremented twice for a single distinct signer. If the signer's weight is at least half of `permission.getThreshold()`, this lets the attacker satisfy the multisig threshold with fewer genuinely-distinct authorized signers than intended, undermining the multisig-threshold invariant used to gate on-chain contract logic that depends on `ValidateMultiSign`'s boolean result.

### Likelihood Explanation
Preconditions: the attacker must possess one valid signature from a legitimate signer over the exact `(address, permissionId, hash)` triple (e.g., obtained as one of several co-signers, or via any process that exposes a valid signature for that payload). Given that, deriving the malleable counterpart is a pure, offline, deterministic BigInteger computation (`s' = n - s`, flip recovery parity), requiring no private key. The precompile is directly reachable by any account issuing a `TriggerSmartContract` that calls the `validatemultisign(address,uint256,bytes32,bytes[])` precompiled contract, making this fully attacker-reachable and repeatable.

### Recommendation
Change the deduplication key in `ValidateMultiSign.execute` (and the analogous logic anywhere else `executedSignList`/`recoveredAddr` bookkeeping is used for weight accumulation) to dedupe strictly on `recoveredAddr` alone — i.e., if the address has already contributed weight, always `continue` regardless of whether the raw signature bytes match. Additionally, enforce canonical low-`s` signatures in `ECKey.ECDSASignature`/`SM2Signature` validation (`validateComponents`) to reject non-canonical `s` values, closing the malleability vector at the signature-acceptance layer as defense in depth.

### Proof of Concept
```java
// Pseudocode/PoC sketch for ValidateMultiSignContractTest
@Test
public void testMalleableSignatureDoubleCountsWeight() {
  ECKey key1 = new ECKey();
  ECKey key2 = new ECKey();
  // Set up account permission with threshold requiring e.g. weight 2,
  // where key1 alone has weight 1 (insufficient alone).
  // ... (mirrors setup in testDifferentCase) ...

  byte[] toSign = /* computed hash as in existing test */;
  ECKey.ECDSASignature sig = key1.sign(toSign);

  // Derive malleable counterpart: s' = n - s, flip recovery id parity.
  BigInteger n = ECKey.CURVE.getN();
  BigInteger sPrime = n.subtract(sig.s);
  int newV = (sig.v % 2 == 0) ? sig.v + 1 : sig.v - 1; // flip parity bit
  ECKey.ECDSASignature malleable = new ECKey.ECDSASignature(sig.r, sPrime);
  malleable.v = (byte) newV;

  // Confirm both recover to same address.
  assertArrayEquals(key1.getAddress(),
      ECKey.recoverAddressFromSignature(/*recId derived from v*/, sig, toSign));
  assertArrayEquals(key1.getAddress(),
      ECKey.recoverAddressFromSignature(/*recId derived from newV*/, malleable, toSign));

  List<Object> signs = new ArrayList<>();
  signs.add(Hex.toHexString(sig.toByteArray()));
  signs.add(Hex.toHexString(malleable.toByteArray())); // distinct bytes, same signer

  // Expect: DATA_FALSE because only 1 distinct signer contributed weight 1 < threshold 2.
  // Actual (bug): DATA_ONE because totalWeight double-counted to 2 >= threshold 2.
  Assert.assertArrayEquals(
      validateMultiSign(address, permissionId, data, signs).getValue(),
      DataWord.ZERO().getData()); // fails under current implementation
}
```
This test should assert `totalWeight` only increases once per unique recovered address; if it currently returns `DataWord.ONE()` (threshold met) despite only one genuinely distinct signer, the vulnerability is confirmed.

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

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L517-586)
```java
  @Nullable
  public static byte[] recoverPubBytesFromSignature(int recId,
      ECDSASignature sig, byte[] messageHash) {
    check(recId >= 0, "recId must be positive");
    check(sig.r.signum() >= 0, "r must be positive");
    check(sig.s.signum() >= 0, "s must be positive");
    check(messageHash != null, "messageHash must not be null");
    // 1.0 For j from 0 to h   (h == recId here and the loop is outside
    // this function)
    //   1.1 Let x = r + jn
    BigInteger n = CURVE.getN();  // Curve order.
    BigInteger i = BigInteger.valueOf((long) recId / 2);
    BigInteger x = sig.r.add(i.multiply(n));
    //   1.2. Convert the integer x to an octet string X of length mlen
    // using the conversion routine
    //        specified in Section 2.3.7, where mlen = ⌈(log2 p)/8⌉ or
    // mlen = ⌈m/8⌉.
    //   1.3. Convert the octet string (16 set binary digits)||X to an
    // elliptic curve point R using the
    //        conversion routine specified in Section 2.3.4. If this
    // conversion routine outputs “invalid”, then
    //        do another iteration of Step 1.
    //
    // More concisely, what these points mean is to use X as a compressed
    // public key.
    ECCurve.Fp curve = (ECCurve.Fp) CURVE.getCurve();
    BigInteger prime = curve.getQ();  // Bouncy Castle is not consistent
    // about the letter it uses for the prime.
    if (x.compareTo(prime) >= 0) {
      // Cannot have point co-ordinates larger than this as everything
      // takes place modulo Q.
      return null;
    }
    // Compressed allKeys require you to know an extra bit of data about the
    // y-coord as there are two possibilities.
    // So it's encoded in the recId.
    ECPoint R = decompressKey(x, (recId & 1) == 1);
    //   1.4. If nR != point at infinity, then do another iteration of
    // Step 1 (callers responsibility).
    if (!R.multiply(n).isInfinity()) {
      return null;
    }
    //   1.5. Compute e from M using Steps 2 and 3 of ECDSA signature
    // verification.
    BigInteger e = new BigInteger(1, messageHash);
    //   1.6. For k from 1 to 2 do the following.   (loop is outside this
    // function via iterating recId)
    //   1.6.1. Compute a candidate public key as:
    //               Q = mi(r) * (sR - eG)
    //
    // Where mi(x) is the modular multiplicative inverse. We transform
    // this into the following:
    //               Q = (mi(r) * s ** R) + (mi(r) * -e ** G)
    // Where -e is the modular additive inverse of e, that is z such that
    // z + e = 0 (mod n). In the above equation
    // ** is point multiplication and + is point addition (the EC group
    // operator).
    //
    // We can find the additive inverse by subtracting e from zero then
    // taking the mod. For example the additive
    // inverse of 3 modulo 11 is 8 because 3 + 8 mod 11 = 0, and -3 mod
    // 11 = 8.
    BigInteger eInv = BigInteger.ZERO.subtract(e).mod(n);
    BigInteger rInv = sig.r.modInverse(n);
    BigInteger srInv = rInv.multiply(sig.s).mod(n);
    BigInteger eInvrInv = rInv.multiply(eInv).mod(n);
    ECPoint.Fp q = (ECPoint.Fp) ECAlgorithms.sumOfTwoMultiplies(CURVE
        .getG(), eInvrInv, R, srInv);
    return q.getEncoded(/* compressed */ false);
  }
```
