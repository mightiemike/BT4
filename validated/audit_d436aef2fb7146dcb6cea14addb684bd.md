### Title
Signature malleability allows double-counting of a single key's weight in `ValidateMultiSign` precompile - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
The `ValidateMultiSign` precompiled contract (invoked from TVM via the `validatemultisign` opcode) accumulates signature weight for multi-sig permission checks, deduplicating by recovered address plus raw signature bytes. Because ECDSA signatures are malleable and the contract's dedup logic explicitly tolerates a different signature for an address it has already seen (only skipping when the exact same signature bytes repeat), an attacker who controls a single private key can submit two malleable variants of the same signature to have that key's weight counted twice, potentially satisfying a multi-key threshold with only one real signer.

### Finding Description
`ValidateMultiSign.execute` in `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java` (~lines 1080-1106) processes an array of ECDSA signatures against a `Permission`:

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
    return Pair.of(true, DATA_FALSE);
  }
  totalWeight += weight;
  executedSignList.add(sign);
  executedSignList.add(recoveredAddr);
}
``` [1](#0-0) 

The intended dedup rule is: skip only exact-duplicate signatures for an address already counted (`continue`). But when the *same recovered address* appears again with a *different* signature byte encoding, execution falls through the `continue`, calls `MUtil.checkCPUTime()` (a DoS guard, not a rejection), and then proceeds to add `weight` again and append the new signature/address pair to `executedSignList`. This means a second, distinct-but-valid signature from the same key is fully re-counted rather than rejected.

ECDSA signatures recovered via `recoverAddrBySign` (backed by `ECKey`/`SignUtils`) are malleable: for any valid `(r, s, v)`, the pair `(r, N-s, v')` is also a valid signature recovering to the same address, per the standard secp256k1 malleability property. `ECKey.ECDSASignature` explicitly documents and implements this transformation (`toCanonicalised()`), confirming the codebase is aware `s` and `N-s` are both valid for the same key/message but `validateComponents()`/canonicalization is not enforced inside `ValidateMultiSign`'s signature-recovery path. Thus an attacker holding one private key can trivially derive a second, byte-distinct signature for the identical message and identical recovered address (e.g., by flipping `s -> N-s` and `v` accordingly), producing two entries in `signatures[]` that are unequal as raw bytes but both attributable to the same key. [2](#0-1) 

### Impact Explanation
`validatemultisign` is a public TVM precompile reachable from any smart contract call (unprivileged, attacker-controlled bytecode can call it directly), used by contracts to gate privileged actions on multi-signature account permissions (threshold-based authorization). By exploiting malleability to submit two variants of a single signer's signature, an attacker can inflate `totalWeight` and satisfy a permission `threshold` that was designed to require multiple independent key holders. This breaks the security guarantee of on-chain multi-signature authorization for any contract or account relying on `validatemultisign` with a threshold greater than a single key's weight but achievable by doubling one key's weight — enabling unauthorized execution of multi-sig-gated operations (asset transfers, privileged actuator calls proxied through contracts, etc.) with fewer real signers than intended.

### Likelihood Explanation
Likelihood is high for any permission configured with `threshold` reachable by summing one key's weight twice (e.g., weight-1 keys with threshold 2, or generally `threshold <= 2 * weight(any single key)`). No privileged access, node compromise, or leaked keys are required — only knowledge of a valid signature and elementary secp256k1 arithmetic to produce the malleable counterpart, which is public-domain cryptography. The vulnerable code path is directly reachable from any TVM contract call to the `validatemultisign` opcode.

### Recommendation
In `ValidateMultiSign.execute` (and the analogous `BatchValidateSign` precompile if it has similar dedup logic), deduplicate strictly by **recovered address**, not by `(address, raw signature bytes)` pairs — i.e., once an address has contributed weight, any further signature recovering to that same address must be skipped entirely, regardless of byte-level differences. Additionally, enforce canonical (low-`s`) signature form (reject if `s > SECP256K1N/2`) before/while recovering the address, consistent with `ECKey.ECDSASignature.validateComponents()`/`toCanonicalised()`, to eliminate malleable variants outright.

### Proof of Concept
1. Attacker holds a single private key `k` mapped to address `A` with weight `w` in a target account's `Permission` (e.g., `w=1`, `threshold=2`, with a second unrelated key `B` also weight 1).
2. Attacker signs `hash` with `k` to get signature `sig1 = (r, s, v)`.
3. Attacker derives the malleable counterpart `sig2 = (r, N-s, v')` (standard secp256k1 transform), which also recovers to address `A` for the same `hash`.
4. Attacker calls the `validatemultisign(address, permissionId, hash, [sig1, sig2])` precompile.
5. In the loop: for `sig1`, `recoveredAddr = A`, not yet in `executedSignList`, so `weight=w` is added; for `sig2`, `recoveredAddr = A` again — `matrixContains(executedSignList, A)` is true, but `matrixContains(executedSignList, merge(A, sig2))` is false (different bytes) — the `continue` is skipped, `weight=w` is added again.
6. `totalWeight = 2w`, meeting `threshold=2` using only one real signer `A`, returning `DataWord.ONE()` (success) despite key `B` never having signed. [3](#0-2)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1080-1109)
```java
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
```

**File:** crypto/src/main/java/org/tron/common/crypto/ECKey.java (L944-963)
```java
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
