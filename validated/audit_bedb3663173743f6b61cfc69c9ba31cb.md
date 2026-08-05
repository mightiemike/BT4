### Title
ValidateMultiSign duplicate-weight via ECDSA signature malleation bypasses exact-signature dedup check - (File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java)

### Finding Description
`PrecompiledContracts.ValidateMultiSign.execute` iterates over the caller-supplied `signatures[]` array and, for each signature, recovers the signer address and accumulates permission weight: [1](#0-0) 

The dedup logic is two-tiered: it first checks if `recoveredAddr` alone has already appeared in `executedSignList` (`ByteArray.matrixContains(executedSignList, recoveredAddr)`), and only if the *exact merged bytes* `merge(recoveredAddr, sign)` are also already present does it `continue` (skip) the entry. If the address was seen before but the merged bytes differ, the code merely calls `MUtil.checkCPUTime()` — a CPU-time/interrupt check used elsewhere as a DoS guard, not a security/anti-duplication guard — and then falls straight through to `TransactionCapsule.getWeight(permission, recoveredAddr)` and adds the weight again into `totalWeight`.

Because ECDSA signatures are malleable (for signature `(r, s)` under secp256k1, `(r, n-s)` with the recovery id flipped recovers to the *same* public key/address), an attacker holding one valid signature from a legitimate key can locally derive a second, byte-distinct signature that recovers to the identical `recoveredAddr`. Submitting both:
1. First iteration: `recoveredAddr` not in list → weight added, `merge(addr,sign1)` and `addr` pushed to `executedSignList`.
2. Second iteration (malleated sig): `recoveredAddr` IS in list (address-only match) → enters the `if` branch, but `merge(addr,sign2)` is a different byte string than `merge(addr,sign1)`, so the inner `matrixContains` check is false → does NOT `continue`. It calls `checkCPUTime()` and proceeds to add the same key's weight to `totalWeight` a second time.

This lets one real private-key signature count twice toward `permission.getThreshold()`, undermining the intended "N distinct keys must sign" multisig invariant enforced by TVM contracts that call `validatemultisign`/`ValidateMultiSign`.

### Impact Explanation
Any smart contract that relies on the `ValidateMultiSign` precompile (address `0x0000...1001`, exposed via Solidity `validatemultisign`) to gate privileged actions on multiple independent Active-permission keys can be tricked into approving an action with fewer real distinct signers than the permission's `keys` list requires — e.g., a 2-of-2 threshold satisfied by one attacker-held key plus one self-derived malleated signature of that same key. This is a TVM-level authorization bypass for any contract logic gated on the boolean/threshold result of `ValidateMultiSign.execute`.

### Likelihood Explanation
Exploitability requires only: (1) one valid ECDSA signature from a key listed in the target permission (attacker must already possess/obtain this — e.g., their own key present with lower weight, or a signature they were given for a legitimate purpose), and (2) trivial off-chain computation of the malleated variant (`s' = n - s`, flipped recovery id), which any attacker can compute without any privileged access. The call path is a normal `TriggerSmartContract` invoking a contract that calls the precompile — fully reachable by an unprivileged public API caller. No fee/replay/settlement guard mitigates this since the check purely concerns signature-set weight computation inside the precompile.

### Recommendation
Change the dedup key to be based on `recoveredAddr` alone (not the full signature bytes) — once an address has contributed weight, any further signature (malleated or not) recovering to that same address must be skipped entirely, not merely rate-limited via `checkCPUTime()`. I.e., replace the two-tier check with a single check: if `recoveredAddr` is already in `executedSignList`, `continue` unconditionally rather than falling through to add weight again.

### Proof of Concept
Extend `ValidateMultiSignContractTest#testDifferentCase`-style test:
1. Create an Active permission with a single key `key1` at weight 1, threshold 2 (so a lone signature is insufficient).
2. Compute `toSign` hash as in the existing test.
3. Sign with `key1` to get `sig1 = key1.sign(toSign)`.
4. Derive a malleated variant `sig2` from `sig1`: `s2 = CURVE.getN().subtract(sig1.s)`, flip the recovery id/`v` accordingly, re-encode to the 65-byte r/s/v format expected by `recoverAddrBySign`.
5. Call `ValidateMultiSign.execute` with `signatures = [sig1, sig2]`.
6. Assert that the result is `DATA_FALSE` (threshold of 2 not met) — currently it will likely return `DATA_TRUE`/pass because `totalWeight` becomes `1 + 1 = 2 >= threshold(2)` from a single distinct key, demonstrating the duplicate-weight bug. (Alternatively, instrument/expose `totalWeight` directly in a white-box unit test on the inner loop logic to assert it equals `1`, not `2`.)

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
