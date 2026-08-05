### Title
Weight double-counting for a single signer in `ValidateMultiSign` TVM precompile due to raw-signature-byte deduplication instead of signer-identity deduplication - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The `ValidateMultiSign` precompiled contract, used by TVM smart contracts to verify multi-signature/multi-key permission thresholds on-chain, deduplicates signatures by comparing raw signature bytes rather than by the recovered signer's address/identity. This mirrors the reported `bigclaim()` bug class ("no check to ensure the requests are signed from different signers"): the code assumes distinct signature byte-strings imply distinct signers, but that assumption is false for ECDSA.

### Finding Description
`ValidateMultiSign.execute()` accumulates permission weight per signature in the caller-supplied array: [1](#0-0) 

For each signature it recovers the signer address, then checks whether that address is already in `executedSignList`. If it is, it only `continue`s (skips re-adding weight) when the *exact byte-for-byte* merged `sign` (recoveredAddr + raw signature bytes) is also already present: [2](#0-1) 

If the recovered address is a repeat but the raw signature bytes differ, the loop does **not** skip — it proceeds to add the signer's weight to `totalWeight` again: [3](#0-2) 

This is exactly the class of bug described in the report: the check for "enough unique signers" is implemented at the wrong granularity (raw signature bytes) instead of signer identity. Contrast this with the equivalent logic in `TransactionCapsule.checkWeight()`, used for on-chain transaction multi-sig, which explicitly dedups by the recovered **address** (`encode58Check(address)` as the map key) and throws `"has signed twice!"` if the same address appears more than once, regardless of the exact signature bytes used: [4](#0-3) 

That fix is *not* mirrored in the `ValidateMultiSign` precompile's dedup logic. Because ECDSA signatures are malleable (for any valid `(r, s)` over curve order `n`, `(r, n-s)` combined with the flipped recovery id is also a valid signature recovering to the same address, and this transformation requires no knowledge of the private key — just one existing valid signature), an attacker in possession of a single valid signature from one signer can trivially derive a second, byte-distinct signature that recovers to the same address. Both would pass the `weight == 0` check and both would have their weight summed, since the `sign` byte-comparison in `executedSignList` would not match.

Whether the codebase additionally enforces canonical (low-S) signatures inside the `recoverAddrBySign` path could not be fully confirmed in this pass; if it does not, the malleable variant is accepted and the double-count is straightforward. Even absent malleability, the structural root cause remains: the dedup key is "exact raw signature bytes" rather than "recovered signer address," which is the same category of flaw as the reported `bigclaim()` issue.

### Impact Explanation
`ValidateMultiSign` is exposed as a TVM precompiled contract, callable from any smart contract (e.g., custom multi-signature wallets, DAOs, or escrow contracts built on TRON that rely on account `Permission`/threshold semantics for authorization). If weight can be double- (or multiply-) counted for a single signer, a threshold nominally requiring N independent keys can be satisfied by fewer real signers than intended, up to a single compromised or colluding key — directly undermining the authorization/accounting guarantee that on-chain multisig contracts depend on.

### Likelihood Explanation
Exploitation requires: (1) a contract that uses `ValidateMultiSign` to gate a privileged action behind a weight threshold, and (2) an attacker who already holds one valid signature from one of the permissioned keys (e.g., a legitimately obtained approval signature, or the signer being the attacker). Deriving the malleable second signature is a pure, well-known arithmetic operation with no cryptographic barrier. The main open uncertainty is whether a canonical/low-S check elsewhere in the signature-recovery path already blocks the malleable variant; this could not be conclusively verified from the available code and would need explicit confirmation (e.g., by inspecting `ECKey`'s signature verification/recovery routines and `recoverAddrBySign`).

### Recommendation
In `ValidateMultiSign.execute()` (and any other precompile with similar loops, e.g. `BatchValidateSign`), deduplicate by the **recovered signer address**, not by raw signature bytes — mirroring the fix already applied in `TransactionCapsule.checkWeight()`. Reject a signature outright (rather than merely deduping) once its signer has already contributed weight in the same call, and additionally enforce canonical (low-S) signature form during recovery to eliminate the malleability vector entirely.

### Proof of Concept
1. Signer A holds key `K` with weight `w` in `Permission P`, where `P.threshold == 2*w` and no other signer provides weight.
2. A produces one valid signature `sig1 = (r, s, v)` over `hash`.
3. Anyone (attacker, or A itself) computes the malleable counterpart `sig2 = (r, n-s, v')` (`v'` = flipped recovery id), which recovers to the same address as `sig1`.
4. A smart contract calls the `ValidateMultiSign` precompile with `signatures = [sig1, sig2]`.
5. In `PrecompiledContracts.ValidateMultiSign.execute()`, the first iteration adds weight `w` for A; the second iteration finds A's address already in `executedSignList` but the raw `sign` bytes differ, so it does not `continue` — it adds weight `w` again.
6. `totalWeight == 2*w >= permission.getThreshold()`, and the precompile returns "success" despite only one real signer having participated — bypassing the intended multi-signer requirement, analogous to the reported `bigclaim()` flaw.

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
