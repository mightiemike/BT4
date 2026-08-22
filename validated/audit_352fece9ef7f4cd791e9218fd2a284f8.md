Confirmed: `MUtil.checkCPUTime()` throws `OutOfTimeException` once the chain has passed fork `VERSION_4_7_1`, which aborts the whole precompile call whenever the same recovered address appears twice with a non-identical signature encoding. Since this fork check has apparently long since passed on mainnet, the double-counting path is effectively closed on current networks, but the vulnerable code path (pre-fork behavior, and the underlying design flaw) is still present in the source and worth flagging.

### Title
Signature malleability in `ECDSASignature` allows duplicate-weight counting in `ValidateMultiSign` precompile - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The `ValidateMultiSign` precompiled contract (address `0x0a`) recovers a signer address for each supplied signature and accumulates `weight` toward a permission `threshold`. Deduplication of "already-counted" signers is done by comparing raw signature bytes (`ByteArray.matrixContains(executedSignList, sign)`), not solely by recovered address, and `ECDSASignature.validateComponents()` does not enforce canonical (low-s) signatures. Because ECDSA signatures are malleable — `(r, s, v)` and `(r, N-s, v^1)` both verify to the same signer/hash — and because a signer can also trivially produce additional distinct valid `(r,s)` pairs for the same message with the same key (randomized nonce), the same private key's weight can be counted more than once in a single `validatemultisign` call, up to `MAX_SIZE = 5` signatures.

### Finding Description
`ValidateMultiSign.execute()` loops over the supplied signatures: [1](#0-0) 

For each signature it recovers `recoveredAddr` via `recoverAddrBySign`, which uses `SignUtils.fromComponents` / `signature.validateComponents()`: [2](#0-1) 

`validateComponents()` only checks that `r` and `s` are within `[1, SECP256K1N)` and `v ∈ {27,28}` — it never rejects the "upper-half" `s` (i.e., it does not canonicalize/enforce low-s), so both `s` and `N-s` variants of a signature are accepted as valid: [3](#0-2) 

Because signatures are deduplicated by exact byte match (`merge(recoveredAddr, sign)` compared via `matrixContains`) rather than by recovered address alone, an attacker holding a single private key that satisfies part of a multisig `Permission` can submit that key's weight multiple times by supplying several *different* valid signatures over the same hash (either the classic malleable `(r, N-s)` transform, or simply re-signing with a fresh random nonce `k`, which ECDSA allows). Each such submission passes `getWeight(permission, recoveredAddr) != 0` and is added to `totalWeight`, inflating the weight total past what the actual set of distinct keys should allow — up to `MAX_SIZE = 5` times.

The only guard against this is `MUtil.checkCPUTime()`, which throws `OutOfTimeException` (aborting the whole call) once fork `VERSION_4_7_1` has activated: [4](#0-3) [5](#0-4) 

Notably, `MUtil.checkCPUTime()` throws rather than skipping/continuing — meaning post-fork, any duplicate-address-different-signature submission simply aborts the call (`DATA_FALSE`/exception), rather than being cleanly rejected while still processing other signatures. This is a hard-fail mitigation rather than a proper per-signature dedup fix.

A structurally similar (already partially fixed) pattern exists in `TransactionCapsule.checkWeight`, used for on-chain multisig transactions, which used to dedupe by raw signature `base64` string and was corrected to dedupe by recovered `address` after fork `VERSION_4_7_1`: [6](#0-5) 

This confirms the java-tron team is aware of the "dedupe-by-signature-bytes-not-by-address" bug class, but the `ValidateMultiSign` TVM precompile still relies on a coarse `OutOfTimeException` fork guard rather than an actual per-address dedup fix.

### Impact Explanation
`ValidateMultiSign` (`0x0a`) is a general-purpose precompile any smart contract can call to check whether a set of signatures satisfies an account's `Permission` threshold — this is the on-chain analog of "signature-uniqueness-based authorization" that the original report warns about. If a contract (deployed by any user, reachable via any TVM contract call) relies on `validatemultisign` to gate privileged operations (e.g., a custodial/multisig wallet contract), an attacker controlling only one of the required keys could, on chains/segments where the fork guard is not yet active or is bypassed, supply multiple distinct signatures from that single key to inflate `totalWeight` and satisfy the multisig `threshold` without holding the other required keys — enabling unauthorized account/contract operations.

### Likelihood Explanation
On current mainnet, `VERSION_4_7_1` has almost certainly already activated, meaning `MUtil.checkCPUTime()` throws and aborts the call whenever a duplicate address with a differing signature is seen, closing off the double-counting path (though at the cost of a hard failure rather than a clean rejection). The likelihood of exploitation on live mainnet is therefore low. However, the underlying design flaw — deduplication by raw signature bytes rather than recovered address, combined with `validateComponents()` not enforcing canonical low-s signatures — remains in the code and would be exploitable on any deployment/fork configuration where `VERSION_4_7_1` is not active (e.g., private/test chains, or if fork activation logic is itself misconfigured).

### Recommendation
- In `ValidateMultiSign.execute()`, deduplicate strictly by `recoveredAddr` (skip/reject immediately on any repeat, regardless of signature-byte equality) instead of relying on the `checkCPUTime()` fork-gated exception.
- Enforce canonical (low-s) signatures in `ECDSASignature.validateComponents()` (reject `s > HALF_CURVE_ORDER`) to close the classical malleability vector at the source, consistent with the OpenZeppelin ECDSA fix referenced in the report.
- Apply the same address-based (not byte-based) dedup fix uniformly, removing the fork-gated `MUtil.checkCPUTime()` special case.

### Proof of Concept
1. Create a `Permission` with `threshold = 2` and two keys, `key1` (weight 1) and `key2` (weight 1).
2. Using only `key1`'s private key, produce two distinct valid signatures over the same `hash` (e.g., a normal signature `(r, s, v)` and its malleable counterpart `(r, N-s, v^1)`, or simply sign twice with a fresh random nonce).
3. Call the `ValidateMultiSign` precompile (`0x0a`) with `signatures = [sig1, sig2]` for `key1` only.
4. Pre-fork (`VERSION_4_7_1` not active), `executedSignList` dedup by exact signature bytes lets both signatures pass `getWeight` and add `weight=1` twice, so `totalWeight=2 >= threshold=2`, returning success (`dataOne()`), even though only one distinct key participated.
5. Post-fork, the same input instead throws `OutOfTimeException` via `MUtil.checkCPUTime()`, aborting the call — confirming the fork guard is the only barrier, not a proper fix. [1](#0-0)

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

**File:** actuator/src/main/java/org/tron/core/vm/utils/MUtil.java (L62-66)
```java
  public static void checkCPUTime() {
    if (ForkController.instance().pass(Parameter.ForkBlockVersionEnum.VERSION_4_7_1)) {
      throw new OutOfTimeException("CPU timeout for 0x0a executing");
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L243-263)
```java
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
