### Title
Multi-signature threshold can be bypassed via ECDSA signature malleability in `ValidateMultiSign` precompile - (File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java)

### Summary
The `ValidateMultiSign` precompiled contract accumulates signer weight into `totalWeight` keyed on `recoveredAddr`/`sign` pairs stored in `executedSignList`, but the de-duplication check keys on the *exact signature bytes* combined with the address rather than on the recovered address alone. This mirrors the reported bug class: using an insufficiently unique/collision-prone identifier as the "already counted" key, so an attacker can submit two syntactically different but cryptographically equivalent inputs (here: a malleable ECDSA signature `(r, s)` and `(r, n-s)` with flipped recovery id, both valid for the same key and message hash) and have both counted as distinct signers.

### Finding Description
In `ValidateMultiSign.execute`: [1](#0-0) 
for each signature: `recoveredAddr` is computed, then `sign` is redefined as `merge(recoveredAddr, sign)`. The duplicate check is:
```
if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
  if (ByteArray.matrixContains(executedSignList, sign)) {
    continue;
  }
  MUtil.checkCPUTime();
}
```
This only skips (`continue`) weight accumulation when the *exact* `merge(recoveredAddr, sign)` byte sequence was already seen. If the address was seen before but the raw signature bytes differ (e.g., ECDSA's inherent `(r,s)` / `(r, n-s)` malleability, which is not rejected anywhere in this path — `ECDSASignature.toCanonicalised()`/`validateComponents()` exist in the crypto layer but are not invoked here), execution falls through, recomputes `weight = TransactionCapsule.getWeight(permission, recoveredAddr)`, and adds it to `totalWeight` again. [2](#0-1) 
Both the report's bug and this one share the same root cause: a state-tracking map/list is keyed on data that does not uniquely and canonically identify the logical entity (source chain+sender vs. just payload in the report; recovered signer address vs. raw malleable signature bytes here), letting an attacker produce two "different" inputs that should be treated as identical for accounting purposes but are not.

### Impact Explanation
`ValidateMultiSign` is used by on-chain contracts to verify that a transaction/message meets an account's `Permission` threshold (multi-sig). If a single key's weight can be counted twice by resubmitting a malleable variant of its own valid signature, an attacker holding only one authorized key (with weight < threshold) can satisfy a 2x-weight threshold alone, defeating the multi-signature security guarantee that dependent contracts (custody, governance, bridges, DAOs on TVM) rely on. This is an accounting/permission-validation corruption reachable from any TVM contract call.

### Likelihood Explanation
Reachability is straightforward: any contract or externally-triggered call can invoke this precompile (address `0x66`) with attacker-controlled signature arrays, and ECDSA signature malleability (flipping `s -> n-s`, `v -> 27/28` swap) is a well-known, cheaply computable property requiring no private key compromise — only possession of one valid signature over the target hash. The main uncertainty is whether some other layer (permission threshold design, or callers always requiring `weight >= threshold` with `MAX_SIZE=5` cap) limits practical impact, and whether `MUtil.checkCPUTime()` (called only when the address already matched but sign didn't) has any side effect beyond CPU/DoS metering — from the code shown it does not prevent the weight addition.

### Recommendation
Track de-duplication solely by `recoveredAddr` (not by the raw signature bytes), i.e., once an address has contributed weight, any further signature recovering to the same address should be skipped regardless of its byte encoding. Additionally, reject non-canonical signatures (enforce `s <= HALF_CURVE_ORDER`, i.e., call `toCanonicalised()`/`validateComponents()`) before recovery so that malleable duplicates cannot be produced in the first place.

### Proof of Concept
1. Create account `A` with `Permission` containing key `K` with weight `w >= threshold/2` but `< threshold`.
2. Sign `hash` with `K` to get `(r, s, v)`.
3. Derive the malleable counterpart `(r, n-s, v')` (standard secp256k1 malleability transform), which recovers to the same address as `K`.
4. Call `ValidateMultiSign(address=A, permissionId, data=hash, signatures=[sig1, sig2_malleable])`.
5. Observe: first signature adds weight `w`; for the second, `matrixContains(executedSignList, recoveredAddr)` is true but `matrixContains(executedSignList, merge(recoveredAddr, sig2))` is false (different bytes), so it does not `continue`; weight `w` is added again, `totalWeight = 2w >= threshold`, and the call returns success (`dataOne()`) despite only one actual key having signed. [3](#0-2)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1036-1121)
```java
  public static class ValidateMultiSign extends PrecompiledContract {

    private static final int ENGERYPERSIGN = 1500;
    private static final int MAX_SIZE = 5;
    private static final int ABI_HEADER_WORDS = 5;
    private static final int ABI_ITEM_WORDS = 5;


    @Override
    public long getEnergyForData(byte[] data) {
      long cnt = (data.length / WORD_SIZE - 5) / 5;
      // one sign 1500, half of ecrecover
      return cnt * ENGERYPERSIGN;
    }

    @Override
    public Pair<Boolean, byte[]> execute(byte[] rawData) {
      if (VMConfig.allowTvmOsaka()
          && !isValidAbiEncoding(rawData, ABI_HEADER_WORDS, ABI_ITEM_WORDS)) {
        return Pair.of(false, EMPTY_BYTE_ARRAY);
      }
      DataWord[] words = DataWord.parseArray(rawData);
      byte[] address = words[0].toTronAddress();
      int permissionId = words[1].intValueSafe();
      byte[] data = words[2].getData();

      byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
      byte[] hash = Sha256Hash.hash(CommonParameter
          .getInstance().isECKeyCryptoEngine(), combine);

      if (VMConfig.allowTvmSelfdestructRestriction()) {
        int sigArraySize = words[words[3].intValueSafe() / WORD_SIZE].intValueSafe();
        if (sigArraySize > MAX_SIZE) {
          return Pair.of(true, DATA_FALSE);
        }
      }
      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[3].intValueSafe() / WORD_SIZE, rawData) :
          extractBytesArray(words, words[3].intValueSafe() / WORD_SIZE, rawData);

      if (signatures.length == 0 || signatures.length > MAX_SIZE) {
        return Pair.of(true, DATA_FALSE);
      }

      AccountCapsule account = this.getDeposit().getAccount(address);
      if (account != null) {
        try {
          Permission permission = account.getPermissionById(permissionId);
          if (permission != null) {
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

            if (totalWeight >= permission.getThreshold()) {
              return Pair.of(true, dataOne());
            }
          }
        } catch (Throwable t) {
          if (t instanceof OutOfTimeException) {
            throw t;
          }
          logger.info("ValidateMultiSign error:{}", t.getMessage());
        }
      }
      return Pair.of(true, DATA_FALSE);
    }
  }
```

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L923-963)
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
