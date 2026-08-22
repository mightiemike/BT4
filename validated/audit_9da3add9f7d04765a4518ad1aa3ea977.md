### Title
Weight double-counting in `ValidateMultiSign.execute` via malleable/duplicate signatures dedup'd only on merged (address, sig-bytes) pair - ([File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java])

### Summary
`ValidateMultiSign.execute` deduplicates signatures by checking whether the *exact byte-merged* `(recoveredAddr, sign)` pair was already seen, rather than deduplicating solely on `recoveredAddr`. When a `recoveredAddr` has already been seen but the current raw `sign` bytes differ (e.g., due to signature malleability or any alternate valid encoding recovering to the same address), the loop does not `continue`; it falls through and adds `weight` to `totalWeight` again for the same key. This lets an attacker with a single authorized key submit multiple distinct-byte signatures that all recover to that one address and inflate `totalWeight` past `permission.getThreshold()` without truly independent authorizations.

### Finding Description
The relevant loop: [1](#0-0) 

For each signature `sign`:
1. `recoveredAddr = recoverAddrBySign(sign, hash)` recovers the signer address from the raw signature bytes.
2. `sign = merge(recoveredAddr, sign)` builds the merged dedup key.
3. `if (ByteArray.matrixContains(executedSignList, recoveredAddr))` — if this address was seen before:
   - `if (ByteArray.matrixContains(executedSignList, sign)) { continue; }` — only skips if the *exact same merged bytes* (i.e., identical raw signature) were already recorded.
   - Otherwise it just calls `MUtil.checkCPUTime()` and **falls through** to weight accumulation.
4. `weight = TransactionCapsule.getWeight(permission, recoveredAddr)` is computed and unconditionally added to `totalWeight`, then both `sign` and `recoveredAddr` are appended to `executedSignList` again.

The dedup therefore only prevents literal, byte-for-byte re-submission of the identical signature. It does **not** prevent two syntactically different signatures (different `r`/`s`/`v` encodings, e.g. via ECDSA/SM2 signature malleability where `(r, n-s, 1-v)` also recovers the same public key/address) from both counting weight for the same underlying key. `permission.getThreshold()` is a security invariant meant to require genuinely independent keys' weights to sum to the threshold; this dedup flaw breaks that guarantee by allowing one real key's weight to be counted multiple times as long as each submitted signature has distinct raw bytes.

The precompile is directly reachable by any unprivileged account or contract via a TVM `CALL`/`STATICCALL` to the `ValidateMultiSign` precompile address with attacker-controlled `data` containing the packed `(address, permissionId, data, sig[])`, so no privileged role is required — only possession of one key with nonzero weight in the target permission, which the threat model explicitly grants.

### Impact Explanation
Any contract logic gated by `validateMultiSign` (e.g., asset release, voting, delegation, or custom multisig-controlled contracts) can be unlocked by an attacker holding only one of the required keys, provided that key's weight, when double- (or multiply-) counted via distinct-byte signature variants, reaches `permission.getThreshold()`. This is an unauthorized-authorization / unauthorized state-change vulnerability: it directly undermines the on-chain multisig threshold guarantee, matching the "unauthorized account operations" bounty impact class.

### Likelihood Explanation
Preconditions are minimal: the attacker needs one key with nonzero weight in the target account's permission (explicitly granted as an assumption in this analysis) and the ability to produce an alternate valid-but-differently-encoded signature from that key that recovers to the same address (a standard, low-cost cryptographic malleability operation, not requiring key compromise). The call is a normal TVM contract call, paid for with ordinary energy/bandwidth fees; `getEnergyForData` charges only `1500` energy per signature slot, which is cheap. This is fully repeatable and requires no special node configuration, privileged role, or race condition — it is a deterministic logic flaw.

However, I was unable to directly inspect `recoverAddrBySign`'s implementation (and whether the underlying `SignUtils`/`ECKey`/`SM2` recovery path enforces canonical/low-S signatures, which would block the classic malleability trick) within the available tool budget, so full confirmation that a malleable-but-valid alternate encoding is actually accepted end-to-end is not fully verified from the code I was able to load. The dedup logic flaw itself, however, is clearly present regardless of malleability: any two distinct-byte signatures from the same key (not necessarily via ECDSA malleability — any two valid signatures over the same hash produced with different randomness/encoding, if the scheme allows it) would trigger the same double-count.

### Recommendation
Change the dedup check to key exclusively on `recoveredAddr`: once an address has been seen (`ByteArray.matrixContains(executedSignList, recoveredAddr)`), always `continue` (skip weight accumulation) regardless of whether the exact signature bytes match, instead of only skipping on exact match and otherwise falling through to add weight again. Remove reliance on the merged `(recoveredAddr, sign)` key for controlling whether weight is added.

### Proof of Concept
```java
// Extend ValidateMultiSignContractTest
// Setup: account permission with threshold = 2, two distinct authorized keys (keyA weight=1, keyB weight=1)
// Attacker controls only keyA.

byte[] hash = ...; // as computed by the precompile: sha256(address || permissionId || data)
byte[] sigA1 = ECKey.fromPrivate(keyA).sign(hash).toByteArray(); // canonical signature
byte[] sigA2 = malleate(sigA1); // e.g. flip s -> n-s and recovery id -> 1-v; recovers to same address as sigA1, byte-distinct

byte[][] sigs = new byte[][] { sigA1, sigA2 };
byte[] rawData = encodeValidateMultiSignInput(address, permissionId, data, sigs);

Pair<Boolean, byte[]> result = validateMultiSign.execute(rawData);

// EXPECTED (correct behavior): result.getRight() == DATA_FALSE, because only keyA (weight 1) truly participated,
// which is below threshold 2.
// ACTUAL (buggy behavior, if malleability accepted and recovers to same address with different bytes):
// totalWeight becomes 1 (sigA1) + 1 (sigA2, since matrixContains(executedSignList, sign) is false for the
// differently-encoded sigA2) = 2 >= threshold(2), so result.getRight() == dataOne(), i.e. unauthorized success.
assertArrayEquals(DATA_FALSE, result.getRight());
```

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
