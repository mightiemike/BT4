### Title
Weight double-counting via ECDSA signature malleability bypasses multisig threshold dedup - (File: actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java)

### Summary
`PrecompiledContracts.ValidateMultiSign.execute` only skips a signature as a duplicate when the *entire* `merge(recoveredAddr, sign)` byte sequence exactly matches a previously seen entry. If the recovered address matches an already-processed address but the raw signature bytes differ (e.g. a malleable ECDSA variant of a previously used signature), the code does not skip the entry — it falls through and adds the signer's weight to `totalWeight` again. This lets a single authorized signer's approval be counted more than once, letting `totalWeight` cross `permission.getThreshold()` without independent approvals.

### Finding Description
In `ValidateMultiSign.execute` [1](#0-0) , for each signature:

```
byte[] recoveredAddr = recoverAddrBySign(sign, hash);
sign = merge(recoveredAddr, sign);
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
```

The dedup check at lines 1092-1097 only `continue`s (skips) when the *exact byte-identical* merged signature was already processed. If `recoveredAddr` is already present but the merged `sign` bytes differ, execution proceeds past the `if` block, recomputes `weight` for that same address, and adds it to `totalWeight` a second time — the code does not enforce "one weight credit per unique address."

Standard (non-deterministic) ECDSA signing allows two distinct valid `(r, s, v)` triples for the same message and same private key (either via fresh random nonces, or trivially via the well-known malleability transform `s' = n - s`, `v' = 1 - v`, which anyone can compute from a single known valid signature without the private key). Both variants recover to the identical address via `recoverAddrBySign`, but produce different raw `sign` byte arrays, so `matrixContains(executedSignList, sign)` at line 1093 fails to match, and the loop does not `continue`.

An attacker who has observed even one valid signature from an authorized multisig key for the target hash (signatures are visible on-chain / in the ABI-encoded call data of any transaction) can derive a second, byte-distinct, still-valid signature for the same signer and same message via the malleability transform, and submit both in the `signatures[]` argument to `validatemultisign`. The precompile will credit that single signer's weight twice.

This is reachable by any unprivileged account: deploy or call a contract that invokes the `validatemultisign` precompile via `TriggerSmartContract`, supplying the crafted `signatures` array as calldata. No special privileges, admin access, or off-chain trust are required — only knowledge of one valid signature for the target permission/hash, which is inherently public once used.

### Impact Explanation
Any on-chain contract logic that gates critical actions behind `validatemultisign` (permission-weighted approvals, e.g. custom multisig vaults or governance contracts built on top of the TVM precompile) can be unlocked by an attacker who possesses only one authorized signer's signature (or observes one on-chain), rather than the number of independent signers required to meet `permission.getThreshold()`. This breaks the fundamental multisig-threshold invariant the precompile is meant to enforce and could allow unauthorized execution of contract logic that assumes true N-of-M independent approval.

### Likelihood Explanation
Preconditions are modest: the attacker needs access to at least one valid signature over the specific `(address, permissionId, data)` hash from an authorized key (which becomes attacker-visible the moment it is used once, e.g. in a legitimate transaction, mempool, or event log), and a contract that calls `validatemultisign` with attacker-influenceable signature array contents (a common integration pattern, since the signatures are typically passed through from external call data). Deriving the malleable counterpart signature is a pure, deterministic, well-documented mathematical transform requiring no private key access. This is fully repeatable and does not depend on race conditions or timing.

### Recommendation
Change the dedup logic in `ValidateMultiSign.execute` to key exclusively on `recoveredAddr`, not on the combined `sign` bytes: once an address has contributed weight, any further signature (identical or malleable variant) recovering to that same address must be skipped outright, e.g.:
```
if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
  continue;
}
...
executedSignList.add(recoveredAddr);
```
removing the byte-exact `sign` comparison, so weight is credited at most once per unique recovered address regardless of malleable signature variants.

### Proof of Concept
Extend `ValidateMultiSignContractTest` with a differential test:
1. Create an account with an `Active` permission with threshold `2`, two keys `key1`, `key2`, weight `1` each.
2. Compute `toSign` as in existing tests.
3. Generate one valid signature `sigA = key1.sign(toSign)`.
4. Derive the malleable counterpart `sigB` by transforming `sigA`'s `(r, s, v)` to `(r, n - s, 1 - v)` (secp256k1 order `n`), re-encoding it in the same 65-byte `r||s||v` layout used by the precompile.
5. Call `validateMultiSign(address, permissionId, data, [sigA, sigB])` — only `key1`'s weight (1) should legitimately count once, so the expected/correct result is `DATA_FALSE` (total legitimate weight 1 < threshold 2).
6. Assert: current code returns `DataWord.ONE().getData()` (threshold satisfied) — proving weight was double-counted from a single signer — whereas a reference implementation that dedups strictly by `recoveredAddr` returns `DATA_FALSE`. [1](#0-0) [2](#0-1)

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

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L117-132)
```java
    List<Object> signs = new ArrayList<>();
    signs.add(Hex.toHexString(key1.sign(toSign).toByteArray()));
    //add Repetitive
    signs.add(Hex.toHexString(key1.sign(toSign).toByteArray()));
    signs.add(Hex.toHexString(key2.sign(toSign).toByteArray()));

    Assert.assertArrayEquals(
        validateMultiSign(StringUtil.encode58Check(key.getAddress()), permissionId, data, signs)
            .getValue(), DataWord.ONE().getData());

    //after optimized
    VMConfig.initAllowTvmSelfdestructRestriction(1);
    Assert.assertArrayEquals(
        validateMultiSign(StringUtil.encode58Check(key.getAddress()), permissionId, data, signs)
            .getValue(), DataWord.ONE().getData());
    VMConfig.initAllowTvmSelfdestructRestriction(0);
```
