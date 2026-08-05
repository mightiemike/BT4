### Title
Signature malleability in `ValidateMultiSign` precompile allows multisig weight double-counting - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The `ValidateMultiSign` TVM precompiled contract validates a set of signatures against an account's `Permission` to check whether a weighted signature threshold is met. Because the underlying ECDSA validation only restricts `v` to `27/28` and `s` to be less than the curve order `N` (not `N/2`), a single private key can produce two distinct, valid signatures over the same message hash — the classic ECDSA malleability pair `(r, s, v)` and `(r, N-s, v')`. The precompile's de-duplication logic keys on the raw signature bytes rather than on the recovered signer address, so the two malleable signature encodings from the same key are both counted toward `totalWeight`, allowing an attacker holding a single signing key to satisfy a multisig threshold that should require multiple independent signers.

### Finding Description
`ECDSASignature.validateComponents` only enforces `v == 27 || v == 28` and `0 < r, s < SECP256K1N`; it does not enforce the canonical/low-`s` form (`s <= N/2`): [1](#0-0) 

This means for any valid signature `(r, s, v)` on a message hash, the malleable counterpart `(r, N-s, v')` also passes `validateComponents()` and recovers to the **same** signer address, since `toCanonicalised()` (which would normalize `s`) is not applied on the verification path: [2](#0-1) 

`ValidateMultiSign.execute` uses `recoverAddrBySign` to recover an address per supplied signature, and de-duplicates using the *raw signature bytes merged with the address*, not the address alone: [3](#0-2) 

The dedup check is: if `recoveredAddr` was already seen but the exact `sign` bytes were not, the code merely calls `MUtil.checkCPUTime()` and then proceeds to add `weight` again for that same address — it never `continue`s in that branch. Only an exact byte-for-byte repeat of the previous signature is skipped. `recoverAddrBySign` itself relies on the same permissive `validateComponents()`: [4](#0-3) 

This mirrors the reported bug class in `ERC865Token`: a signature (rather than a canonical identifier like signer address or a payload hash) is used to key uniqueness, and malleability lets an attacker generate multiple distinct signature encodings for the same underlying authorization, defeating the intended uniqueness/threshold check.

### Impact Explanation
`ValidateMultiSign` is a TVM precompile at a fixed address, reachable by any smart contract call once `VMConfig.allowTvmSolidity059()` is active — this includes calls made on behalf of unprivileged end users interacting with any contract that uses on-chain multisig verification (e.g. custom wallets, DAOs, exchange withdrawal approvals implemented in Solidity via this precompile): [5](#0-4) 

An attacker controlling one key that is a partial signer on an account's `Permission` (with `weight < threshold`) can submit that key's signature twice in malleable form within the `signatures` array passed to `ValidateMultiSign`. Because the second malleable copy is not byte-identical to the first, it bypasses the `continue` short-circuit and its weight is added again, so `totalWeight` can reach `permission.getThreshold()` using only one real signer instead of the number of independent signers the permission model intends. This is a concrete authorization-bypass impact: a smart-contract-enforced multisig gate can be satisfied without collecting the required number of independent signatures.

### Likelihood Explanation
The attack requires only: (1) possession of one valid private key participating in a target `Permission` with non-zero weight, and (2) the ability to derive the trivial malleable transform `s' = N - s` (and corresponding `v'`), which is public-key arithmetic requiring no special access. Since `ValidateMultiSign` is a public precompile invocable from any contract/user transaction with attacker-supplied `data` (the signature array), this is directly reachable by an unprivileged actor with no elevated permissions — likelihood is high wherever contracts rely on `ValidateMultiSign` for threshold-based authorization.

### Recommendation
Enforce canonical signature form before/at validation in `ECDSASignature.validateComponents` (reject `s > N/2`, i.e., require `toCanonicalised()` equivalence), consistent with EIP-2/OpenZeppelin ECDSA practices. Additionally, fix the `ValidateMultiSign` de-duplication logic in `PrecompiledContracts.java` to key strictly on `recoveredAddr` (skip/continue whenever the address has already contributed weight), rather than on the raw signature bytes, so a given signer's weight can only be counted once regardless of how many differently-encoded valid signatures they produce.

### Proof of Concept
1. An account has `Permission` with signers `A` (weight 1) and `B` (weight 2), `threshold = 3`.
2. Attacker controls only key `A`. Attacker computes `sig1 = sign(hash)` with key `A`, obtaining `(r, s, v)`.
3. Attacker computes the malleable counterpart `sig2 = (r, N-s, v_flipped)`; `sig2` still recovers to address `A` and passes `ECDSASignature.validateComponents` since only `v ∈ {27,28}` and `s < N` are checked.
4. Attacker calls the `ValidateMultiSign` precompile (`0x...0a`) with `signatures = [sig1, sig2]`.
5. In `execute`, first iteration adds weight 1 for `A`; second iteration finds `recoveredAddr` (`A`) already in `executedSignList` but `sign` bytes differ, so it does not `continue`, and instead calls `TransactionCapsule.getWeight` again, adding another weight 1 — `totalWeight = 2` from a single real key.
6. Repeating with more malleable variants (not literally possible beyond the 2 forms per signature, but combined with any other genuinely-owned key) can push `totalWeight` over `threshold` without collecting the required independent signers, returning `dataOne()` (success) to the calling contract logic. [6](#0-5)

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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L254-259)
```java
    if (VMConfig.allowTvmSolidity059() && address.equals(batchValidateSignAddr)) {
      return batchValidateSign;
    }
    if (VMConfig.allowTvmSolidity059() && address.equals(validateMultiSignAddr)) {
      return validateMultiSign;
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

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1086-1109)
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

            if (totalWeight >= permission.getThreshold()) {
              return Pair.of(true, dataOne());
```
