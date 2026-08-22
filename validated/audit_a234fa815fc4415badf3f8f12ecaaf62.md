### Title
Signature malleability allows a single multisig key's weight to be counted multiple times in `ValidateMultiSign.execute`, bypassing quorum threshold - (File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java)

### Summary
The `ValidateMultiSign` precompile deduplicates signatures using `ByteArray.matrixContains(executedSignList, sign)`, keyed on the exact raw signature bytes merged with the recovered address, not on the recovered address alone. Because ECDSA signatures are malleable (a signature `(R,S)` and `(R, N-S)` both recover to the same address but differ as raw bytes), an attacker who controls a single co-signer key can submit several distinct-but-malleable signatures of the same hash and have that one key's `weight` added to `totalWeight` multiple times, satisfying `permission.getThreshold()` without real quorum.

### Finding Description
In `PrecompiledContracts.java` (`ValidateMultiSign.execute`, around lines 1088-1106): [1](#0-0) 

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
```

The intended dedup logic appears to be: "if we've already counted this address, and this exact signature was already counted, skip it (idempotent for repeated identical signatures)." However, if the address was already counted but the *exact byte sequence* of `sign` differs (which trivially happens with ECDSA malleability — same key, same message hash, valid alternate `S` value `N-S`, or alternate recovery/format encodings that still recover to the same address), the inner `continue` is **not** taken. Execution falls through to `TransactionCapsule.getWeight(permission, recoveredAddr)` and `totalWeight += weight` again, and the new (still-different) `sign` byte array is appended to `executedSignList`, allowing the process to repeat up to `MAX_SIZE` (5) times.

`TransactionCapsule.getWeight` derives weight purely from the recovered address in the permission's key list, with no tracking of "address already granted weight this call" beyond the flawed sign-content check above. This means one authorized key, controlled by a single co-signer, can be presented multiple times via malleable signature variants to accumulate `weight * N` (`N` up to `MAX_SIZE`) instead of `weight` once — directly violating the multisig invariant that each key's weight counts once.

Existing checks do not stop this:
- `ByteArray.matrixContains(executedSignList, recoveredAddr)` — this check exists and detects the repeat address, but its outcome (`MUtil.checkCPUTime()`) does not prevent double counting; it appears intended only as an anti-DoS/CPU-time guard against many collisions.
- No comparison against previously-recovered addresses alone is used to `continue`; only the full `(recoveredAddr, sign)` tuple.

### Impact Explanation
This bypasses the intended M-of-N quorum semantics for `ValidateMultiSign`, a precompile explicitly designed for smart contracts to verify TRON permission-based multisignature authorization. A single co-owner with insufficient individual weight (e.g., weight 1 out of threshold 2, in a 2-of-3 scheme) can single-handedly satisfy the threshold using malleable variants of their own signature, without cooperation of other key holders. Any on-chain contract logic gating privileged actions (fund transfers, ownership changes, governance actions) on this precompile's boolean result can be unilaterally authorized by one weak-weight key holder — an unauthorized state change / authorization bypass, matching TRON bounty's "unauthorized account operations" impact class.

### Likelihood Explanation
- Attacker precondition: must control one legitimate private key that is part of the target account's `Permission` (a normal scenario if the attacker is themselves one of several co-signers with sub-threshold weight, i.e., "an ordinary funded account").
- No privileged role, RPC exploit, or node compromise required — this is purely a TVM contract call to the precompile at address `0x0000...1005` (or whichever address maps to `ValidateMultiSign`), reachable from any deployed contract.
- ECDSA malleability (computing `N - S` for a valid `(R,S)` signature) is trivial and well documented; producing several distinct valid encodings of a signature over the same fixed hash is cheap and deterministic.
- Cost is only the energy for the additional signature slots (`ENGERYPERSIGN` per entry, up to `MAX_SIZE`=5), which is negligible.
- Fully repeatable for any account/permission that uses this precompile with a weighted (non-unanimous) threshold.

### Recommendation
Deduplicate strictly by `recoveredAddr`, not by the raw `sign` bytes: if `matrixContains(executedAddrList, recoveredAddr)` is true, always `continue` (skip weight accumulation) instead of allowing fallthrough when the exact signature bytes differ. Maintain a separate list solely of recovered addresses already counted, and check membership in that list before adding weight, regardless of the signature encoding used to derive it.

### Proof of Concept
```java
// Pseudo-PoC illustrating the flaw in ValidateMultiSign.execute
// 1. Permission P has threshold = 2, with keyA (weight=1), keyB (weight=1), keyC (weight=1)
// 2. Attacker controls only keyA.
// 3. Attacker computes hash = Sha256Hash.hash(combine) as done in execute().
// 4. Attacker signs hash with keyA to get (R, S) -> sigA1.
// 5. Attacker derives a malleable variant sigA2 = (R, N - S) — still recovers to keyA's address.
// 6. Attacker calls the precompile with signatures = [sigA1, sigA2].
//
// Expected (correct) behavior: totalWeight == 1 (keyA counted once) -> threshold 2 NOT met -> DATA_FALSE.
// Actual behavior per code path:
//   iteration 1: recoveredAddr=keyA, executedSignList empty -> weight=1 added, totalWeight=1
//   iteration 2: recoveredAddr=keyA, matrixContains(list, keyA) == true,
//                matrixContains(list, merge(keyA, sigA2)) == false (different raw bytes)
//                -> continue NOT hit -> checkCPUTime() -> weight=1 added again -> totalWeight=2
//   totalWeight (2) >= threshold (2) -> returns dataOne() (valid!) with only one real key.
//
// JUnit-style assertion to add to ValidateMultiSignContractTest.java:
// assertEquals(DATA_FALSE, result_when_only_keyA_signs_with_malleable_variants);
``` [2](#0-1)

### Citations

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

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L1-1)
```java
package org.tron.common.runtime.vm;
```
