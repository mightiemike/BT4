### Title
Weight double-counting in `ValidateMultiSign` precompile allows a single key to satisfy multi-sig threshold via duplicate/malleable signatures - (File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java)

### Summary
This maps the report's "same underlying asset counted twice" bug class onto TVM signature-weight accounting: the `ValidateMultiSign` precompiled contract sums up a `Permission`'s key weights per submitted signature, but its de-duplication check only skips a signature when the *exact byte-identical* signature has already been counted — not when the same recovered address (the "unique key," analogous to the report's "unique asset") has already contributed weight. This lets the same key's weight be added to `totalWeight` more than once.

### Finding Description
In `PrecompiledContracts.ValidateMultiSign.execute()`, weights are accumulated like this: [1](#0-0) 

For each provided signature, the code recovers an address, builds `sign = merge(recoveredAddr, sign)`, and only skips adding weight for that iteration if `executedSignList` already contains the exact merged `sign` bytes:

```
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
``` [2](#0-1) 

If `recoveredAddr` is already present but the exact `sign` bytes differ (e.g. a second, distinct valid signature by the same key over the same hash), the function does **not** skip — it falls through, recomputes `weight` for that same address, and adds it to `totalWeight` again. This is the same root cause pattern as the reported bug: the design assumes each contributing entity (there: "asset" per strategy; here: "address/key" per signature) is unique per accounting pass, but nothing enforces that uniqueness before the values are summed, so the same key's weight is counted multiple times, inflating the aggregate (`totalWeight`) just as `totalAssets()` was inflated by double-counted balances.

This is directly reachable and unprivileged: `ValidateMultiSign` is a TVM precompiled contract invoked by any smart contract via `STATICCALL`/`CALL` from any user-submitted transaction, so any external caller can trigger this accounting path.

By contrast, the same-purpose logic used for real transaction signature verification, `TransactionCapsule.checkWeight()`, correctly rejects/deduplicates by address rather than raw signature bytes: [3](#0-2) 

This confirms the codebase's own intended invariant ("one address should contribute weight at most once") — but `ValidateMultiSign` fails to enforce it identically, deduplicating by raw signature bytes instead of by recovered address.

### Impact Explanation
Impact is High for any smart contract that relies on `ValidateMultiSign` (address `0x0000...1000c`, TVM precompile) to gate privileged on-chain logic (e.g. custom multisig wallets, DAOs, escrow contracts built on TRON that use this precompile to check TRON account permissions). A single signer holding one of the required keys can supply two distinct valid signatures for the *same* key/hash pair and have that key's weight counted twice (or more, bounded by `MAX_SIZE = 5`), effectively lowering the number of independent signers actually required to reach `permission.getThreshold()`. This breaks the security guarantee of the multisig scheme enforced by contracts depending on this precompile, potentially enabling unauthorized execution of privileged contract logic that should require multiple independent approvers.

### Likelihood Explanation
Likelihood is Low/Medium: it requires (a) a contract on-chain that uses `ValidateMultiSign` for access control, and (b) the attacker to already control one of the permission's keys and be able to produce a second syntactically-different but validly-recovering signature over the same hash (e.g. via ECDSA signature malleability or simply asking the same signer to sign the same hash twice with different nonce/format producing different raw bytes but same recovered address is not generally possible for standard ECDSA without malleability, but malleable/alternate encodings of `(r,s,v)` recovering to the same address are a known class of issue). Exploitability depends on whether malleable/alternate valid signature encodings are accepted by `recoverAddrBySign`/`SignUtils`, which was not fully confirmed within the available index.

### Recommendation
In `ValidateMultiSign.execute()` (and any similar precompile logic), deduplicate strictly by `recoveredAddr`, not by the merged signature bytes — i.e., once an address has contributed weight, any further signature recovering to that same address must be skipped entirely (as `TransactionCapsule.checkWeight()` does by throwing/deduping on address). Concretely, replace the byte-exact `matrixContains(executedSignList, sign)` check with an address-only "already counted" check that unconditionally `continue`s once `recoveredAddr` has previously contributed weight, regardless of the specific signature bytes.

### Proof of Concept
1. Create a TRON account with an `Active` permission of type multisig containing two keys `key1`, `key2`, each weight 1, threshold 2 (as in `ValidateMultiSignContractTest.testDifferentCase`) [4](#0-3) .
2. Have only `key1` sign the target hash, but produce two distinct valid signature byte sequences for `key1` over the same hash (e.g. via signature malleability/alternate valid encoding), and submit both as the `signatures` array to `ValidateMultiSign`, omitting `key2`'s signature.
3. In `execute()`, the first `key1` signature adds weight 1 (`totalWeight = 1`). For the second `key1` signature, `recoveredAddr` is already in `executedSignList`, but since the raw `sign` bytes differ from the first, `matrixContains(executedSignList, sign)` is `false`, so the loop does not `continue` and instead adds weight 1 again (`totalWeight = 2`) [2](#0-1) .
4. `totalWeight (2) >= permission.getThreshold() (2)` returns `true` even though only one of the two required independent keys actually signed, satisfying the multisig check improperly.

Note: full confirmation that TRON's ECDSA signature recovery (`recoverAddrBySign`/`SignUtils`) accepts multiple distinct byte encodings recovering to the same address (malleability) could not be completed within the available index; this should be verified directly against `crypto/src/main/java/org/tron/common/crypto/ECKey.java` and `SignUtils.java` before relying on this PoC path, but the underlying accounting flaw (dedup by signature bytes instead of by address) is confirmed in the code itself.

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

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L242-263)
```java
    HashMap addMap = new HashMap();
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

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L80-95)
```java
    Protocol.Permission activePermission =
        Protocol.Permission.newBuilder()
            .setType(Protocol.Permission.PermissionType.Active)
            .setId(2)
            .setPermissionName("active")
            .setThreshold(2)
            .setOperations(ByteString.copyFrom(ByteArray
                .fromHexString("0000000000000000000000000000000000000000000000000000000000000000")))
            .addKeys(Protocol.Key.newBuilder().setAddress(ByteString.copyFrom(key1.getAddress()))
                .setWeight(1).build())
            .addKeys(
                Protocol.Key.newBuilder()
                    .setAddress(ByteString.copyFrom(key2.getAddress()))
                    .setWeight(1)
                    .build())
            .build();
```
