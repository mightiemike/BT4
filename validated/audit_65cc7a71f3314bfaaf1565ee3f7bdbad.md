### Title
Energy Underpricing in `ValidateMultiSign` via ABI Offset/Length Decoupling from `data.length` - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
`ValidateMultiSign.getEnergyForData` derives the energy charge purely from `rawData.length` under an assumed canonical ABI layout (`ABI_HEADER_WORDS=5`, `ABI_ITEM_WORDS=5` per signature), while `execute()` determines the actual number of signatures to recover (and thus the number of `recoverAddrBySign`/ecrecover calls) from the array-length word located at `words[words[3].intValueSafe()/WORD_SIZE]`. Because these two computations are independent, an attacker can craft `rawData` whose byte layout reuses/overlaps signature-data words (non-canonical but still parseable) so that `data.length` implies a low `cnt`, while the parsed array-length word still reports up to `MAX_SIZE` (5) signatures, causing more `ecrecover` operations to be executed than were paid for.

### Finding Description
`getEnergyForData` computes: [1](#0-0) 
`cnt = (data.length / WORD_SIZE - 5) / 5`, energy `= cnt * ENGERYPERSIGN`. This formula assumes each signature item consumes exactly 5 words (offset + length + up to 3 words of signature bytes) in a strictly canonical, non-overlapping ABI encoding.

`execute()`, however, derives the real signature count from the dynamic-array length word reached via the offset in `words[3]`: [2](#0-1) 
`words[3].intValueSafe()/WORD_SIZE` locates the array-length slot, and the resulting `sigArraySize`/`signatures.length` (capped at `MAX_SIZE=5`) governs how many times `recoverAddrBySign` is invoked in the weight-accumulation loop: [3](#0-2) 

The strict-encoding guard that would tie these two together (`isValidAbiEncoding`, gated by `VMConfig.allowTvmOsaka()`) is only applied conditionally: [4](#0-3) 
When `allowTvmOsaka()` is false (pre-activation window on any given chain/fork, or a chain that has not yet enabled TIP-854), no validation forces `data.length` to correspond to the canonical per-item word count implied by the declared array size. An attacker can therefore construct offsets in the `bytes[]` signatures section that overlap/reuse the same underlying signature bytes (or otherwise pack the encoding non-canonically) so that the declared array length is 5 (the max allowed) while the total `rawData.length` is only sized for e.g. 1 canonical item. `getEnergyForData` then reports `cnt=1` (or 0), but `execute()` still performs 5 full signature-recovery operations in the accumulation loop.

I was unable to directly inspect the bodies of `extractSigArray`/`extractBytesArray` (grep on the exact file did not return matches, likely due to indexing limits), so the precise overlap mechanics of the byte-extraction helpers are not fully confirmed from source; the core decoupling between `getEnergyForData`'s length-based formula and `execute()`'s offset/length-word-based signature count is, however, directly supported by the cited code.

### Impact Explanation
An unprivileged attacker submitting a `TriggerSmartContract` invoking `ValidateMultiSign` can force up to 5 `ecrecover`-equivalent signature recoveries (`recoverAddrBySign`) while paying energy computed for fewer (potentially 1) signatures. This is a materially underpriced computation: full-node/validator CPU cost for signature recovery is not proportionally billed, enabling spam/DoS at below-market energy cost, especially at scale via many such crafted transactions.

### Likelihood Explanation
Preconditions: the attacker fully controls the byte layout of the precompile's `rawData` as part of ordinary `TriggerSmartContract` calldata; no privileged access is required. The vulnerability is exploitable whenever `VMConfig.allowTvmOsaka()` is not active (pre-hardfork window) or wherever a non-canonical-but-parseable layout can still satisfy the guard's accepted shape. Since the guard is a hardfork-conditioned feature flag, there is a real, repeatable window of exposure prior to activation across chains that adopt this codebase.

### Recommendation
Compute the energy charge from the actually-parsed signature count (post-ABI-parsing, capped at `MAX_SIZE`) rather than from raw `data.length`, e.g. `energy = signatures.length * ENGERYPERSIGN`, evaluated after `extractSigArray`/`extractBytesArray` resolve the true array size. Alternatively/additionally, make the strict ABI-encoding validation (`isValidAbiEncoding`) unconditional (not gated by `allowTvmOsaka()`), and have it verify that offsets are non-overlapping and that `data.length` exactly matches the space required by the declared array size, closing the decoupling window entirely.

### Proof of Concept
Java unit test outline (to be placed alongside `framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java`):
1. Build a canonical `rawData` encoding `address`, `permissionId`, `data` offset, `signatures` offset, followed by a `signatures` array of length 5, where all 5 offsets inside the array point to the same 65-byte signature payload (reused/overlapping bytes) instead of 5 distinct canonical items.
2. Assert `new ValidateMultiSign().getEnergyForData(rawData)` returns an energy value corresponding to `cnt < 5` (e.g., `cnt == 1`), computed via `(rawData.length / WORD_SIZE - 5) / 5`.
3. Instrument/spy `recoverAddrBySign` (or count via a permission with 5 distinct low-weight keys summing to the threshold only if all 5 signatures are processed) to confirm `execute(rawData)` performs 5 signature-recovery operations and returns success (`dataOne()`), proving actual work exceeded the billed `cnt`.
4. Assert the mismatch: `actualRecoverCalls (5) > cnt derived from getEnergyForData (1)`, demonstrating underpriced computation.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1044-1049)
```java
    @Override
    public long getEnergyForData(byte[] data) {
      long cnt = (data.length / WORD_SIZE - 5) / 5;
      // one sign 1500, half of ecrecover
      return cnt * ENGERYPERSIGN;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1053-1056)
```java
      if (VMConfig.allowTvmOsaka()
          && !isValidAbiEncoding(rawData, ABI_HEADER_WORDS, ABI_ITEM_WORDS)) {
        return Pair.of(false, EMPTY_BYTE_ARRAY);
      }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1057-1074)
```java
      DataWord[] words = DataWord.parseArray(rawData);
      byte[] address = words[0].toTronAddress();
      int permissionId = words[1].intValueSafe();
      byte[] data = words[2].getData();

      byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
      byte[] hash = Sha256Hash.hash(CommonParameter
          .getInstance().isECKeyCryptoEngine(), combine);

      if (VMConfig.allowTvmSelfdestructRestriction()) {
        int sigArraySize = words[words[3].intValueSafe() / WORD_SIZE].intValueSafe();
        if (sigArraySize > MAX_SIZE) {
          return Pair.of(true, DATA_FALSE);
        }
      }
      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[3].intValueSafe() / WORD_SIZE, rawData) :
          extractBytesArray(words, words[3].intValueSafe() / WORD_SIZE, rawData);
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
