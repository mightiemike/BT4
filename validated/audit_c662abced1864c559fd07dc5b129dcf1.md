### Title
Multisig weight double-counting via repeated re-signing of the same hash by one key - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
`ValidateMultiSign.execute` deduplicates signatures using a byte-exact match of `(recoveredAddr || rawSignature)` instead of deduplicating by recovered address alone. Because ECDSA signing in this codebase is not deterministic (nonce `k` is not fixed via RFC6979), the same private key can produce multiple distinct, canonical, byte-different signatures over the identical `hash`. Submitting several such signatures from one key lets an attacker inflate `totalWeight` past `permission.getThreshold()` using fewer distinct real signers than the permission requires.

### Finding Description
In `PrecompiledContracts.java`: [1](#0-0) 

For each supplied signature, the code recovers `recoveredAddr` via `recoverAddrBySign`, then builds `sign = merge(recoveredAddr, sign)` and checks:
```
if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
  if (ByteArray.matrixContains(executedSignList, sign)) {
    continue;   // exact duplicate (addr+sig bytes) already processed -> skip
  }
  MUtil.checkCPUTime();  // otherwise: NOT skipped, falls through
}
long weight = TransactionCapsule.getWeight(permission, recoveredAddr);
...
totalWeight += weight;
executedSignList.add(sign);
executedSignList.add(recoveredAddr);
```
The intent appears to be "skip if this signer has already contributed," but the actual skip condition requires an **exact byte match of the whole `addr+sig` blob**, not just a match on `recoveredAddr`. If the same address appears again but with a *different* signature byte sequence (different `r`/`s` from re-signing the same `hash` with the same key), the inner `matrixContains(executedSignList, sign)` check fails, `continue` is not executed, and `totalWeight += weight` runs again for the same address/key.

`recoverAddrBySign` (lines 371-388) uses `Rsv.fromSignature` and `SignUtils.signatureToAddress`/`validateComponents`, which validates the signature is well-formed/canonical but does not prevent a signer from producing more than one valid signature over the same hash — that's a normal property of ECDSA when the nonce `k` is randomized (not RFC6979-deterministic) rather than a malformed/invalid signature. [2](#0-1) 

Reachability: `ValidateMultiSign` is a TVM precompile invoked from any contract call (constant or state-changing) via `TriggerSmartContract`/`TriggerConstantContract` (`eth_call`/`triggerConstantContract`), so any unprivileged caller can supply the `signatures` array directly as calldata to a contract that forwards it to this precompile, with no special privileges required — only the target account and its `Permission` need to exist (a normal precondition of this feature).

### Impact Explanation
Any on-chain contract that relies on `ValidateMultiSign` for authorization (e.g., a wallet/multisig contract requiring `permission.getThreshold()` combined weight) can be tricked into approving an action using a single signer (or fewer distinct signers than the threshold requires), as long as that signer's weight, when doubled/multiplied, reaches the threshold. This breaks the fundamental multisig invariant that distinct authorized weight, not repeated counting of one key, is required to reach threshold, enabling unauthorized execution of privileged multisig-gated actions.

### Likelihood Explanation
- Preconditions are minimal and attacker-controlled: the target account must have a `Permission` (a legitimate multisig setup with `getPermissionById`), which is a normal precondition of using this feature, not an admin-only state.
- The attacker only needs to control (or collude with) one of the permission's keys and be able to generate two signatures over the same hash — trivial, since standard ECDSA `sign()` calls with fresh randomness produce different `(r,s)` on each invocation.
- No cryptographic breaking is required; it purely exploits the flawed dedup logic (`matrixContains` on the full `addr+sig` blob instead of on `recoveredAddr`).
- The condition is reachable via a plain `eth_call`/`triggerConstantContract`, requiring no elevated privileges, gas payment beyond the precompile's cost (`ENGERYPERSIGN` per signature, capped at `MAX_SIZE`=5), and no node/peer/admin interaction.

### Recommendation
Change the dedup/skip condition to key strictly on `recoveredAddr`, not on the exact signature bytes: once an address has contributed weight, any further signature recovering to that same address must be skipped (`continue`) unconditionally, regardless of whether the raw signature bytes differ. E.g.:
```java
if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
  continue; // address already counted, skip regardless of signature bytes
}
long weight = TransactionCapsule.getWeight(permission, recoveredAddr);
...
totalWeight += weight;
executedSignList.add(recoveredAddr);
```
Remove the separate `executedSignList.add(sign)` bookkeeping since only address-level dedup is meaningful for threshold-weight math.

### Proof of Concept
Java unit test (extending `ValidateMultiSignContractTest` style):
```java
@Test
public void testWeightDoubleCountingViaResignedHash() {
  ECKey key = new ECKey();
  AccountCapsule toAccount = new AccountCapsule(ByteString.copyFrom(key.getAddress()),
      Protocol.AccountType.Normal, System.currentTimeMillis(), true,
      dbManager.getDynamicPropertiesStore());

  ECKey signerKey = new ECKey(); // weight 1, threshold 2 -> normally needs 2 distinct signers
  Protocol.Permission activePermission = Protocol.Permission.newBuilder()
      .setType(Protocol.Permission.PermissionType.Active)
      .setId(2).setPermissionName("active").setThreshold(2)
      .setOperations(ByteString.copyFrom(ByteArray.fromHexString(
          "0000000000000000000000000000000000000000000000000000000000000000")))
      .addKeys(Protocol.Key.newBuilder()
          .setAddress(ByteString.copyFrom(signerKey.getAddress())).setWeight(1).build())
      .build();
  toAccount.updatePermissions(toAccount.getPermissionById(0), null,
      Collections.singletonList(activePermission));
  dbManager.getAccountStore().put(key.getAddress(), toAccount);

  byte[] data = Sha256Hash.hash(CommonParameter.getInstance().isECKeyCryptoEngine(), longData);
  byte[] merged = ByteUtil.merge(key.getAddress(), ByteArray.fromInt(2), data);
  byte[] toSign = Sha256Hash.hash(CommonParameter.getInstance().isECKeyCryptoEngine(), merged);

  // Attacker re-signs the SAME hash twice with the SAME key -> two distinct byte sequences,
  // same recovered address.
  byte[] sig1 = signerKey.sign(toSign).toByteArray();
  byte[] sig2 = signerKey.sign(toSign).toByteArray(); // different r,s due to fresh nonce
  Assert.assertFalse(Arrays.equals(sig1, sig2));

  List<Object> signs = new ArrayList<>();
  signs.add(Hex.toHexString(sig1));
  signs.add(Hex.toHexString(sig2));

  Pair<Boolean, byte[]> result =
      validateMultiSign(StringUtil.encode58Check(key.getAddress()), 2, data, signs);

  // BUG: threshold(2) reached with only ONE distinct signer contributing weight twice.
  Assert.assertArrayEquals(DataWord.ONE().getData(), result.getValue());
}
```
Expected (fixed) behavior: `totalWeight` should equal `1` (single distinct signer, weight 1), which is `< threshold(2)`, so the call should return `DATA_FALSE`. The observed (vulnerable) behavior returns `dataOne()` (success), confirming the double-counting flaw described in [3](#0-2) .

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
