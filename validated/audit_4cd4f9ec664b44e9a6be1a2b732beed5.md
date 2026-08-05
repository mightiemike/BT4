### Title
Signature recycling in `ValidateMultiSign` precompile allows one signer to satisfy multisig threshold with distinct-but-equivalent signatures - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The `ValidateMultiSign` precompiled contract, used by TVM smart contracts to check on-chain multisig `Permission` weight thresholds, deduplicates recovered signatures by the pair `(recoveredAddress, rawSignatureBytes)` rather than by `recoveredAddress` alone. This mirrors the reported `UserOpMultiSigVerifier` bug class: the report's contract deduplicated by index but not by signer identity, letting one signer be counted more than once; here the dedup key includes the raw signature bytes, so a signer holding a single private key can supply two distinct valid signatures over the same hash (ECDSA is not required to be deterministic — different nonce `k` yields different valid `(r,s)` pairs recovering to the same address) and both get counted toward `totalWeight`.

### Finding Description
The vulnerable loop is: [1](#0-0) 

For each provided signature, the contract recovers the signer address via `recoverAddrBySign`, then checks whether that address has been seen (`matrixContains(executedSignList, recoveredAddr)`). If the address was seen but the exact same raw-signature bytes were not (`!matrixContains(executedSignList, sign)`), the code does **not** skip — it only calls `MUtil.checkCPUTime()` and falls through to add the key's weight to `totalWeight` again [2](#0-1) .

This means the anti-double-counting mechanism only rejects byte-for-byte identical signature values, not repeated use of the same signer's key. Since a single ECDSA private key can produce multiple distinct valid `(r,s,v)` signatures for the same message hash (unless a strictly deterministic k-derivation like RFC6979 is enforced and the signature is uniquely canonicalized), an attacker holding one key with weight `w` in the `Permission` can submit N different valid signatures from that same key to accumulate `N*w` toward `permission.getThreshold()`, bypassing the intent that `threshold` requires distinct co-signers.

By contrast, the equivalent transaction-level check `TransactionCapsule.checkWeight` correctly deduplicates by recovered **address** only (via `addMap.containsKey(base64)` keyed on the encoded address, throwing "has signed twice!" on a repeat) [3](#0-2) , showing that the correct semantics (dedupe by signer identity, not signature bytes) were already implemented elsewhere in the codebase but not applied consistently in the TVM precompile.

Additionally, `AccountPermissionUpdateActuator.checkPermission` already enforces that `Permission.getKeysList()` addresses are distinct [4](#0-3) , so the analogous "duplicate owner in the list" vector from the report is closed; the reachable variant here is recycling via *distinct signature bytes from the same key*, not duplicate list entries.

### Impact Explanation
Any TRON smart contract that relies on `ValidateMultiSign` (a public precompile reachable by any unprivileged user/contract via `TriggerSmartContract`) to gate authorization — e.g., a custodial wallet or governance contract checking that `M-of-N` account permission signers approved an action — can have its threshold bypassed by a single colluding/compromised signer supplying multiple distinct valid signatures for their own key. This is a concrete authorization-bypass / accounting-of-weight impact: contract logic that treats `validateMultiSign(...) == true` as proof of `threshold` independent approvals is not guaranteed that multiple independent signers approved.

### Likelihood Explanation
Medium. It requires: (1) a smart contract that uses `ValidateMultiSign` for authorization decisions with a `Permission` where the attacker controls one signing key with weight less than the full threshold but enough combined with recycled weight to reach it, and (2) the attacker being able to produce more than one distinct valid ECDSA signature over the identical hash (trivial — sign the same hash from the same key using different randomness/tooling, or use signature malleability if not canonicalized). No special privilege is needed to call the precompile.

### Recommendation
Change the dedup key in `ValidateMultiSign.execute` from `(recoveredAddr, sign)` to `recoveredAddr` alone, matching `TransactionCapsule.checkWeight`'s semantics: once an address has been credited with weight, any further signature recovering to that same address must be rejected/skipped, regardless of whether the raw signature bytes differ. Concretely, replace the `matrixContains(executedSignList, sign)` inner check (lines 1093–1096) with an unconditional `continue` when `matrixContains(executedSignList, recoveredAddr)` is true.

### Proof of Concept
1. Create an account with an `Active` `Permission` containing key `A` (weight `w1`) and key `B` (weight `w2`), threshold `T = w1 + w2` (i.e., both signers are required).
2. Attacker controls only key `A`. Using key `A`, produce two different valid ECDSA signatures `sigA1` and `sigA2` over the same `hash` (different `k`/nonce; BouncyCastle's default non-deterministic signer or malleability yields distinct byte encodings recovering to the same address).
3. Deploy/trigger a contract calling the `ValidateMultiSign` precompile with `signatures = [sigA1, sigA2]`.
4. In `execute()`: iteration 1 recovers address `A`, `weight += w1`, added to `executedSignList`. Iteration 2 recovers address `A` again; `matrixContains(executedSignList, recoveredAddr)` is true, but `matrixContains(executedSignList, merge(A, sigA2))` is false (different bytes from `sigA1`), so it does **not** `continue` — it adds `w1` again, making `totalWeight = 2*w1`.
5. If `2*w1 >= T`, the call returns `dataOne()` (success) even though key `B` never signed, bypassing the intended 2-of-2 multisig requirement [5](#0-4) .

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1086-1110)
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
            }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L233-269)
```java
  public static long checkWeight(Permission permission, List<ByteString> sigs, byte[] hash,
      List<ByteString> approveList)
      throws SignatureException, PermissionException, SignatureFormatException {
    long currentWeight = 0;
    if (sigs.size() > permission.getKeysCount()) {
      throw new PermissionException(
          "Signature count is " + (sigs.size()) + " more than key counts of permission : "
              + permission.getKeysCount());
    }
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
      if (approveList != null) {
        approveList.add(ByteString.copyFrom(address)); //out put approve list.
      }
      currentWeight += weight;
    }
    return currentWeight;
```

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L95-104)
```java
    long weightSum = 0;
    List<ByteString> addressList = permission.getKeysList()
        .stream()
        .map(x -> x.getAddress())
        .distinct()
        .collect(toList());
    if (addressList.size() != permission.getKeysList().size()) {
      throw new ContractValidateException(
          "address should be distinct in permission " + permission.getType());
    }
```
