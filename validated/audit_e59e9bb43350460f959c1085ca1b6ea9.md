### Title
Duplicate-signer weight double-counting in `ValidateMultiSign` precompile threshold check - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The TVM precompiled contract `ValidateMultiSign` (address `0x66`) recomputes a multi-sig account's accumulated weight by iterating over caller-supplied signatures and recovering the signer for each one. Instead of deduplicating by **recovered signer address** (as the equivalent transaction-level check `TransactionCapsule.checkWeight` does), it deduplicates by the **raw signature bytes**. This is the same bug class as the reported `MultiSigGenVerifier` issue: a submitter can supply two distinct byte-level signatures that recover to the same signer address and have that single signer's weight counted twice (or more) toward the permission's `threshold`.

### Finding Description
In `ValidateMultiSign.execute`: [1](#0-0) 

the loop recovers `recoveredAddr` for each supplied signature, merges it with the raw signature bytes into `sign`, and only skips accumulating weight (`continue`) when the exact merged byte sequence `sign` was already seen. If `recoveredAddr` was seen before but the new `sign` bytes differ (e.g., a second, distinct valid signature for the same key/hash — obtainable via ECDSA signature malleability, i.e., `(r, s)` and `(r, n-s)` with the adjusted recovery id both recover to the same address for the same message), the code does **not** skip; it merely calls `MUtil.checkCPUTime()` and falls through to add `weight` again via `TransactionCapsule.getWeight(permission, recoveredAddr)`: [2](#0-1) 

This directly mirrors the reported flaw in `MultiSigGenVerifier.signedDataExecution`: the threshold accumulation trusts uniqueness of raw signature material rather than uniqueness of the recovered signer, so a single signer's weight can be applied more than once.

By contrast, the account/transaction-level equivalent, `TransactionCapsule.checkWeight`, correctly deduplicates by the recovered **address**, throwing `PermissionException` ("has signed twice!") if the same address appears twice regardless of signature bytes: [3](#0-2) 

This shows the `ValidateMultiSign` precompile's check is inconsistent with, and weaker than, the transaction-broadcast path's dedup logic.

### Impact Explanation
`ValidateMultiSign` is a public precompiled contract reachable from any smart contract or user transaction; it is commonly used by dApps/smart-contract wallets to verify off-chain multi-sig approvals for an account's `Permission` (weights/threshold) on-chain. If a caller can obtain two malleable signature variants from a single colluding signer, they can pass a multi-sig `threshold` (e.g., 2-of-3) using only one signer's authorization, causing the smart contract logic gated by `ValidateMultiSign`'s result to execute as if multiple independent signers approved it. This is a concrete authorization/accounting-bypass impact for any TVM contract relying on this precompile for multi-signature authorization.

### Likelihood Explanation
Exploitation requires: (1) a colluding or compromised signer willing to produce a second malleable signature for the same message/key (straightforward if `validateComponents()` does not enforce canonical low-S — I could not fully confirm the canonicalization behavior of `ECKey`/`SignatureInterface.validateComponents()` within the available investigation), and (2) a contract that relies on `ValidateMultiSign`'s boolean result for authorization decisions with more than one required signer. Given the precompile is public and requires no privileged access, and the flawed byte-level dedup is unconditionally reachable, likelihood is assessed as reasonable/plausible pending confirmation of whether malleable signature acceptance is otherwise blocked elsewhere in the signature-parsing path (`Rsv.fromSignature`, `SignUtils.fromComponents`, `validateComponents`).

### Recommendation
Change the duplicate-detection key in `ValidateMultiSign.execute` from the merged `(recoveredAddr, signatureBytes)` byte array to the recovered address alone, matching `TransactionCapsule.checkWeight`'s approach: track `recoveredAddr` values seen so far and unconditionally `continue` (skip weight accumulation) whenever an address repeats, instead of only skipping on exact signature-byte match. Additionally, confirm/enforce canonical (low-S) signature validation in the signature-recovery path so that ECDSA malleability cannot produce two distinct valid signatures for the same signer/message in the first place.

### Proof of Concept
1. Create an account with an Active permission requiring threshold 2, with two keys `key1` (weight 1) and `key2` (weight 1) — same setup used in `ValidateMultiSignContractTest.testDifferentCase`: [4](#0-3) 
2. Using only `key1`, produce two distinct valid ECDSA signatures for the same `toSign` hash by exploiting signature malleability (submit `(r, s)` and the malleable counterpart `(r, n-s, adjusted-v)`, both of which recover to `key1`'s address).
3. Call `ValidateMultiSign` with `signs = [sig1_malleable_variantA, sig1_malleable_variantB]`.
4. Because the dedup check in `PrecompiledContracts.ValidateMultiSign.execute` (lines 1088–1106) compares merged signature bytes rather than just the recovered address, both entries pass the "not seen before" check and each contributes `key1`'s weight (1+1=2), reaching the threshold of 2 with signatures from only one real signer — exactly analogous to the reported `MultiSigGenVerifier` single-signature-passes-threshold flaw.

Note: I was unable to fully verify within the available tool budget whether `SignatureInterface.validateComponents()` / `ECKey` enforce canonical low-S signatures, which would be a prerequisite for step 2's malleable-signature generation to succeed; this should be confirmed to fully validate exploitability.

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

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L80-99)
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

    toAccount
        .updatePermissions(toAccount.getPermissionById(0), null,
            Collections.singletonList(activePermission));
```
