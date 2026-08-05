### Title
Multisig weight duplication via signature malleability in `ValidateMultiSign` precompile - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Finding Description
The `ValidateMultiSign` precompile's duplicate-signature guard relies on `ByteArray.matrixContains(executedSignList, sign)`, which performs a raw byte-array equality check (`Arrays.equals(sobj, obj)`) against the previously processed signature bytes, not against the recovered signer address: [1](#0-0) 

Because the dedup key is the raw `sign` bytes rather than the `recoverAddrBySign` output, an attacker can submit two distinct-byte signatures (e.g. produced via ECDSA signature malleability — flipping `s` to `n-s` together with the recovery id) that are not byte-identical but both recover to the same address. Both entries fail the `matrixContains` check (since the byte arrays differ), so both proceed through `MUtil.checkCPUTime()` and are treated as independent, valid signatures, each contributing that key's `Permission` weight toward the total. This allows a single signer's weight to be counted more than once against `permission.getThreshold()`.

### Impact Explanation
If confirmed to reach the weight-accumulation step unconditionally after the byte-level dedup, this would allow an attacker holding only a subset of keys (insufficient combined weight) to inflate the effective total weight by submitting malleated duplicate signatures for one key, satisfying `permission.getThreshold()` without holding enough distinct signer weight. In a contract that gates fund transfers or privileged actions on `ValidateMultiSign` returning success, this would constitute a TVM-level authorization bypass for multisig-protected transfers.

### Likelihood Explanation
I was unable to fully re-verify the complete `execute()` method body of `PrecompiledContracts.ValidateMultiSign` in this session (tool budget exhausted before a clean read of that function), so I cannot confirm with certainty that there is no additional address-level dedup step (e.g., a separate `Set`/map keyed by recovered address) elsewhere in the weight-accumulation loop that would neutralize this issue. The confirmed piece of evidence — `ByteArray.matrixContains` comparing raw signature bytes — is consistent with the described flaw, but full confirmation of the downstream weight-summing logic (whether `recoveredAddr` is separately deduplicated before `getWeight`/`totalWeight += weight` is applied) was not completed.

### Recommendation
Given the confirmed byte-level dedup in `ByteArray.matrixContains`, the fix (to be validated against the actual `execute()` logic) should deduplicate on the **recovered address**, not the raw signature bytes — e.g., maintain a `Set<ByteString>`/`List<byte[]>` of already-counted recovered addresses and skip weight addition if the address was already seen, regardless of whether the underlying signature bytes are malleated.

### Proof of Concept
Extend `ValidateMultiSignContractTest.testDifferentCase` (framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java) with a case where `signatures = [sigA1, sigA2]`, both derived from the same private key/address `A` via signature malleability (same `r`, `s` and `n-s` variants with corresponding recovery ids) but differing in raw bytes, and assert that the computed total weight only counts address `A`'s weight once (i.e., `totalWeight == weight(A)`, not `2 * weight(A)`), and that the call fails when `weight(A) < threshold` even though `2 * weight(A) >= threshold`.

**Caveat**: This finding is based on confirmed evidence of `ByteArray.matrixContains`'s raw-byte comparison semantics; I could not fully re-confirm the exact weight-accumulation code path in `PrecompiledContracts.java` within the available tool budget. A Devin session with full file access should verify the complete `ValidateMultiSign.execute()` method to confirm whether any address-level dedup exists before treating this as conclusively proven.

### Citations

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
