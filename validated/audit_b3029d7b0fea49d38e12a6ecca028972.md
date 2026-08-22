### Title
Signature malleability bypasses de-duplication in TVM `ValidateMultiSign` precompile, allowing weight over-counting with a single key - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
The `ValidateMultiSign` precompiled contract (TVM opcode `validatemultisign`, address `0x...0a`) accepts an array of signature bytes and sums each recovered signer's permission weight, exactly like the Tracer `Liquidation.claimReceipt(orders)` pattern of summing values from an attacker-supplied array. Its de-duplication check compares the *exact byte encoding* of `merge(recoveredAddr, sign)` rather than the recovered address alone, so two ECDSA-malleable encodings of the same signature (same signer, different `(r, s, v)` bytes) are treated as distinct entries and both contribute weight, letting a single private key be counted multiple times toward the multisig threshold.

### Finding Description
`ValidateMultiSign.execute` iterates over the caller-supplied `signatures` array and accumulates `totalWeight`: [1](#0-0) 

For each signature it recovers `recoveredAddr`, and only skips adding weight (`continue`) if the *exact merged byte sequence* `merge(recoveredAddr, sign)` was already seen; if the address was seen before but with a different signature encoding, it does **not** skip — it just calls `MUtil.checkCPUTime()` and falls through to add the weight again: [2](#0-1) 

Signature validity checking (`recoverAddrBySign` → `ECDSASignature.validateComponents`) does not enforce the canonical low-S form, so ECDSA's well-known malleability — `(r, s, v)` and `(r, n-s, v')` both being valid signatures that recover to the same address — is not rejected: [3](#0-2) [4](#0-3) 

The canonicalization helper `toCanonicalised()` exists in `ECKey` but is never invoked before comparison in `ValidateMultiSign`: [5](#0-4) 

As a result, an attacker who controls a single key with weight `w` in a permission (`Permission.threshold > w`) can derive a second, byte-distinct but equally valid signature over the same message (flipping `s` to `n-s` and the recovery id) and submit both signatures in the `signatures` array to `validatemultisign`. Both entries recover to the same address but are not deduplicated, so `totalWeight` becomes `2w` (or more, with more malleated variants, bounded only by `MAX_SIZE = 5`), potentially reaching the permission threshold with only one real signer — exactly analogous to the Tracer bug where duplicate array entries let a single "bad trade" be counted repeatedly for reimbursement instead of once.

The sibling precompile `BatchValidateSign` is not directly affected the same way because it checks each `(signature, address)` pair independently rather than summing a threshold-based weight, but `ValidateMultiSign`'s threshold-accumulation logic is the vulnerable pattern.

### Impact Explanation
Any smart contract that relies on the `validatemultisign` precompile (accessible via the `validatemultisign(address,uint256,bytes32,bytes[])` TVM builtin) to gate privileged actions — e.g., custody/escrow/multisig wallet contracts checking that a threshold of independent keys approved an operation — can be bypassed by an attacker who controls only a single key whose weight is below the threshold. This is a genuine authorization bypass reachable from any broadcast transaction that triggers a contract calling this precompile, and could lead to unauthorized asset transfers or state changes gated by on-chain multisig checks.

### Likelihood Explanation
Exploitation requires only: (1) a contract using `validatemultisign` for an account/permission where a single controlled key's weight is less than the threshold but multiplied duplicates would exceed it, and (2) computing one signature malleation (`s' = n - s`, flip recovery parity), which is a standard, well-known, deterministic operation requiring no additional secrets. No privileged access, leaked keys, or malicious peers are needed — only a normal transaction from an anonymous account calling a vulnerable contract. Likelihood is limited by how many deployed contracts actually rely on `validatemultisign` for weighted-threshold logic with single-holder keys just under threshold, but the primitive itself is directly and trivially reachable.

### Recommendation
In `ValidateMultiSign.execute` (and equally in `TransactionCapsule.checkWeight`/`addSign` if the same pattern exists there), deduplicate strictly by **recovered address**, not by the raw/merged signature bytes: once an address has contributed weight, any further signature recovering to that same address must be skipped entirely (not merely rate-limited via `checkCPUTime`). Additionally, enforce canonical (low-S) signature form before accepting a signature (call `toCanonicalised()`/reject non-canonical `s`), which independently eliminates the two-encodings-per-key malleability at the input validation layer.

### Proof of Concept
1. Deploy a contract that requires two independent signers via an `Active` permission with `threshold = 2`, where key `K1` has `weight = 1` and belongs to the attacker, and the attacker does not hold any other key.
2. Attacker signs the required hash with `K1`, producing `(r, s, v)`.
3. Attacker computes the malleated signature `(r, n - s, v')` (flip `v`'s parity bit accordingly) — both signatures recover to the same address for the same message hash, per `ECKey.recoverPubBytesFromSignature`/`validateComponents` which does not reject high-S values: [6](#0-5) 
4. Attacker calls `validatemultisign(address, permissionId, hash, [sign1, sign2])`. In the loop, iteration 1 adds weight `1` for `K1`'s address; iteration 2 recovers the same address, but since `merge(addr, sign2) != merge(addr, sign1)`, the exact-match check fails and weight `1` is added again, making `totalWeight = 2 >= threshold(2)`.
5. The precompile returns `true` (`dataOne()`), incorrectly signaling that the multisig threshold was satisfied by a single controlled key.

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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1085-1107)
```java
            //calculate weight
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

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L923-946)
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


    public boolean validateComponents() {
      return validateComponents(r, s, v);
    }
```

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L948-963)
```java
    public ECDSASignature toCanonicalised() {
      if (s.compareTo(HALF_CURVE_ORDER) > 0) {
        // The order of the curve is the number of valid points that
        // exist on that curve. If S is in the upper
        // half of the number of valid points, then bring it back to
        // the lower half. Otherwise, imagine that
        //    N = 10
        //    s = 8, so (-8 % 10 == 2) thus both (r, 8) and (r, 2)
        // are valid solutions.
        //    10 - 8 == 2, giving us always the latter solution,
        // which is canonical.
        return new ECDSASignature(r, CURVE.getN().subtract(s));
      } else {
        return this;
      }
    }
```
